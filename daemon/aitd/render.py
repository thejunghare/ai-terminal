"""Turns the model's SUMMARY/PART/FIX/WARN/TIP lines into a quiet, native-looking block.

Design: no frames, titles or emoji. Hierarchy comes from weight and dimming in the
terminal's own palette (like primary/secondary labels); the only colour is the
warning, so it stands out when it matters.
"""
import os
import re
import textwrap
import unicodedata

_LINE = re.compile(r"^\s*(SUMMARY|PART|FIX|WARN|TIP)\s*:\s*(.*)$", re.IGNORECASE)
_ANSI = re.compile(r"\033\[[0-9;]*m")

RESET = "\033[0m"
STYLE = {
    "primary": "\033[1m",      # bold: the one-line summary, command pieces
    "secondary": "\033[2m",    # dim: meanings, tips
    "warning": "\033[33m",     # the terminal's yellow/orange
}
INDENT = "  "
GAP = "   "                    # between a command piece and its meaning


def _width(text: str) -> int:
    """Display width: ignores colour codes, counts wide characters as two columns."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in _ANSI.sub("", text))


def _parse(raw: str) -> dict:
    out = {"summary": "", "parts": [], "fix": [], "warn": [], "tip": [], "other": []}
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
        elif kind in ("PART", "FIX"):
            piece, _, meaning = body.partition("::")
            out["parts" if kind == "PART" else "fix"].append(
                (piece.strip().strip("`"), meaning.strip().rstrip(".")))
        elif kind == "WARN":
            out["warn"].append(body)
        elif kind == "TIP":
            out["tip"].append(body)
    return out


def render(command: str, raw: str, cols: int = 80, model: str = "") -> str:
    color = "NO_COLOR" not in os.environ

    def c(style: str, text: str) -> str:
        return f"{STYLE[style]}{text}{RESET}" if color and text else text

    cols = max(40, min(cols or 80, 100))
    width = cols - len(INDENT) - 2
    data = _parse(raw)
    out: list[str] = []

    def para(text: str, style: str | None = None, lead: str = INDENT, hang: str = INDENT) -> None:
        avail = width - (_width(lead) - len(INDENT))
        for i, chunk in enumerate(textwrap.wrap(text, avail, break_on_hyphens=False) or [""]):
            out.append((lead if i == 0 else hang) + (c(style, chunk) if style else chunk))

    if data["summary"]:
        para(data["summary"], "primary")

    def columns(pairs: list[tuple[str, str]]) -> None:
        """Command pieces on the left, their meanings dimmed on the right."""
        out.append("")
        name_w = min(max(_width(p) for p, _ in pairs), width // 2)
        hang = INDENT + " " * name_w + GAP
        for piece, meaning in pairs:
            if _width(piece) > name_w:              # long piece gets its own line
                para(piece)
                para(meaning, "secondary", lead=hang, hang=hang)
            else:
                lead = INDENT + piece + " " * (name_w - _width(piece)) + GAP
                para(meaning, "secondary", lead=lead, hang=hang)

    for pairs in (data["parts"], data["fix"]):
        if pairs:
            columns(pairs)

    if not (data["summary"] or data["parts"] or data["fix"]):  # model ignored the format
        for line in data["other"]:
            para(line)

    notes = [(w, "warning", "⚠︎ ") for w in data["warn"]] + [(t, "secondary", "") for t in data["tip"]]
    if notes:
        out.append("")
        for text, style, mark in notes:
            para(mark + text, style)

    return "\n".join(out)
