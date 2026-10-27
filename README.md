# ai-terminal

AI help inside your **native macOS terminal** (Terminal.app or iTerm2). It's a zsh plugin, not a new app.

- **Suggestions as you type**: grey "ghost" text after the cursor, from your history instantly and from the AI after a short pause. Press `→` to accept.
- **Ask in plain English**: type `# make a new folder here` and press Return. Safe commands run right away; risky ones ask `y/n` first.
- **Explain** any command before you run it: press `Ctrl+E`. You can keep typing; the breakdown appears above your prompt.
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

### Ask in plain English

Start the line with `#`, say what you want, and press Return:

```
% # make a new folder in here
  AI · 1.4s
% mkdir -p new-folder
```

The AI writes the command and checks its risk:

- **Safe commands run straight away**, with the response time shown above.
- **Risky commands ask first.** Deleting, overwriting, `sudo`, recursive permission changes, stopping processes and running downloaded scripts show the reason and wait for you:
  ```
  % rm old.txt
  ⚠︎ Deletes files. They skip the Trash. Run it? y/n  1.2s
  ```
  `y` runs it; any other key leaves it on the line, not run, for you to edit.
- **If the request is unclear**, the AI asks back with a `#` line, which never runs.

The risk check also runs locally, so it doesn't rely on the AI flagging it. `Ctrl+C` or `Esc` cancels while it's thinking. `Ctrl+G` does the same for a line without the `#`. To be asked about every command, set `AIT_CONFIRM=all`.

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
  Deletes .log files older than 7 days in this folder and below.

  find .          Search from the current folder
  -name '*.log'   Only files ending in .log
  -mtime +7       Modified more than 7 days ago
  -delete         Delete each match without asking

  ⚠︎ Deleted files skip the Trash and can't be recovered.
  Run it without -delete first to preview the matches.
```

The summary is bold, meanings are dimmed, and only warnings use colour, all in your terminal's own theme (set `NO_COLOR=1` for none).

The response time is shown underneath. If your cursor is in the middle of the line, the first press jumps to the end, as `Ctrl+E` normally does; press it again to explain. Explanations are cached on disk, so asking again is instant.

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
| `AIT_CONFIRM` | `risky` | `all` asks before running every AI-written command |

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

Tracked on this repo's GitHub Project board: M1 Explain ✅ · M2 NL→command ✅ · M3 Ghost suggestions ✅ · M4 `why` · M5 Polish.
