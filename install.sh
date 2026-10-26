#!/usr/bin/env bash
# Installs ai-terminal: creates a venv for the daemon and hooks the plugin into ~/.zshrc.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { echo "Python 3.10+ required"; exit 1; }

echo "→ Creating virtualenv in $ROOT/.venv"
"$PY" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/requirements.txt"

LINE="source \"$ROOT/plugin/ai-terminal.zsh\""
if ! grep -qF "$LINE" "$HOME/.zshrc" 2>/dev/null; then
  echo "→ Adding plugin to ~/.zshrc"
  printf '\n# ai-terminal\n%s\n' "$LINE" >> "$HOME/.zshrc"
fi

echo
echo "Done. Next:"
echo "  1. Open a new terminal tab (or: source ~/.zshrc)"
echo "  2. Run: ait key        # stores your Anthropic API key in Keychain"
echo "  3. Type a command, press Ctrl+E twice to explain it"
