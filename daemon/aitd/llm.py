"""Thin wrapper around the Anthropic client."""
import os
import subprocess

import anthropic

EXPLAIN_MODEL = os.environ.get("AIT_EXPLAIN_MODEL", "claude-sonnet-5-5")
SUGGEST_MODEL = os.environ.get("AIT_SUGGEST_MODEL", "claude-haiku-5-5")

KEYCHAIN_SERVICE = "ai-terminal"


def _api_key() -> str | None:
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        return key
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


class MissingKey(RuntimeError):
    pass


_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        key = _api_key()
        if not key:
            raise MissingKey(
                "No API key. Run `ait key` to store one in Keychain, or export ANTHROPIC_API_KEY."
            )
        _client = anthropic.AsyncAnthropic(api_key=key)
    return _client


async def complete(model: str, system: str, user: str, max_tokens: int = 600) -> str:
    resp = await client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
