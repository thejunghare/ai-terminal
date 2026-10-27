from . import context, llm, prompts, render, store

_cache = store.DiskCache("explain")
# Bump when EXPLAIN_SYSTEM changes so answers in the old style aren't reused.
PROMPT_VERSION = "2"


async def explain(command: str, cwd: str, cols: int = 80) -> str:
    command = command.strip()
    if not command:
        return "Nothing to explain — type a command first."
    safe_cmd = context.redact(command)
    key = f"v{PROMPT_VERSION}:{safe_cmd}"
    hit = _cache.get(key)
    if hit:
        raw, model = hit
    else:
        raw, model = await llm.complete(
            "explain",
            prompts.EXPLAIN_SYSTEM,
            prompts.explain_user(safe_cmd, context.describe(cwd)),
        )
        _cache.put(key, [raw, model])
    return render.render(command, raw, cols, model)
