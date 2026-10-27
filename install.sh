#!/usr/bin/env bash
# Installs Shelldon: creates a venv for the daemon and hooks the plugin into zsh and bash.
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

# Hook the plugin into zsh and bash, so either shell works.
add_line() {   # add_line <file> <line>
  if ! grep -qF "$2" "$1" 2>/dev/null; then
    echo "→ Adding plugin to ${1/#"$HOME"/\~}"
    printf '\n# Shelldon\n%s\n' "$2" >> "$1"
  fi
}
add_line "$HOME/.zshrc" "source \"$ROOT/plugin/ai-terminal.zsh\""
# Interactive non-login bash reads ~/.bashrc; login bash (Terminal.app) reads the first
# of these that exists. Never create ~/.bash_profile over an existing ~/.profile.
BASH_LINE="source \"$ROOT/plugin/ai-terminal.bash\""
add_line "$HOME/.bashrc" "$BASH_LINE"
for f in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile" ""; do
  [[ -z $f || -f $f ]] && break
done
add_line "${f:-$HOME/.bash_profile}" "[ -n \"\$BASH_VERSION\" ] && $BASH_LINE"

echo
echo "Done. Next:"
echo "  1. Open a new terminal tab"
echo "  2. Run: ait key        # paste an NVIDIA (nvapi-...) or Anthropic key; saved in Keychain"
echo "  3. Type: # make a new folder here      (then Return)"

PARENT_SHELL="$(ps -o comm= -p "$PPID" 2>/dev/null || true)"
PARENT_SHELL="${PARENT_SHELL#-}"          # login shells show up as "-bash" / "-zsh"
PARENT_SHELL="${PARENT_SHELL##*/}"
if [[ "$PARENT_SHELL" == bash ]]; then
  echo
  echo "You're in bash: plain-English requests, why and ait all work. Suggestions as you"
  echo "type need zsh, and Ctrl+E / Ctrl+G need bash 4+ (brew install bash)."
fi
