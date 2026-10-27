"""LLM providers: Anthropic (Claude) or NVIDIA (build.nvidia.com, OpenAI-compatible).

Which one is used:
  1. AIT_PROVIDER=anthropic|nvidia, if set
  2. otherwise Anthropic if it has a key, else NVIDIA if it has a key
Keys come from the environment (ANTHROPIC_API_KEY / NVIDIA_API_KEY) or macOS
Keychain (services "ai-terminal" / "ai-terminal-nvidia", set with `ait key`).
"""
import asyncio
import logging
import os
import re
import subprocess
import time

import anthropic
import httpx

log = logging.getLogger("aitd")

# Model per task, per provider. Each can be overridden with an env var.
MODELS = {
    "anthropic": {
        "explain": os.environ.get("AIT_EXPLAIN_MODEL", "claude-sonnet-5-5"),
        "suggest": os.environ.get("AIT_SUGGEST_MODEL", "claude-haiku-5-5"),
    },
    "nvidia": {
        "explain": os.environ.get("AIT_NVIDIA_EXPLAIN_MODEL", "openai/gpt-oss-20b"),
        "suggest": os.environ.get("AIT_NVIDIA_SUGGEST_MODEL", "openai/gpt-oss-20b"),
    },
}

NVIDIA_BASE_URL = os.environ.get("AIT_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

# build.nvidia.com lists models that a given account can't call (HTTP 404
# "Function ... Not found for account"). When that happens we try these in order.
NVIDIA_FALLBACK_MODELS = [
    "openai/gpt-oss-20b",
    "deepseek-ai/deepseek-v4.1-flash",
    "z-ai/glm-5.3-flash",
    "nvidia/nemotron-3-super-120b-a12b",
    "nv-mistralai/mistral-nemo-12b-instruct",
]
# Reasoning models spend tokens thinking before they answer, so allow more.
NVIDIA_MAX_TOKENS = 4096
# Free-tier models can queue for a long time. Give each model this long before
# moving on, and give up entirely before the shell's 60s wait runs out.
NVIDIA_ATTEMPT_TIMEOUT = float(os.environ.get("AIT_NVIDIA_TIMEOUT", "20"))
NVIDIA_TOTAL_BUDGET = 50.0

# Model actually answering per task, once a fallback has been needed.
_resolved_nvidia: dict[str, str] = {}

_KEY_SOURCES = {
    "anthropic": ("ANTHROPIC_API_KEY", "ai-terminal"),
    "nvidia": ("NVIDIA_API_KEY", "ai-terminal-nvidia"),
}

# Reasoning models may prepend their chain of thought; the terminal only wants the answer.
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


class MissingKey(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


def _api_key(provider: str) -> str | None:
    env_var, service = _KEY_SOURCES[provider]
    if key := os.environ.get(env_var):
        return key
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _available_keys() -> dict[str, str]:
    """Keys found, keyed by the provider they belong to.

    The key's own prefix wins over the slot it was stored in, so an nvapi- key
    saved under the Anthropic slot is still sent to NVIDIA.
    """
    found: dict[str, str] = {}
    for slot in ("anthropic", "nvidia"):
        if key := _api_key(slot):
            found.setdefault("nvidia" if key.startswith("nvapi-") else slot, key)
    return found


def _provider() -> tuple[str, str]:
    """Returns (provider, api_key)."""
    keys = _available_keys()
    forced = os.environ.get("AIT_PROVIDER", "").lower()
    if forced:
        if forced not in _KEY_SOURCES:
            raise LLMError(f"AIT_PROVIDER must be 'anthropic' or 'nvidia', not '{forced}'")
        if forced in keys:
            return forced, keys[forced]
        raise MissingKey(f"No {forced} API key. Run `ait key`.")
    for provider in ("anthropic", "nvidia"):
        if provider in keys:
            return provider, keys[provider]
    raise MissingKey("No API key. Run `ait key` and paste an Anthropic or NVIDIA key.")


def active_provider() -> str:
    """Provider name and model for `ait status`, or why none is usable."""
    try:
        provider, _ = _provider()
    except (MissingKey, LLMError) as e:
        return f"no provider: {e}"
    model = _resolved_nvidia.get("explain") if provider == "nvidia" else None
    return f"{provider}, explain model {model or MODELS[provider]['explain']}"


_clients: dict[str, object] = {}


async def _anthropic(key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    client = _clients.get("anthropic") or _clients.setdefault(
        "anthropic", anthropic.AsyncAnthropic(api_key=key))
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIError as e:
        raise LLMError(f"Claude API error: {getattr(e, 'message', e)}") from e
    return "".join(b.text for b in resp.content if b.type == "text")


class _ModelUnavailable(LLMError):
    """This model can't answer for this account right now; try another one."""


# Models that rejected the reasoning_effort parameter; we stop sending it to them.
_no_reasoning_effort: set[str] = set()


async def _nvidia_post(key: str, model: str, messages: list, max_tokens: int,
                       timeout: float) -> httpx.Response:
    client = _clients.get("nvidia") or _clients.setdefault(
        "nvidia", httpx.AsyncClient(base_url=NVIDIA_BASE_URL))
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0.2, "messages": messages}
    # gpt-oss thinks before answering; "low" makes it answer several times faster.
    low_effort = "gpt-oss" in model and model not in _no_reasoning_effort
    if low_effort:
        body["reasoning_effort"] = "low"
    try:
        resp = await client.post("/chat/completions", json=body, timeout=timeout,
                                 headers={"Authorization": f"Bearer {key}"})
        if low_effort and resp.status_code in (400, 422):
            _no_reasoning_effort.add(model)
            del body["reasoning_effort"]
            resp = await client.post("/chat/completions", json=body, timeout=timeout,
                                     headers={"Authorization": f"Bearer {key}"})
        return resp
    except httpx.TimeoutException as e:
        raise _ModelUnavailable(f"{model} did not answer within {timeout:.0f}s") from e
    except httpx.HTTPError as e:
        raise LLMError(f"NVIDIA API unreachable: {e}") from e


def _nvidia_error(resp: httpx.Response, model: str) -> LLMError:
    try:
        detail = resp.json().get("detail") or resp.json().get("error") or resp.text
    except ValueError:
        detail = resp.text
    msg = f"NVIDIA API error {resp.status_code} ({model}): {str(detail)[:200]}"
    return _ModelUnavailable(msg) if resp.status_code == 404 else LLMError(msg)


async def _nvidia_call(key: str, model: str, system: str, user: str, timeout: float,
                       max_tokens: int) -> str:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    resp = await _nvidia_post(key, model, messages, max_tokens, timeout)
    if resp.status_code != 200:
        raise _nvidia_error(resp, model)
    content = resp.json()["choices"][0]["message"].get("content") or ""
    if not content.strip():
        raise _ModelUnavailable(f"{model} returned an empty answer")
    return content


async def _nvidia(key: str, task: str, system: str, user: str) -> tuple[str, str]:
    preferred = _resolved_nvidia.get(task) or _resolved_nvidia.get("explain") or MODELS["nvidia"][task]
    if task == "suggest":
        # Suggestions are best-effort and frequent: one model, short wait, no cascade.
        candidates, budget, max_tokens = [preferred], 15.0, 512
    else:
        candidates = [preferred] + [m for m in NVIDIA_FALLBACK_MODELS if m != preferred]
        budget, max_tokens = NVIDIA_TOTAL_BUDGET, NVIDIA_MAX_TOKENS
    deadline = time.monotonic() + budget
    problems = []
    for model in candidates:
        remaining = deadline - time.monotonic()
        if remaining < 3:
            break
        try:
            text = await _nvidia_call(key, model, system, user,
                                      min(NVIDIA_ATTEMPT_TIMEOUT, remaining), max_tokens)
        except _ModelUnavailable as e:
            log.warning("%s", e)
            problems.append(str(e))
            continue
        _resolved_nvidia[task] = model
        return text, model
    if task == "suggest":
        raise LLMError("; ".join(problems) or "no suggestion")
    raise LLMError(
        "No NVIDIA model answered in time. Run `ait models` to see which work for "
        "your key. Tried: " + "; ".join(problems)
    )


async def probe_nvidia_models() -> str:
    """Try every candidate model at once with a tiny prompt and report the result."""
    keys = _available_keys()
    if "nvidia" not in keys:
        raise MissingKey("No NVIDIA key saved. Run `ait key`.")
    models = list(dict.fromkeys([MODELS["nvidia"]["explain"], *NVIDIA_FALLBACK_MODELS]))
    messages = [{"role": "user", "content": "Reply with the word ok."}]

    async def probe(model: str) -> tuple[str, str]:
        start = time.monotonic()
        try:
            resp = await _nvidia_post(keys["nvidia"], model, messages, 32, NVIDIA_ATTEMPT_TIMEOUT)
        except LLMError as e:
            return model, f"✗ {e}"
        took = time.monotonic() - start
        if resp.status_code == 200:
            return model, f"✓ works ({took:.1f}s)"
        return model, f"✗ HTTP {resp.status_code}"

    results = await asyncio.gather(*(probe(m) for m in models))
    width = max(len(m) for m in models)
    lines = [f"  {m.ljust(width)}  {r}" for m, r in results]
    return ("NVIDIA models for your key:\n" + "\n".join(lines)
            + "\n\nTo use one: export AIT_NVIDIA_EXPLAIN_MODEL=<model> in ~/.zshrc, then ait restart")


async def complete(task: str, system: str, user: str, max_tokens: int = 600) -> tuple[str, str]:
    """Run one prompt for `task` ("explain" or "suggest"); returns (text, model used)."""
    provider, key = _provider()
    if provider == "anthropic":
        model = MODELS["anthropic"][task]
        text = await _anthropic(key, model, system, user, max_tokens)
    else:
        text, model = await _nvidia(key, task, system, user)
    return _THINK.sub("", text).strip(), model
