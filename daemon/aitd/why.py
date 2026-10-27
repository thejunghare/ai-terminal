"""Explain why the last command failed, from its exit status and output."""
import re

from . import context, generate, llm, prompts, render

FIELD_SEP = "\x1f"      # between fields of the request payload
FIX_SEP = "\x1e"        # between the rendered answer and the fix to put on the prompt
MAX_LINES = 60          # never send more of the output than this
MAX_CHARS = 6000

_ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]|\033\][^\a]*\a")
_FIX = re.compile(r"^\s*FIX\s*:\s*(.+?)\s*(?:::.*)?$", re.IGNORECASE | re.MULTILINE)

_SIGNALS = {1: "SIGHUP", 2: "SIGINT, stopped with Ctrl+C", 6: "SIGABRT", 9: "SIGKILL",
            11: "SIGSEGV, crashed", 13: "SIGPIPE", 15: "SIGTERM"}


def status_meaning(status: str) -> str:
    if not status.isdigit():
        return "unknown (output was piped in)"
    code = int(status)
    if code == 0:
        return "0, success"
    if code == 126:
        return "126, found but not executable"
    if code == 127:
        return "127, command not found"
    if code > 128:
        return f"{code}, killed by signal {code - 128} ({_SIGNALS.get(code - 128, 'signal')})"
    return f"{code}, error"


def output_of(command: str, screen: str) -> str:
    """The failed command's output, cut out of a terminal screen dump."""
    lines = _ANSI.sub("", screen).rstrip().splitlines()
    # The last line is the prompt where `why` was typed.
    if lines and re.search(r"\bwhy\b", lines[-1]):
        lines.pop()
    first = command.strip().splitlines()[0].strip() if command.strip() else ""
    if first:
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].rstrip().endswith(first):     # prompt + command line
                lines = lines[i + 1:]
                break
    return _tail("\n".join(lines))


def _tail(text: str) -> str:
    text = _ANSI.sub("", text).strip("\n")
    text = "\n".join(text.splitlines()[-MAX_LINES:])
    return text[-MAX_CHARS:]


async def why(payload: str, cwd: str, cols: int = 80) -> str:
    status, command, source, output = (payload.split(FIELD_SEP, 3) + ["", "", "", ""])[:4]
    command = command.strip()
    if not command:
        return "ERR: Nothing to explain yet. Run a command first."
    if source == "screen":
        output = output_of(command, output)
    else:
        output = _tail(output)
    safe_cmd = context.redact(command)
    raw, model = await llm.complete(
        "why", prompts.WHY_SYSTEM,
        prompts.why_user(safe_cmd, status_meaning(status), context.redact(output),
                         context.describe(cwd)))
    text = render.render(command, raw, cols, model)
    m = _FIX.search(raw)
    fix = m.group(1).strip().strip("`").strip() if m else ""
    # Only a safe fix goes on the prompt; a risky one stays in the answer to copy by hand.
    if not fix or fix.startswith("#") or generate._local_risk(fix):
        return text
    return f"{text}{FIX_SEP}{fix}"
