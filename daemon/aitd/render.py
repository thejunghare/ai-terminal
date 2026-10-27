"""Turns the model's SUMMARY/PART/WARN/TIP lines into a framed, coloured terminal block."""
import os
import re
import textwrap
import unicodedata

_LINE = re.compile(r"^\s*(SUMMARY|PART|WARN|TIP)\s*:\s*(.*)$", re.IGNORECASE)
_ANSI = re.compile(r"\033\[[0-9;]*m")

RESET = "\033[0m"
STYLE = {
    "border": "\033[2m",            # dim
    "title": "\033[2;3m",           # dim italic
    "command": "\033[1;36m",        # bold cyan
    "summary": "\033[1m",           # bold
    "part": "\033[32m",             # green
    "warn": "\033[1;33m",           # bold yellow
    "tip": "\033[35m",              # magenta
}


def _width(text: str) -> int:
    """Display width: ignores colour codes, counts wide characters (emoji, CJK) as two."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in _ANSI.sub("", text))


def _parse(raw: str) -> dict:
    out = {"summary": "", "parts": [], "warn": [], "tip": [], "other": []}
    for line in raw.splitlines():
        if not line.strip():
            continue
        m = _LINE.match(line)
        if not m:
            out["other"].append(line.strip())
            continue
        kind, body = m.group(1).upper(), m.group(2).strip()
        if kind == "SUMMARY" and not out["summary"]:
            out["summary"] = body
        elif kind == "PART":
            piece, _, meaning = body.partition("::")
            out["parts"].append((piece.strip().strip("`"), meaning.strip()))
        elif kind == "WARN":
            out["warn"].append(body)
        elif kind == "TIP":
            out["tip"].append(body)
    return out


def render(command: str, raw: str, cols: int = 80, model: str = "") -> str:
    color = "NO_COLOR" not in os.environ

    def c(style: str, text: str) -> str:
        return f"{STYLE[style]}{text}{RESET}" if color and text else text

    cols = max(40, min(cols or 80, 110))
    inner = cols - 4                      # "│ " on the left, a margin on the right
    data = _parse(raw)
    rows: list[str] = []

    def add(text: str = "", style: str | None = None, indent: str = "", first: str = "") -> None:
        if not text:
            rows.append("")
            return
        wrapped = textwrap.wrap(text, width=inner - _width(first or indent),
                                break_long_words=True, break_on_hyphens=False) or [""]
        for i, chunk in enumerate(wrapped):
            lead = first if i == 0 and first else indent
            rows.append(lead + (c(style, chunk) if style else chunk))

    add(command, "command", indent="  ")
    rows.append("")
    if data["summary"]:
        add(data["summary"], "summary")
    if data["parts"]:
        rows.append("")
        name_w = min(max(_width(p) for p, _ in data["parts"]), inner // 2)
        for piece, meaning in data["parts"]:
            if _width(piece) > name_w:     # long piece: own line, meaning below
                add(piece, "part", indent="  ")
                add(meaning, indent="  " + " " * (name_w + 3))
            else:
                pad = " " * (name_w - _width(piece))
                add(meaning, first="  " + c("part", piece) + pad + "   ",
                    indent="  " + " " * (name_w + 3))
    if not data["summary"] and not data["parts"]:
        for line in data["other"]:            # model ignored the format: show it as-is
            add(line)
    for w in data["warn"]:
        rows.append("")
        add(w, "warn", first="⚠  ", indent="   ")
    for t in data["tip"]:
        rows.append("")
        add(t, "tip", first="›  ", indent="   ")

    title = " explain " + (f"· {model} " if model else "")
    lines = [c("border", "╭─") + c("title", title) + c("border", "─" * max(0, cols - 2 - _width(title)))]
    lines += [c("border", "│ ") + r for r in rows]
    lines.append(c("border", "╰" + "─" * (cols - 1)))
    return "\n".join(lines)
