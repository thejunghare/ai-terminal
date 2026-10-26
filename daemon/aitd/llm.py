"""LLM providers: Anthropic (Claude) or NVIDIA (build.nvidia.com, OpenAI-compatible).

Which one is used:
  1. AIT_PROVIDER=anthropic|nvidia, if set
  2. otherwise Anthropic if it has a key, else NVIDIA if it has a key
Keys come from the environment (ANTHROPIC_API_KEY / NVIDIA_API_KEY) or macOS
Keychain (services "ai-terminal" / "ai-terminal-nvidia", set with `ait key`).
"""
import os
import re
import subprocess

import anthropic
import httpx

# Model per task, per provider. Each can be overridden with an env var.
MODELS = {
    "anthropic": {
        "explain": os.environ.get("AIT_EXPLAIN_MODEL", "claude-sonnet-5-5"),
        "suggest": os.environ.get("AIT_SUGGEST_MODEL", "claude-haiku-5-5"),
    },
    "nvidia": {
        "explain": os.environ.get("AIT_NVIDIA_EXPLAIN_MODEL", "deepseek-ai/deepseek-v4.1-flash"),
        "suggest": os.environ.get("AIT_NVIDIA_SUGGEST_MODEL", "deepseek-ai/deepseek-v4.1-flash"),
    },
}

NVIDIA_BASE_URL = os.environ.get("AIT_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

# build.nvidia.com lists models that a given account can't call (HTTP 404
# "Function ... Not found for account"). When that happens we try these in order.
NVIDIA_FALLBACK_MODELS = [
    "deepseek-ai/deepseek-v4.1-flash",
    "openai/gpt-oss-20b",
    "z-ai/glm-5.3-flash",
    "nvidia/nemotron-3-super-120b-a12b",
    "nv-mistralai/mistral-nemo-12b-instruct",
]
# Reasoning models spend tokens thinking before they answer, so allow more.
NVIDIA_MAX_TOKENS = 4096

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
    pass


async def _nvidia_call(key: str, model: str, system: str, user: str) -> str:
    client = _clients.get("nvidia") or _clients.setdefault(
        "nvidia", httpx.AsyncClient(base_url=NVIDIA_BASE_URL, timeout=90))
    try:
        resp = await client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "max_tokens": NVIDIA_MAX_TOKENS,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
    except httpx.HTTPError as e:
        raise LLMError(f"NVIDIA API unreachable: {e}") from e
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail") or resp.json().get("error") or resp.text
        except ValueError:
            detail = resp.text
        msg = f"NVIDIA API error {resp.status_code} ({model}): {str(detail)[:200]}"
        if resp.status_code == 404:
            raise _ModelUnavailable(msg)
        raise LLMError(msg)
    content = resp.json()["choices"][0]["message"].get("content") or ""
    if not content.strip():
        raise LLMError(f"NVIDIA model {model} returned an empty answer.")
    return content


async def _nvidia(key: str, task: str, system: str, user: str) -> str:
    preferred = _resolved_nvidia.get(task, MODELS["nvidia"][task])
    candidates = [preferred] + [m for m in NVIDIA_FALLBACK_MODELS if m != preferred]
    tried = []
    for model in candidates:
        try:
            text = await _nvidia_call(key, model, system, user)
        except _ModelUnavailable:
            tried.append(model)
            continue
        _resolved_nvidia[task] = model
        return text
    raise LLMError(
        "None of these NVIDIA models are available to your account: "
        + ", ".join(tried)
        + ". Pick one from build.nvidia.com and set AIT_NVIDIA_EXPLAIN_MODEL."
    )


async def complete(task: str, system: str, user: str, max_tokens: int = 600) -> str:
    """Run one prompt for `task` ("explain" or "suggest") on the configured provider."""
    provider, key = _provider()
    if provider == "anthropic":
        text = await _anthropic(key, MODELS["anthropic"][task], system, user, max_tokens)
    else:
        text = await _nvidia(key, task, system, user)
    return _THINK.sub("", text).strip()
