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
        "explain": os.environ.get("AIT_NVIDIA_EXPLAIN_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct"),
        "suggest": os.environ.get("AIT_NVIDIA_SUGGEST_MODEL", "nv-mistralai/mistral-nemo-12b-instruct"),
    },
}

NVIDIA_BASE_URL = os.environ.get("AIT_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

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


def _provider() -> tuple[str, str]:
    """Returns (provider, api_key)."""
    forced = os.environ.get("AIT_PROVIDER", "").lower()
    if forced:
        if forced not in _KEY_SOURCES:
            raise LLMError(f"AIT_PROVIDER must be 'anthropic' or 'nvidia', not '{forced}'")
        if key := _api_key(forced):
            return forced, key
        raise MissingKey(f"No {forced} API key. Run `ait key {forced}`.")
    for provider in ("anthropic", "nvidia"):
        if key := _api_key(provider):
            return provider, key
    raise MissingKey("No API key. Run `ait key anthropic` or `ait key nvidia`.")


def active_provider() -> str:
    """Provider name and model for `ait status`, or why none is usable."""
    try:
        provider, _ = _provider()
    except (MissingKey, LLMError) as e:
        return f"no provider: {e}"
    return f"{provider}, explain model {MODELS[provider]['explain']}"


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


async def _nvidia(key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    client = _clients.get("nvidia") or _clients.setdefault(
        "nvidia", httpx.AsyncClient(base_url=NVIDIA_BASE_URL, timeout=60))
    try:
        resp = await client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "max_tokens": max_tokens,
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
        raise LLMError(f"NVIDIA API error {resp.status_code}: {str(detail)[:300]}")
    return resp.json()["choices"][0]["message"].get("content") or ""


async def complete(task: str, system: str, user: str, max_tokens: int = 600) -> str:
    """Run one prompt for `task` ("explain" or "suggest") on the configured provider."""
    provider, key = _provider()
    model = MODELS[provider][task]
    call = _anthropic if provider == "anthropic" else _nvidia
    text = await call(key, model, system, user, max_tokens)
    return _THINK.sub("", text).strip()
