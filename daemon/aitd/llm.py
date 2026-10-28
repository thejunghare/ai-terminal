"""LLM providers: Anthropic (Claude), and three OpenAI-compatible ones:
NVIDIA (build.nvidia.com), OpenRouter (openrouter.ai) and Ollama (local models).

Which one is used:
  1. AIT_PROVIDER=anthropic|nvidia|openrouter|ollama, if set
  2. otherwise the one chosen with `ait use <provider>`
  3. otherwise the first available of: Anthropic key, OpenRouter key, NVIDIA key,
     a running Ollama
Keys come from the environment (ANTHROPIC_API_KEY / NVIDIA_API_KEY / OPENROUTER_API_KEY)
or the macOS Keychain (services "ai-terminal", "ai-terminal-nvidia",
"ai-terminal-openrouter", set with `ait key`). Ollama needs no key.
"""
import asyncio
import logging
import os
import re
import socket
import subprocess
import time
from urllib.parse import urlparse

import anthropic
import httpx

from . import store

log = logging.getLogger("aitd")

ANTHROPIC_MODELS = {
    "explain": os.environ.get("AIT_EXPLAIN_MODEL", "claude-sonnet-5-5"),
    "suggest": os.environ.get("AIT_SUGGEST_MODEL", "claude-haiku-5-5"),
    "generate": os.environ.get("AIT_EXPLAIN_MODEL", "claude-sonnet-5-5"),
    "why": os.environ.get("AIT_EXPLAIN_MODEL", "claude-sonnet-5-5"),
}

# Catalog entries that aren't chat models: embeddings, rerankers, guards, vision, speech...
_NOT_CHAT = re.compile(
    r"embed|rerank|retriev|guard|safety|reward|vision|vlm|-vl\b|vila|neva|clip|ocr|parse|"
    r"asr|tts|riva|whisper|canary|parakeet|audio|speech|detect|pii|deplot|kosmos|paligemma|"
    r"fuyu|cosmos|sdxl|flux|diffusion|bge|translate|image", re.IGNORECASE)
# Name hints for small, fast models, best first; these answer quickest.
_FAST_HINTS = ["nano", "mini", "flash", "1b", "3b", "4b", "8b", "7b", "9b", "12b", "small",
               "20b", "instruct"]
# A model that was busy is skipped for a while; one missing from the account for longer.
COOLDOWN_BUSY, COOLDOWN_MISSING = 120.0, 3600.0
# Reasoning models may prepend their chain of thought; the terminal only wants the answer.
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
# Control characters other than newline and tab. Model output is printed to the terminal,
# and text the model read (e.g. screen output for `why`) must not smuggle in escape
# sequences that retitle the window, write the clipboard or fake a prompt.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_settings = store.DiskCache("settings", max_items=20)   # `ait use` choice
_clients: dict[str, object] = {}


class MissingKey(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class _ModelUnavailable(LLMError):
    """This model can't answer right now; try another one."""

    def __init__(self, msg: str, missing: bool = False):
        super().__init__(msg)
        self.missing = missing          # not on this account at all, rather than busy


# --- OpenAI-compatible providers ------------------------------------------------

class Compat:
    """A provider speaking the OpenAI chat API, with a live model catalog and fallback
    to other models when one is missing, busy or rate limited."""

    def __init__(self, name: str, label: str, base_url: str, model_env: str,
                 suggest_env: str, default: str | None, fallbacks: list[str],
                 attempt_timeout: float, budget: float, key_hint: str,
                 headers: dict | None = None, free_suffix: str | None = None):
        self.name, self.label, self.base_url = name, label, base_url
        self.model_env, self.suggest_env = model_env, suggest_env
        self.default, self.fallbacks = default, fallbacks
        self.attempt_timeout, self.budget = attempt_timeout, budget
        self.key_hint, self.extra_headers, self.free_suffix = key_hint, headers or {}, free_suffix
        self.resolved: dict[str, str] = {}      # model answering per task, this run
        self.saved = store.DiskCache(name, max_items=10)   # survives restarts
        self.models: list[str] = []             # the catalog, fetched once
        self.cooldown: dict[str, float] = {}    # model -> time.monotonic() it may be retried
        self.no_reasoning_effort: set[str] = set()

    # -- plumbing --
    def client(self) -> httpx.AsyncClient:
        return _clients.get(self.name) or _clients.setdefault(
            self.name, httpx.AsyncClient(base_url=self.base_url))

    def headers(self, key: str) -> dict:
        return {**self.extra_headers, **({"Authorization": f"Bearer {key}"} if key else {})}

    def unreachable(self, e: Exception) -> LLMError:
        return LLMError(f"{self.label} API unreachable: {e}")

    # -- choosing models --
    async def catalog(self, key: str) -> list[str]:
        """Model ids this key can use, from GET /models; [] if the list can't be fetched."""
        if self.models:
            return self.models
        try:
            resp = await self.client().get("/models", timeout=10, headers=self.headers(key))
        except httpx.HTTPError as e:
            log.warning("%s model list unavailable: %s", self.label, e)
            if isinstance(e, httpx.ConnectError):
                raise self.unreachable(e) from e
            return []
        if resp.status_code in (401, 403):
            raise LLMError(f"{self.label} rejected your key (HTTP {resp.status_code}). {self.key_hint}")
        if resp.status_code != 200:
            return []
        try:
            ids = [m["id"] for m in resp.json().get("data", []) if m.get("id")]
        except (ValueError, AttributeError, TypeError):
            return []
        self.models[:] = ids
        return ids

    def rank(self, ids: list[str]) -> list[str]:
        """Chat models from a catalog: free ones first (OpenRouter), then small and fast."""
        def order(model: str) -> tuple[int, int]:
            name = model.lower()
            paid = 0 if not self.free_suffix or name.endswith(self.free_suffix) else 1
            speed = next((i for i, h in enumerate(_FAST_HINTS) if h in name), len(_FAST_HINTS))
            return paid, speed
        return sorted((m for m in ids if not _NOT_CHAT.search(m)), key=order)

    def preferred(self, task: str) -> str | None:
        """An explicit env setting wins, then what answered last, then the default."""
        env = self.suggest_env if task == "suggest" else self.model_env
        return (os.environ.get(env) or self.resolved.get(task) or self.resolved.get("explain")
                or self.saved.get("model") or self.default)

    async def candidates(self, key: str, preferred: str | None, skip_cooling: bool = True) -> list[str]:
        """Models to try in order: the preferred one, ones that worked before, then the catalog."""
        catalog = await self.catalog(key)
        worked = [m for m in (self.saved.get("working") or []) if not catalog or m in catalog]
        rest = self.rank(catalog) if catalog else self.fallbacks
        models = [m for m in dict.fromkeys([preferred, *worked, *rest]) if m]
        if skip_cooling:
            now = time.monotonic()
            models = [m for m in models if self.cooldown.get(m, 0) <= now] or models
        return models

    def remember(self, model: str, others: list[str] | None = None) -> None:
        """Use `model` from now on, and keep a short list of models known to work."""
        self.resolved["explain"] = self.resolved["generate"] = self.resolved["why"] = model
        working = list(dict.fromkeys([model, *(others or []), *(self.saved.get("working") or [])]))[:6]
        if self.saved.get("model") != model or self.saved.get("working") != working:
            self.saved.put("model", model)
            self.saved.put("working", working)

    # -- talking to it --
    async def post(self, key: str, model: str, messages: list, max_tokens: int,
                   timeout: float) -> httpx.Response:
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0.2, "messages": messages}
        # gpt-oss thinks before answering; "low" makes it answer several times faster.
        low_effort = "gpt-oss" in model and model not in self.no_reasoning_effort
        if low_effort:
            body["reasoning_effort"] = "low"
        try:
            resp = await self.client().post("/chat/completions", json=body, timeout=timeout,
                                            headers=self.headers(key))
            if low_effort and resp.status_code in (400, 422):
                self.no_reasoning_effort.add(model)
                del body["reasoning_effort"]
                resp = await self.client().post("/chat/completions", json=body, timeout=timeout,
                                                headers=self.headers(key))
            return resp
        except httpx.TimeoutException as e:
            raise _ModelUnavailable(f"{model} did not answer within {timeout:.0f}s") from e
        except httpx.HTTPError as e:
            raise self.unreachable(e) from e

    def error(self, code: int, detail: object, model: str) -> LLMError:
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
        detail = str(detail)[:120]
        log.warning("%s %s from %s: %s", self.label, code, model, detail)
        if code == 404:
            return _ModelUnavailable(f"{model}: not available for your account", missing=True)
        if code == 402:                       # OpenRouter: paid model, no credits
            return _ModelUnavailable(f"{model}: needs credits", missing=True)
        if code == 429:
            return _ModelUnavailable(f"{model}: rate limited")
        if code in (408, 500, 502, 503, 504):  # e.g. NVIDIA 503 "ResourceExhausted"
            return _ModelUnavailable(f"{model}: busy right now")
        if code in (401, 403):
            return LLMError(f"{self.label} rejected your key. {self.key_hint}")
        return LLMError(f"{self.label} API error {code} ({model}): {detail}")

    def response_error(self, resp: httpx.Response, model: str) -> LLMError:
        try:
            body = resp.json()
            detail = body.get("detail") or body.get("error") or body.get("message") or resp.text
        except (ValueError, AttributeError):
            detail = resp.text
        return self.error(resp.status_code, detail, model)

    async def call(self, key: str, model: str, system: str, user: str, timeout: float,
                   max_tokens: int) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        resp = await self.post(key, model, messages, max_tokens, timeout)
        if resp.status_code != 200:
            raise self.response_error(resp, model)
        data = resp.json()
        if "choices" not in data:            # OpenRouter reports some failures inside a 200
            err = data.get("error") or {}
            raise self.error(int(err.get("code") or 503) if isinstance(err, dict) else 503, err, model)
        content = data["choices"][0]["message"].get("content") or ""
        if not content.strip():
            raise _ModelUnavailable(f"{model} returned an empty answer")
        return content

    async def complete(self, key: str, task: str, system: str, user: str) -> tuple[str, str]:
        preferred = self.preferred(task)
        if task == "suggest":
            # Suggestions are best-effort and frequent: one model, short wait, no cascade.
            candidates = [preferred] if preferred else (await self.candidates(key, None))[:1]
            budget, max_tokens = 15.0, 512
        else:
            candidates = await self.candidates(key, preferred)
            budget, max_tokens = self.budget, 4096   # reasoning models think before answering
        if not candidates:
            raise LLMError(self.no_models_message())
        deadline = time.monotonic() + budget
        problems = []
        for model in candidates:
            remaining = deadline - time.monotonic()
            if remaining < 3:
                break
            try:
                text = await self.call(key, model, system, user,
                                       min(self.attempt_timeout, remaining), max_tokens)
            except _ModelUnavailable as e:
                log.warning("%s", e)
                problems.append(str(e))
                self.cooldown[model] = time.monotonic() + (COOLDOWN_MISSING if e.missing else COOLDOWN_BUSY)
                continue
            if task == "suggest":
                self.resolved["suggest"] = model
            else:
                self.remember(model)
            return text, model
        if task == "suggest":
            raise LLMError("; ".join(problems) or "no suggestion")
        raise LLMError(self.all_busy_message(len(problems)))

    def no_models_message(self) -> str:
        return f"{self.label} has no models to use. Run `ait models`."

    def all_busy_message(self, tried: int) -> str:
        return (f"{self.label}'s models are busy right now (tried {tried}). Try again in a minute, "
                "or run `ait models` to find others.")

    # -- `ait models` --
    PROBE_LIMIT, PROBE_PARALLEL, PROBE_TIMEOUT = 12, 6, 12.0

    async def probe(self, key: str) -> str:
        """Try likely models from the live catalog and keep the fastest that works."""
        catalog = await self.catalog(key)
        models = (await self.candidates(key, self.preferred("explain"), skip_cooling=False))[:self.PROBE_LIMIT]
        if not models:
            return self.no_models_message()
        messages = [{"role": "user", "content": "Reply with the word ok."}]
        gate = asyncio.Semaphore(self.PROBE_PARALLEL)

        async def one(model: str) -> tuple[str, float | None, str]:
            async with gate:
                start = time.monotonic()
                try:
                    resp = await self.post(key, model, messages, 64,
                                           min(self.PROBE_TIMEOUT, self.attempt_timeout))
                except LLMError as e:
                    return model, None, "✗ " + str(e).replace(f"{model} ", "").replace(f"{model}: ", "")
                took = time.monotonic() - start
                if resp.status_code == 200 and "choices" in resp.json():
                    return model, took, f"✓ works ({took:.1f}s)"
                return model, None, "✗ " + str(self.response_error(resp, model)).replace(f"{model}: ", "")

        results = await asyncio.gather(*(one(m) for m in models))
        width = max(len(m) for m in models)
        lines = [f"  {m.ljust(width)}  {r}" for m, _, r in results]
        n = len(self.rank(catalog))
        head = (f"{self.label}: {n} chat model{'' if n == 1 else 's'} available. Tried the likeliest:"
                if catalog else f"Couldn't fetch {self.label}'s model list; tried the built-in ones:")
        working = sorted((took, m) for m, took, _ in results if took is not None)
        if working:
            best = working[0][1]
            self.remember(best, [m for _, m in working])
            tail = f"Using {best} from now on (fastest that answered)."
            if len(working) > 1:
                tail += f"\nIf it's busy, Shelldon switches to the other {len(working) - 1} that worked."
            if os.environ.get(self.model_env):
                tail += f"\nNote: {self.model_env} is set and takes priority. Remove it to use this one."
        else:
            tail = f"None answered. {self.all_busy_message(len(results)).split('. ', 1)[1]}"
        return f"{head}\n" + "\n".join(lines) + f"\n\n{tail}"


class Ollama(Compat):
    """Local models: no key, never busy, but a cold model can take a while to load."""

    def unreachable(self, e: Exception) -> LLMError:
        return LLMError("Ollama isn't running. Open the Ollama app (or run `ollama serve`), "
                        "then try again.")

    def no_models_message(self) -> str:
        return "No Ollama models installed yet. Run: ollama pull llama3.2:3b"

    def all_busy_message(self, tried: int) -> str:
        return (f"No Ollama model answered in time (tried {tried}). The first request loads the "
                "model into memory, so try again in a moment, or pick a smaller one: "
                "ollama pull llama3.2:3b")

    def running(self) -> bool:
        url = urlparse(self.base_url)
        try:
            with socket.create_connection((url.hostname or "localhost", url.port or 11434), timeout=0.3):
                return True
        except OSError:
            return False


PROVIDERS: dict[str, Compat] = {
    "nvidia": Compat(
        "nvidia", "NVIDIA", os.environ.get("AIT_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        "AIT_NVIDIA_EXPLAIN_MODEL", "AIT_NVIDIA_SUGGEST_MODEL", "openai/gpt-oss-20b",
        # Only used when the live catalog can't be fetched; the catalog changes often.
        ["openai/gpt-oss-20b", "meta/llama-3.1-8b-instruct"],
        # Free-tier models can queue; give up before the shell's 60s wait runs out.
        float(os.environ.get("AIT_NVIDIA_TIMEOUT", "20")), 50.0,
        "Create a new one at build.nvidia.com, then run `ait key`."),
    "openrouter": Compat(
        "openrouter", "OpenRouter", os.environ.get("AIT_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        "AIT_OPENROUTER_MODEL", "AIT_OPENROUTER_SUGGEST_MODEL", None, [],
        20.0, 50.0, "Create one at openrouter.ai/keys, then run `ait key`.",
        # OpenRouter's optional app attribution headers.
        headers={"HTTP-Referer": "https://github.com/thejunghare/shelldon", "X-Title": "Shelldon"},
        free_suffix=":free"),
    "ollama": Ollama(
        "ollama", "Ollama", os.environ.get("AIT_OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/v1",
        "AIT_OLLAMA_MODEL", "AIT_OLLAMA_SUGGEST_MODEL", None, [],
        # Loading a model into memory the first time can take tens of seconds.
        float(os.environ.get("AIT_OLLAMA_TIMEOUT", "50")), 55.0, ""),
}

# --- which provider ----------------------------------------------------------------

ALL = ("anthropic", "openrouter", "nvidia", "ollama")
_KEY_SOURCES = {
    "anthropic": ("ANTHROPIC_API_KEY", "ai-terminal"),
    "nvidia": ("NVIDIA_API_KEY", "ai-terminal-nvidia"),
    "openrouter": ("OPENROUTER_API_KEY", "ai-terminal-openrouter"),
}


def _owner(key: str, slot: str) -> str:
    """The provider a key belongs to; its prefix wins over the slot it was saved in."""
    if key.startswith("nvapi-"):
        return "nvidia"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("sk-ant-"):
        return "anthropic"
    return slot


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
    found: dict[str, str] = {}
    for slot in _KEY_SOURCES:
        if key := _api_key(slot):
            found.setdefault(_owner(key, slot), key)
    return found


def _usable(provider: str, keys: dict[str, str]) -> bool:
    return provider in keys or (provider == "ollama" and PROVIDERS["ollama"].running())


def _missing(provider: str) -> MissingKey:
    if provider == "ollama":
        return MissingKey("Ollama isn't running. Open the Ollama app (or `ollama serve`), "
                          "or install it: brew install ollama")
    return MissingKey(f"No {provider} API key. Run `ait key`.")


def _provider() -> tuple[str, str]:
    """Returns (provider, api_key); the key is "" for Ollama."""
    keys = _available_keys()
    chosen = (os.environ.get("AIT_PROVIDER") or _settings.get("provider") or "").lower()
    if chosen and chosen != "auto":
        if chosen not in ALL:
            raise LLMError(f"Unknown provider '{chosen}'. Use one of: {', '.join(ALL)}")
        if _usable(chosen, keys):
            return chosen, keys.get(chosen, "")
        raise _missing(chosen)
    for provider in ALL:
        if _usable(provider, keys):
            return provider, keys.get(provider, "")
    raise MissingKey("No AI provider set up. Run `ait key` (Anthropic, OpenRouter or NVIDIA), "
                     "or install Ollama for local models: brew install ollama")


def use(provider: str) -> str:
    """`ait use <provider>`: remember which provider to use (auto = first available)."""
    provider = provider.strip().lower()
    if provider not in (*ALL, "auto"):
        return f"ERR: Unknown provider '{provider}'. Use one of: {', '.join(ALL)}, auto"
    _settings.put("provider", provider)
    note = ""
    if os.environ.get("AIT_PROVIDER"):
        note = f"\nNote: AIT_PROVIDER={os.environ['AIT_PROVIDER']} is set and takes priority."
    if provider == "auto":
        return "Using the first available provider: " + ", ".join(ALL) + note
    if not _usable(provider, _available_keys()):
        return f"Switched to {provider}, but it isn't ready yet: {_missing(provider)}{note}"
    return f"Switched to {provider}. Run `ait models` to pick its fastest model.{note}"


def active_provider() -> str:
    """Provider name and model for `ait status`, or why none is usable."""
    try:
        provider, _ = _provider()
    except (MissingKey, LLMError) as e:
        return f"no provider: {e}"
    if provider == "anthropic":
        return f"anthropic, explain model {ANTHROPIC_MODELS['explain']}"
    model = PROVIDERS[provider].preferred("explain")
    return f"{provider}, explain model {model or 'picked on first use (run `ait models`)'}"


async def probe_models() -> str:
    """`ait models` for the active provider."""
    provider, key = _provider()
    if provider == "anthropic":
        return (f"Anthropic: explain, why and # requests use {ANTHROPIC_MODELS['explain']}, "
                f"suggestions use {ANTHROPIC_MODELS['suggest']}.\n"
                "Change them with AIT_EXPLAIN_MODEL / AIT_SUGGEST_MODEL, then ait restart.")
    return await PROVIDERS[provider].probe(key)


# --- calls -----------------------------------------------------------------------

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


async def complete(task: str, system: str, user: str, max_tokens: int = 600) -> tuple[str, str]:
    """Run one prompt for `task` (explain, suggest, generate, why); returns (text, model used)."""
    provider, key = _provider()
    if provider == "anthropic":
        model = ANTHROPIC_MODELS[task]
        text = await _anthropic(key, model, system, user, max_tokens)
    else:
        text, model = await PROVIDERS[provider].complete(key, task, system, user)
    return _CONTROL.sub("", _THINK.sub("", text)).strip(), model
