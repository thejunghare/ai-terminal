# ai-terminal

AI help inside your **native macOS terminal** (Terminal.app or iTerm2). It's a zsh plugin, not a new app.

- **Explain** any command before you run it: press `Ctrl+E`
- *(coming)* English → command with `Ctrl+G`
- *(coming)* inline ghost-text suggestions as you type
- *(coming)* `why` explains your last failed command

It never runs anything for you. It only fills in or explains your prompt.

## Install

```bash
git clone https://github.com/thejunghare/ai-terminal.git ~/ai-terminal
~/ai-terminal/install.sh
source ~/.zshrc
ait key          # paste an NVIDIA (nvapi-…) or Anthropic (sk-ant-…) key; stored in macOS Keychain
```

You need an API key from either provider:

| Provider | Get a key | Command |
|---|---|---|
| **NVIDIA** (build.nvidia.com, free tier) | [build.nvidia.com](https://build.nvidia.com) → any model → *Get API Key* (starts with `nvapi-`) | `ait key` |
| **Anthropic** (Claude) | [console.anthropic.com](https://console.anthropic.com) | `ait key` |

If you save both, Anthropic is used. To choose, put `export AIT_PROVIDER=nvidia` (or `anthropic`) above the `source` line in `~/.zshrc`.

## Use

Type a command and **don't press Enter**:

```
❯ find . -name '*.log' -mtime +7 -delete
```

Press `Ctrl+E`. The explanation prints above your prompt. If your cursor is in the middle of the line, the first press jumps to the end, as `Ctrl+E` normally does; press it again to explain. The command you typed stays where it was.

| Command | What it does |
|---|---|
| `ait explain <cmd>` | explain without the keybinding |
| `ait status` | check the background daemon and which provider/model it uses |
| `ait start` / `stop` / `restart` | manage the daemon (it auto-starts on first use) |
| `ait key` | save an API key to Keychain (provider detected from the key) |
| `ait models` | test which NVIDIA models answer for your key, and how fast |
| `ait log` | show the daemon log |

Set `AIT_EXPLAIN_KEY='^X^E'` before the `source` line to use a different key.

## How it works

```
zsh (ai-terminal.zsh) ──Unix socket──▶ aitd (Python daemon) ──▶ Claude API or NVIDIA API
```

A long-running daemon keeps the API client warm, so there's no Python startup on every keypress.

| | Anthropic | NVIDIA |
|---|---|---|
| Explanations | `claude-sonnet-5-5` | `openai/gpt-oss-20b` (falls back to other models if your account can't use it) |
| Fast suggestions (coming) | `claude-haiku-5-5` | `openai/gpt-oss-20b` |
| Override with | `AIT_EXPLAIN_MODEL`, `AIT_SUGGEST_MODEL` | `AIT_NVIDIA_EXPLAIN_MODEL`, `AIT_NVIDIA_SUGGEST_MODEL` |

Any model listed at `https://integrate.api.nvidia.com/v1/models` works for the NVIDIA overrides.

**Privacy:** Only the command and a short context (OS, current folder, git branch) are sent. Anything that looks like a secret is replaced with `[REDACTED]` first (API keys, tokens, `password=…`, `user:pass@` URLs). Shell history is not sent.

## Roadmap

Tracked on this repo's GitHub Project board: M1 Explain ✅ · M2 NL→command · M3 Ghost suggestions · M4 `why` · M5 Polish.
