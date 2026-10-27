import re

from . import context, llm, prompts

_COMMAND = re.compile(r"^\s*COMMAND\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RISK = re.compile(r"^\s*RISK\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# Where a command name can start: line start, after ; & | ( { ` $( , or after a
# wrapper such as xargs/sudo/env, optionally escaped (\rm) to skip aliases.
_START = r"(?:^|[;&|({`]|\$\(|\b(?:xargs|sudo|doas|env|command|exec|nohup|time)(?:\s+-\S+)*)\s*\\?"
_RUNNERS = r"(?:sudo\s+)?(?:ba|z|da|k|fi)?sh|python\d*(?:\.\d+)?|perl|ruby|node|osascript"

# Checked locally too, so a model that says "none" can't hide these. Anything that
# matches asks y/n before running, so false alarms are cheap and misses are not.
_DANGER = [
    (re.compile(r"\b(sudo|doas)\b"), "Runs with administrator rights."),
    (re.compile(_START + r"(rm|rmdir|unlink|shred|srm)\b|\s-delete\b"), "Deletes files. They skip the Trash."),
    (re.compile(r"\b(dd|mkfs|newfs\w*|diskutil\s+(erase\w*|partition\w*|zero\w*))\b"), "Can erase a disk."),
    (re.compile(r"\bch(mod|own|flags)\s+(-\w*R|--recursive)"), "Changes permissions recursively."),
    (re.compile(r"\b(curl|wget)\b.*\|\s*(" + _RUNNERS + r")\b|<\(\s*(curl|wget)\b"),
     "Downloads and runs a script."),
    (re.compile(r"\beval\b"), "Runs code that is only known when it runs."),
    (re.compile(r"\b(kill|killall|pkill)\b"), "Stops running processes."),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-\w*f|push\b.*\s(--force\S*|-f)\b|"
                r"checkout\s+(--\s|\.)|restore\b|branch\s+-D|stash\s+(drop|clear))"),
     "Throws away git changes for good."),
    (re.compile(r"(^|[^>])>\|?\s*[^>&\s]|\btee\s+(?!-a)|\btruncate\b"), "Overwrites a file."),
    (re.compile(_START + r"(mv|cp)\b(?![^;&|]*\s-\w*[in])"), "Can overwrite existing files."),
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
