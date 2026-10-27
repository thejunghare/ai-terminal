#!/usr/bin/env bash
# Installs ai-terminal: creates a venv for the daemon and hooks the plugin into ~/.zshrc.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { echo "Python 3.10+ required"; exit 1; }

VENV_PY="$ROOT/.venv/bin/python"
# Subshell so a crashing interpreter from a broken venv doesn't print "Segmentation fault"
if ( "$VENV_PY" -c 'import anthropic' ) >/dev/null 2>&1; then
  echo "→ Reusing existing virtualenv in $ROOT/.venv"
else
  echo "→ Creating virtualenv in $ROOT/.venv"
  # --clear rebuilds a half-made or stale venv instead of patching it in place
  "$PY" -m venv --clear "$ROOT/.venv" || {
    echo "Could not create the virtualenv. Try: rm -rf \"$ROOT/.venv\" and run install.sh again."
    exit 1
  }
fi
"$VENV_PY" -m pip install --quiet --disable-pip-version-check -r "$ROOT/requirements.txt"

LINE="source \"$ROOT/plugin/ai-terminal.zsh\""
if ! grep -qF "$LINE" "$HOME/.zshrc" 2>/dev/null; then
  echo "→ Adding plugin to ~/.zshrc"
  printf '\n# ai-terminal\n%s\n' "$LINE" >> "$HOME/.zshrc"
fi

echo
echo "Done. Next:"
echo "  1. Open a new terminal tab (or: source ~/.zshrc)"
echo "  2. Run: ait key        # paste an NVIDIA (nvapi-...) or Anthropic key; saved in Keychain"
echo "  3. Type a command, press Ctrl+E to explain it"

# The plugin only loads in zsh. Warn if this terminal is running something else.
PARENT_SHELL="$(ps -o comm= -p "$PPID" 2>/dev/null || true)"
PARENT_SHELL="${PARENT_SHELL#-}"          # login shells show up as "-bash" / "-zsh"
PARENT_SHELL="${PARENT_SHELL##*/}"
if [[ "$PARENT_SHELL" != zsh ]]; then
  echo
  echo "⚠ This terminal is running $PARENT_SHELL, but ai-terminal needs zsh."
  echo "  Run 'zsh' to switch now. In Terminal.app, to make it permanent:"
  echo "  Settings → General → Shells open with → Default login shell"
fi
