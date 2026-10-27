#!/usr/bin/env bash
# Creates the GitHub repo + a GitHub Project board with the roadmap as issues.
# Usage: scripts/setup-github.sh [--public]
set -euo pipefail
cd "$(dirname "$0")/.."

VISIBILITY=--private
[[ "${1:-}" == "--public" ]] && VISIBILITY=--public
REPO_NAME=shelldon
PROJECT_TITLE="Shelldon"

gh auth status >/dev/null 2>&1 || { echo "Run: gh auth login --scopes project"; exit 1; }
gh auth status 2>&1 | grep -q "'project'" || {
  echo "Your gh token lacks the 'project' scope. Run: gh auth refresh -s project"; exit 1; }

OWNER=$(gh api user --jq .login)

if ! gh repo view "$OWNER/$REPO_NAME" >/dev/null 2>&1; then
  echo "→ Creating repo $OWNER/$REPO_NAME ($VISIBILITY)"
  gh repo create "$REPO_NAME" $VISIBILITY --source=. --remote=origin --push \
    --description "AI command suggestions and explanations inside your native macOS terminal (zsh plugin + Claude)"
else
  echo "→ Repo exists, pushing"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$REPO_NAME.git"
  git push -u origin HEAD
fi

echo "→ Creating project \"$PROJECT_TITLE\""
PROJECT_NUM=$(gh project create --owner "$OWNER" --title "$PROJECT_TITLE" --format json --jq .number)
gh project link "$PROJECT_NUM" --owner "$OWNER" --repo "$OWNER/$REPO_NAME" >/dev/null

STATUS_FIELD=$(gh project field-list "$PROJECT_NUM" --owner "$OWNER" --format json \
  --jq '.fields[] | select(.name=="Status") | .id')
PROJECT_ID=$(gh project view "$PROJECT_NUM" --owner "$OWNER" --format json --jq .id)
status_option() {
  gh project field-list "$PROJECT_NUM" --owner "$OWNER" --format json \
    --jq ".fields[] | select(.name==\"Status\") | .options[] | select(.name==\"$1\") | .id"
}
OPT_DONE=$(status_option Done); OPT_TODO=$(status_option Todo)

add_item() {  # add_item <status-option-id> <title> <body>
  local url item
  url=$(gh issue create --repo "$OWNER/$REPO_NAME" --title "$2" --body "$3")
  item=$(gh project item-add "$PROJECT_NUM" --owner "$OWNER" --url "$url" --format json --jq .id)
  gh project item-edit --id "$item" --project-id "$PROJECT_ID" \
    --field-id "$STATUS_FIELD" --single-select-option-id "$1" >/dev/null
  echo "  + $2"
}

echo "→ Adding roadmap items"
add_item "$OPT_DONE" "M1: Explain command (Ctrl+E)" \
"- [x] Python daemon over a Unix socket
- [x] zsh widget: Ctrl+E (twice) explains the typed command
- [x] Secret redaction before sending
- [x] \`ait\` helper: start/stop/status/key/explain/log
- [x] API key in macOS Keychain"
add_item "$OPT_TODO" "M2: Natural language → command (Ctrl+G)" \
"Type \`# find files over 1GB\`, press Ctrl+G, buffer is replaced with a command to review.
- [ ] \`generate\` request type + prompt
- [ ] Danger warning for risky commands
- [ ] Never auto-execute"
add_item "$OPT_TODO" "M3: Inline ghost-text suggestions" \
"- [ ] History prefix match (instant)
- [ ] Debounced (~300ms) Haiku completion via async zle -F
- [ ] Ghost text with region_highlight, accept with →
- [ ] Cache by (prefix, cwd)"
add_item "$OPT_TODO" "M4: Explain failed commands (\`why\`)" \
"- [ ] preexec/precmd hooks record last command + exit status
- [ ] \`why\` explains likely cause and fix
- [ ] Opt-in stderr capture"
add_item "$OPT_TODO" "M5: Polish & distribution" \
"- [ ] launchd agent for auto-start at login
- [ ] config.toml (models, keys, privacy toggles)
- [ ] Optional local model (Ollama) backend
- [ ] Homebrew tap"

echo
echo "Repo:    https://github.com/$OWNER/$REPO_NAME"
echo "Project: https://github.com/users/$OWNER/projects/$PROJECT_NUM"
