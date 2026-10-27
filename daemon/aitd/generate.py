import re

from . import context, llm, prompts

_COMMAND = re.compile(r"^\s*COMMAND\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RISK = re.compile(r"^\s*RISK\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# Checked locally too, so a model that says "none" can't hide these.
_DANGER = [
    (re.compile(r"(^|[;&|]\s*)(sudo|doas)\b"), "Runs with administrator rights."),
    (re.compile(r"(^|[;&|]\s*)rm\b"), "Deletes files. They skip the Trash."),
    (re.compile(r"\b(dd|mkfs|diskutil\s+erase\w*)\b"), "Can erase a disk."),
    (re.compile(r"\bch(mod|own)\s+(-\w*R|--recursive)"), "Changes permissions recursively."),
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b"), "Downloads and runs a script."),
    (re.compile(r"\b(kill|killall|pkill)\b"), "Stops running processes."),
    (re.compile(r"(^|[^>])>\s*[^>&\s]"), "Overwrites a file."),
]


def _local_risk(command: str) -> str:
    for pattern, message in _DANGER:
        if pattern.search(command):
            return message
    return ""


async def generate(request: str, cwd: str) -> str:
    """Returns "<command>\\n<risk or empty>" for the shell to place on the prompt."""
    request = request.strip().lstrip("#").strip()
    if not request:
        return "ERR: Type what you want after #, e.g.  # make a new folder here"
    answer, _ = await llm.complete(
        "generate", prompts.GENERATE_SYSTEM,
        prompts.generate_user(context.redact(request), context.describe(cwd)),
        max_tokens=300)
    m = _COMMAND.search(answer)
    command = (m.group(1) if m else answer.strip().splitlines()[0] if answer.strip() else "")
    command = command.strip().strip("`").strip()
    if not command:
        return "ERR: The AI didn't return a command. Try rephrasing."
    r = _RISK.search(answer)
    risk = r.group(1).strip() if r else ""
    if risk.lower().rstrip(".") in ("none", "no", "n/a", ""):
        risk = ""
    risk = risk or _local_risk(command)
    return f"{command}\n{risk}"
