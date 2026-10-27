# ai-terminal

AI help inside your **native macOS terminal** (Terminal.app or iTerm2). It's a zsh plugin, not a new app.

- **Suggestions as you type**: grey "ghost" text after the cursor, from your history instantly and from the AI after a short pause. Press `→` to accept.
- **Explain** any command before you run it: press `Ctrl+E`. You can keep typing; a framed breakdown appears above your prompt.
- *(coming)* English → command with `Ctrl+G`
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

### Suggestions

Start typing. If you've run a matching command before, the rest of it appears in grey straight away. Otherwise, after you pause for a moment, the AI suggests a completion.

| Key | Does |
|---|---|
| `→` / `End` / `Ctrl+F` | accept the suggestion (when the cursor is at the end of the line) |
| keep typing | the suggestion updates or disappears |

### Explain

Type a command and **don't press Enter**:

```
❯ find . -name '*.log' -mtime +7 -delete
```

Press `Ctrl+E`:

```
╭─ explain · openai/gpt-oss-20b ─────────────────────────────
│   find . -name '*.log' -mtime +7 -delete
│
│ Deletes every .log file older than 7 days under this folder.
│
│   find .          search starting in the current directory
│   -name '*.log'   only files whose name ends in .log
│   -mtime +7       last modified more than 7 days ago
│   -delete         delete each match immediately
│
│ ⚠  Irreversible: matching files skip the Trash.
│
│ ›  Run it once without -delete to preview the matches.
╰────────────────────────────────────────────────────────────
```

If your cursor is in the middle of the line, the first press jumps to the end, as `Ctrl+E` normally does; press it again to explain. Explanations are cached on disk, so asking again is instant.

### Commands

| Command | What it does |
|---|---|
| `ait explain <cmd>` | explain without the keybinding |
| `ait suggest on` / `history` / `off` | AI + history suggestions / history only / none (this session) |
| `ait status` | check the background daemon and which provider/model it uses |
| `ait start` / `stop` / `restart` | manage the daemon (it starts automatically) |
| `ait key` | save an API key to Keychain (provider detected from the key) |
| `ait models` | test which NVIDIA models answer for your key, and how fast |
| `ait log` | show the daemon log |

### Settings

Put these above the `source` line in `~/.zshrc`:

| Variable | Default | Meaning |
|---|---|---|
| `AIT_EXPLAIN_KEY` | `^E` | key binding for explain, e.g. `'^X^E'` |
| `AIT_SUGGEST` | `1` | `0` turns ghost suggestions off |
| `AIT_AI_SUGGEST` | `1` | `0` keeps suggestions to your history only |
| `AIT_SUGGEST_DELAY` | `0.8` | seconds of pause before asking the AI |
| `AIT_SUGGEST_STYLE` | `fg=8` | how the ghost text is drawn |

## How it works

```
zsh (ai-terminal.zsh) ──Unix socket──▶ aitd (Python daemon) ──▶ Claude API or NVIDIA API
```

A long-running daemon keeps the API client warm, so there's no Python startup on every keypress. The shell talks to it asynchronously, so typing never waits on the network. When you keep typing, the daemon cancels the now-stale suggestion request.

| | Anthropic | NVIDIA |
|---|---|---|
| Explanations | `claude-sonnet-5-5` | `openai/gpt-oss-20b` (falls back to other models if your account can't use it) |
| Suggestions | `claude-haiku-5-5` | `openai/gpt-oss-20b` |
| Override with | `AIT_EXPLAIN_MODEL`, `AIT_SUGGEST_MODEL` | `AIT_NVIDIA_EXPLAIN_MODEL`, `AIT_NVIDIA_SUGGEST_MODEL` |

Any model listed at `https://integrate.api.nvidia.com/v1/models` works for the NVIDIA overrides.

**Privacy:** Only the command you're typing and a short context (OS, current folder, git branch) are sent. Anything that looks like a secret is replaced with `[REDACTED]` first (API keys, tokens, `password=…`, `user:pass@` URLs), and lines containing one never get AI suggestions. Shell history is used locally for suggestions but never sent. Cached answers live in `~/.cache/ai-terminal/`.

## Roadmap

Tracked on this repo's GitHub Project board: M1 Explain ✅ · M2 NL→command · M3 Ghost suggestions ✅ · M4 `why` · M5 Polish.
