from . import context, llm, prompts, render, store

_cache = store.DiskCache("explain")


async def explain(command: str, cwd: str, cols: int = 80) -> str:
    command = command.strip()
    if not command:
        return "Nothing to explain — type a command first."
    safe_cmd = context.redact(command)
    hit = _cache.get(safe_cmd)
    if hit:
        raw, model = hit
    else:
        raw, model = await llm.complete(
            "explain",
            prompts.EXPLAIN_SYSTEM,
            prompts.explain_user(safe_cmd, context.describe(cwd)),
        )
        _cache.put(safe_cmd, [raw, model])
    return render.render(command, raw, cols, model)
