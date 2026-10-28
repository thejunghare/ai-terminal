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

from . import store

log = logging.getLogger("aitd")

# Model per task, per provider. Each can be overridden with an env var.
MODELS = {
    "anthropic": {
        "explain": os.environ.get("AIT_EXPLAIN_MODEL", "claude-sonnet-5-5"),
        "suggest": os.environ.get("AIT_SUGGEST_MODEL", "claude-haiku-5-5"),
        "generate": os.environ.get("AIT_EXPLAIN_MODEL", "claude-sonnet-5-5"),
        "why": os.environ.get("AIT_EXPLAIN_MODEL", "claude-sonnet-5-5"),
    },
    "nvidia": {
        "explain": os.environ.get("AIT_NVIDIA_EXPLAIN_MODEL", "openai/gpt-oss-20b"),
        "suggest": os.environ.get("AIT_NVIDIA_SUGGEST_MODEL", "openai/gpt-oss-20b"),
        "generate": os.environ.get("AIT_NVIDIA_EXPLAIN_MODEL", "openai/gpt-oss-20b"),
        "why": os.environ.get("AIT_NVIDIA_EXPLAIN_MODEL", "openai/gpt-oss-20b"),
    },
}

NVIDIA_BASE_URL = os.environ.get("AIT_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

# build.nvidia.com lists models that a given account can't call (HTTP 404
# "Function ... Not found for account"), and the catalog changes often. So the live
# catalog (GET /v1/models) decides what we try; these are only preferred if listed.
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
# The model that last answered, kept across daemon restarts so we don't hunt again.
_nvidia_saved = store.DiskCache("nvidia", max_items=10)
_nvidia_models: list[str] = []          # the account's catalog, fetched once
# Catalog entries that aren't chat models: embeddings, rerankers, guards, vision, speech...
_NOT_CHAT = re.compile(
    r"embed|rerank|retriev|guard|safety|reward|vision|vlm|-vl\b|vila|neva|clip|ocr|parse|"
    r"asr|tts|riva|whisper|canary|parakeet|audio|speech|detect|pii|deplot|kosmos|paligemma|"
    r"fuyu|cosmos|sdxl|flux|diffusion|bge|translate", re.IGNORECASE)
# Name hints for small, fast models, best first; these answer quickest on the free tier.
_FAST_HINTS = ["flash", "nano", "mini", "8b", "7b", "9b", "12b", "small", "20b", "instruct"]

_KEY_SOURCES = {
    "anthropic": ("ANTHROPIC_API_KEY", "ai-terminal"),
    "nvidia": ("NVIDIA_API_KEY", "ai-terminal-nvidia"),
}

# Reasoning models may prepend their chain of thought; the terminal only wants the answer.
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
# Control characters other than newline and tab. Model output is printed to the terminal,
# and text the model read (e.g. screen output for `why`) must not smuggle in escape
# sequences that retitle the window, write the clipboard or fake a prompt.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


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
    model = _nvidia_preferred("explain") if provider == "nvidia" else MODELS[provider]["explain"]
    return f"{provider}, explain model {model}"


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


def _nvidia_client() -> httpx.AsyncClient:
    return _clients.get("nvidia") or _clients.setdefault(
        "nvidia", httpx.AsyncClient(base_url=NVIDIA_BASE_URL))


async def _nvidia_catalog(key: str) -> list[str]:
    """Model ids this key can see, from GET /v1/models; [] if the list can't be fetched."""
    if _nvidia_models:
        return _nvidia_models
    try:
        resp = await _nvidia_client().get("/models", timeout=10,
                                          headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        log.warning("NVIDIA model list unavailable: %s", e)
        return []
    if resp.status_code in (401, 403):
        raise LLMError(f"NVIDIA rejected your key (HTTP {resp.status_code}). "
                       "Create a new one at build.nvidia.com, then run `ait key`.")
    if resp.status_code != 200:
        return []
    try:
        ids = [m["id"] for m in resp.json().get("data", []) if m.get("id")]
    except (ValueError, AttributeError, TypeError):
        return []
    _nvidia_models[:] = ids
    return ids


def _rank_chat_models(ids: list[str]) -> list[str]:
    """Chat models from a catalog, small and fast ones first."""
    def speed(model: str) -> int:
        name = model.lower()
        return next((i for i, hint in enumerate(_FAST_HINTS) if hint in name), len(_FAST_HINTS))
    return sorted((m for m in ids if not _NOT_CHAT.search(m)), key=speed)


def _nvidia_preferred(task: str) -> str:
    """An explicit env setting wins, then what answered last, then the default."""
    env = "AIT_NVIDIA_SUGGEST_MODEL" if task == "suggest" else "AIT_NVIDIA_EXPLAIN_MODEL"
    return (os.environ.get(env) or _resolved_nvidia.get(task) or _resolved_nvidia.get("explain")
            or _nvidia_saved.get("model") or MODELS["nvidia"][task])


async def _nvidia_candidates(key: str, preferred: str) -> list[str]:
    catalog = await _nvidia_catalog(key)
    if not catalog:                     # couldn't list: fall back to the built-in guesses
        return list(dict.fromkeys([preferred, *NVIDIA_FALLBACK_MODELS]))
    listed = set(catalog)
    known = [m for m in NVIDIA_FALLBACK_MODELS if m in listed]
    return list(dict.fromkeys([preferred, *known, *_rank_chat_models(catalog)]))


def _remember_nvidia(model: str) -> None:
    _resolved_nvidia["explain"] = _resolved_nvidia["generate"] = _resolved_nvidia["why"] = model
    if _nvidia_saved.get("model") != model:
        _nvidia_saved.put("model", model)


async def _nvidia_post(key: str, model: str, messages: list, max_tokens: int,
                       timeout: float) -> httpx.Response:
    client = _nvidia_client()
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
    preferred = _nvidia_preferred(task)
    if task == "suggest":
        # Suggestions are best-effort and frequent: one model, short wait, no cascade.
        candidates, budget, max_tokens = [preferred], 15.0, 512
    else:
        candidates = await _nvidia_candidates(key, preferred)
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
        if task == "suggest":
            _resolved_nvidia["suggest"] = model
        else:
            _remember_nvidia(model)
        return text, model
    if task == "suggest":
        raise LLMError("; ".join(problems) or "no suggestion")
    raise LLMError(
        "No NVIDIA model answered in time; the free tier may be busy. Run `ait models` "
        "to find one that works, or use an Anthropic key. Tried: " + "; ".join(problems[:3])
    )


PROBE_LIMIT = 8            # models `ait models` tries
PROBE_PARALLEL = 4         # at once: enough to be quick, few enough not to trip rate limits


async def probe_nvidia_models() -> str:
    """Try likely models from the account's live catalog and keep the fastest that works."""
    keys = _available_keys()
    if "nvidia" not in keys:
        raise MissingKey("No NVIDIA key saved. Run `ait key`.")
    key = keys["nvidia"]
    catalog = await _nvidia_catalog(key)
    models = (await _nvidia_candidates(key, _nvidia_preferred("explain")))[:PROBE_LIMIT]
    messages = [{"role": "user", "content": "Reply with the word ok."}]
    gate = asyncio.Semaphore(PROBE_PARALLEL)

    async def probe(model: str) -> tuple[str, float | None, str]:
        async with gate:
            start = time.monotonic()
            try:
                resp = await _nvidia_post(key, model, messages, 64, NVIDIA_ATTEMPT_TIMEOUT)
            except LLMError as e:
                return model, None, f"✗ {e}".replace(f"{model} ", "")
            took = time.monotonic() - start
            if resp.status_code == 200:
                return model, took, f"✓ works ({took:.1f}s)"
            if resp.status_code == 404:
                return model, None, "✗ not available for your account"
            if resp.status_code == 429:
                return model, None, "✗ rate limited, try again in a minute"
            return model, None, f"✗ HTTP {resp.status_code}"

    results = await asyncio.gather(*(probe(m) for m in models))
    width = max(len(m) for m in models)
    lines = [f"  {m.ljust(width)}  {r}" for m, _, r in results]
    head = (f"Your key can use {len(_rank_chat_models(catalog))} chat models. Tried the likeliest:"
            if catalog else "Couldn't fetch NVIDIA's model list; tried the built-in ones:")
    working = sorted((took, m) for m, took, _ in results if took is not None)
    if working:
        best = working[0][1]
        _remember_nvidia(best)
        tail = f"Using {best} from now on (fastest that answered)."
        if os.environ.get("AIT_NVIDIA_EXPLAIN_MODEL"):
            tail += "\nNote: AIT_NVIDIA_EXPLAIN_MODEL is set and takes priority. Remove it to use this one."
    else:
        tail = ("None answered. NVIDIA's free tier is often busy: try `ait models` again in a few "
                "minutes, or use an Anthropic key (`ait key`) for reliable answers.")
    return f"{head}\n" + "\n".join(lines) + f"\n\n{tail}"


async def complete(task: str, system: str, user: str, max_tokens: int = 600) -> tuple[str, str]:
    """Run one prompt for `task` (a key of MODELS); returns (text, model used)."""
    provider, key = _provider()
    if provider == "anthropic":
        model = MODELS["anthropic"][task]
        text = await _anthropic(key, model, system, user, max_tokens)
    else:
        text, model = await _nvidia(key, task, system, user)
    return _CONTROL.sub("", _THINK.sub("", text)).strip(), model
