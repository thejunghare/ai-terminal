"""Shell context gathering and secret redaction."""
import os
import platform
import re
import subprocess

# Patterns that look like credentials, with their replacements.
# Matches are replaced before anything leaves the machine.
_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "[REDACTED]"),               # Anthropic/OpenAI style keys
    (re.compile(r"nvapi-[A-Za-z0-9_\-]{16,}"), "[REDACTED]"),            # NVIDIA API keys
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED]"),           # GitHub tokens
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED]"),                     # AWS access key ids
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED]"),        # Slack tokens
    (re.compile(r"eyJ[\w\-]{10,}\.[\w\-]{10,}\.[\w\-]+"), "[REDACTED]"),  # JWTs
    (re.compile(r"""(?i)\b((?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*)[^\s"']+"""), r"\1[REDACTED]"),
    (re.compile(r"""(?i)(--password[= ])[^\s"']+"""), r"\1[REDACTED]"),
    (re.compile(r"""(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s"']+"""), r"\1[REDACTED]"),
    (re.compile(r"(://[^:/\s@]+:)[^@\s]+(@)"), r"\1[REDACTED]\2"),    # user:pass@host in URLs
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _git_branch(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=0.5,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def describe(cwd: str) -> str:
    """A short, redacted description of where the user is."""
    lines = [
        f"OS: macOS {platform.mac_ver()[0] or platform.release()} ({platform.machine()})",
        "Shell: zsh",
        f"Directory: {cwd.replace(os.path.expanduser('~'), '~', 1)}",
    ]
    if branch := _git_branch(cwd):
        lines.append(f"Git branch: {branch}")
    return redact("\n".join(lines))
