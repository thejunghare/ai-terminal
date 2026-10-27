import asyncio

from . import context, llm, prompts, store

_cache = store.DiskCache("suggest", max_items=2000)
_inflight: asyncio.Task | None = None
MAX_SUFFIX = 160


def _clean(typed: str, answer: str) -> str:
    """Reduce the model's answer to just the text to append after `typed`."""
    text = answer.strip().splitlines()[0] if answer.strip() else ""
    text = text.strip().strip("`")
    if not text or text.lower().strip("<> .") == "none":
        return ""
    if text.startswith(typed):                       # model repeated the whole line
        return text[len(typed):][:MAX_SUFFIX]
    stripped = typed.rstrip()
    if stripped and text.startswith(stripped):       # repeated it minus trailing space
        text = text[len(stripped):].lstrip()
        return text[:MAX_SUFFIX] if typed.endswith(" ") else (" " + text)[:MAX_SUFFIX]
    last = typed.split(" ")[-1]
    if last and not typed.endswith(" ") and text.startswith(last):
        return text[len(last):][:MAX_SUFFIX]        # model repeated the partial word
    return text[:MAX_SUFFIX]


async def suggest(typed: str, cwd: str) -> str:
    """Returns the text to show after the cursor, or "" for no suggestion."""
    global _inflight
    if len(typed.strip()) < 2 or "\n" in typed:
        return ""
    if context.redact(typed) != typed:               # never send lines holding secrets
        return ""
    hit = _cache.get(typed)
    if hit is not None:
        return hit

    if _inflight and not _inflight.done():
        _inflight.cancel()                           # user kept typing; drop the old request
    _inflight = asyncio.create_task(llm.complete(
        "suggest", prompts.SUGGEST_SYSTEM,
        prompts.suggest_user(typed, context.describe(cwd)), max_tokens=60))
    try:
        answer, _ = await _inflight
    except asyncio.CancelledError:
        return ""
    suffix = _clean(typed, answer)
    _cache.put(typed, suffix)
    return suffix
