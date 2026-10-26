from . import context, llm, prompts

_cache: dict[str, str] = {}


async def explain(command: str, cwd: str) -> str:
    command = command.strip()
    if not command:
        return "Nothing to explain — type a command first."
    safe_cmd = context.redact(command)
    if safe_cmd in _cache:
        return _cache[safe_cmd]
    text = await llm.complete(
        "explain",
        prompts.EXPLAIN_SYSTEM,
        prompts.explain_user(safe_cmd, context.describe(cwd)),
    )
    _cache[safe_cmd] = text
    return text
