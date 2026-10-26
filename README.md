# ai-terminal

AI help inside your **native macOS terminal** (Terminal.app or iTerm2). It's a zsh plugin, not a new app.

- **Explain** any command before you run it: press `Ctrl+E` twice
- *(coming)* English → command with `Ctrl+G`
- *(coming)* inline ghost-text suggestions as you type
- *(coming)* `why` explains your last failed command

It never runs anything for you. It only fills in or explains your prompt.

## Install

```bash
git clone https://github.com/thejunghare/ai-terminal.git ~/ai-terminal
~/ai-terminal/install.sh
source ~/.zshrc
ait key          # paste your Anthropic API key; it's stored in macOS Keychain
```

## Use

Type a command and **don't press Enter**:

```
❯ find . -name '*.log' -mtime +7 -delete
```

Press `Ctrl+E`. The first press jumps to the end of the line, as it normally does. Press it again and the explanation prints above your prompt. The command you typed stays where it was.

| Command | What it does |
|---|---|
| `ait explain <cmd>` | explain without the keybinding |
| `ait status` | check the background daemon |
| `ait start` / `stop` / `restart` | manage the daemon (it auto-starts on first use) |
| `ait key` | save an API key to Keychain |
| `ait log` | show the daemon log |

Set `AIT_EXPLAIN_KEY='^X^E'` before the `source` line to use a different key.

## How it works

```
zsh (ai-terminal.zsh) ──Unix socket──▶ aitd (Python daemon) ──▶ Claude API
```

A long-running daemon keeps the API client warm, so there's no Python startup on every keypress. Explanations use `claude-sonnet-5-5`. Fast suggestions will use `claude-haiku-5-5`. You can override them with `AIT_EXPLAIN_MODEL` / `AIT_SUGGEST_MODEL`.

**Privacy:** Only the command and a short context (OS, current folder, git branch) are sent. Anything that looks like a secret is replaced with `[REDACTED]` first (API keys, tokens, `password=…`, `user:pass@` URLs). Shell history is not sent.

## Roadmap

Tracked on this repo's GitHub Project board: M1 Explain ✅ · M2 NL→command · M3 Ghost suggestions · M4 `why` · M5 Polish.
