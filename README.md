<div align="center">

# ai-terminal

**AI in the terminal you already have.**

Suggestions as you type, plain-English commands and instant explanations,<br>
inside macOS Terminal and iTerm2. No new app, no subscription.

[![CI](https://github.com/thejunghare/ai-terminal/actions/workflows/ci.yml/badge.svg)](https://github.com/thejunghare/ai-terminal/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![macOS](https://img.shields.io/badge/macOS-zsh-black?logo=apple)
[![GitHub stars](https://img.shields.io/github/stars/thejunghare/ai-terminal?style=social)](https://github.com/thejunghare/ai-terminal/stargazers)
[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/thejunghare)

</div>

```
% # make a new folder for my notes
  AI · 1.4s
% mkdir -p notes

% find . -name '*.log' -mtime +7 -delete        ← press Ctrl+E

  Deletes .log files older than 7 days in this folder and below.

  find .          Search from the current folder
  -name '*.log'   Only files ending in .log
  -mtime +7       Modified more than 7 days ago
  -delete         Delete each match without asking

  ⚠︎ Deleted files skip the Trash and can't be recovered.
  1.8s

% git sta▍tus                                   ← grey suggestion, press → to accept
```

---

## Why I built this

The first time I saw AI inside a terminal was in **Warp**, and it felt like the obvious future: ask in plain English, get the command, understand what it does. But the full experience sat behind a paid plan, which was out of my reach. It also meant installing a whole new terminal app, when macOS already ships a terminal that does everything right.

**We already had a terminal. We just needed AI in it.**

I also teach Linux and Bash commands to students at **School of Future Tech**. We live in the terminal every class: installing tools, moving around folders, running commands for the first time. Having suggestions, explanations and plain-English help **right inside the terminal they already use** turned out to be a huge plus. Students see what a command does *before* they run it, and nothing risky runs without asking.

So ai-terminal is a small, free, open-source zsh plugin. Bring your own API key (NVIDIA's is free), and your existing terminal gets smarter.

## Features

| | |
|---|---|
| **Ask in plain English** | Type `# make a new folder here` and press Return. Safe commands run straight away; risky ones ask `y/n` first. |
| **Suggestions as you type** | Grey ghost text after the cursor: from your history instantly, from the AI after a short pause. Press `→` to accept. |
| **Why did that fail?** | Type `why` after an error. You get the cause in one line and the likely fix, already on your prompt. |
| **Explain any command** | Press `Ctrl+E` before running something. A quiet, native-looking breakdown appears above your prompt, and you can keep typing while it loads. |
| **Safety first** | A local risk check flags deletes, overwrites, `sudo`, recursive permission changes, stopping processes and `curl \| sh`, whatever the AI says. |
| **Private by design** | API key in the macOS Keychain, secrets redacted before anything leaves your Mac, shell history never sent. |
| **Fast** | A small background daemon keeps connections warm, answers are cached on disk, and stale requests are cancelled as you type. |
| **Free to run** | Works with a free [NVIDIA](https://build.nvidia.com) key, or with [Anthropic](https://console.anthropic.com) (Claude) for the fastest answers. |

## Quick start

**Requirements:** macOS, zsh (the default shell), Python 3.10+.

```bash
git clone https://github.com/thejunghare/ai-terminal.git ~/ai-terminal
~/ai-terminal/install.sh
```

Open a new terminal tab, then save an API key (it goes into the macOS Keychain):

```bash
ait key
```

| Provider | Get a key | Notes |
|---|---|---|
| **NVIDIA** | [build.nvidia.com](https://build.nvidia.com) → any model → *Get API Key* (`nvapi-…`) | Free tier |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) (`sk-ant-…`) | Fastest answers |

`ait key` detects the provider from the key. Check it worked:

```bash
ait status
```

> **Terminal.app shows a `$` prompt instead of `%`?** It's running bash. Switch to zsh in *Terminal → Settings → General → Shells open with → Default login shell*.

## Usage

### Ask in plain English

Start a line with `#`, say what you want, press Return:

```
% # show the 5 biggest files in here
  AI · 1.6s
% du -ah . | sort -rh | head -5
```

Risky commands wait for you:

```
% rm -r old-builds
⚠︎ Deletes files. They skip the Trash. Run it? y/n  1.2s
```

`y` runs it; any other key leaves it on the line, unrun, for you to edit. `Ctrl+C` or `Esc` cancels while it's thinking, and `Ctrl+G` works on a line without the `#`.

### Suggestions

Just type. A matching command from your history appears in grey immediately; otherwise the AI suggests one after a short pause. `→`, `End` or `Ctrl+F` accepts it, and typing anything else ignores it.

### Explain

Type a command, **don't press Enter**, press `Ctrl+E`. If the cursor is mid-line, the first press jumps to the end (as `Ctrl+E` normally does) and the second explains.

### Why did that fail?

After a command fails, type `why`:

```
% git psuh origin main
git: 'psuh' is not a git command. See 'git --help'.
% why
  You typed psuh instead of push.

  git push origin main   Push with the right spelling

  First fix is on your prompt. Return runs it.  1.4s
% git push origin main
```

`why` reads the end of the failed command's output from the screen (Terminal.app, iTerm2 and tmux). The first time, macOS asks whether Terminal may control Terminal; allow it, or set `AIT_WHY_SCREEN=0` and `why` works from the command and exit status alone. You can also pipe output in: `make 2>&1 | why`. A risky fix (deletes, `sudo`, …) is shown but never put on your prompt.

### The `ait` command

| Command | What it does |
|---|---|
| `ait key` | Save an API key to the Keychain |
| `ait status` | Show the daemon, provider and model |
| `ait models` | Test which NVIDIA models answer for your key, and how fast |
| `ait suggest on` / `history` / `off` | AI + history suggestions, history only, or none |
| `ait explain <cmd>` | Explain without the key binding |
| `ait why` | Same as `why`, if another `why` command already exists |
| `ait start` / `stop` / `restart` | Manage the background daemon (it starts automatically) |
| `ait log` | Show the daemon log |

## Configuration

Add any of these above the `source` line in `~/.zshrc`:

| Variable | Default | Meaning |
|---|---|---|
| `AIT_CONFIRM` | `risky` | `all` asks before running every AI-written command |
| `AIT_SUGGEST` | `1` | `0` turns ghost suggestions off |
| `AIT_AI_SUGGEST` | `1` | `0` keeps suggestions to your history only |
| `AIT_SUGGEST_DELAY` | `0.8` | Seconds of pause before asking the AI |
| `AIT_SUGGEST_STYLE` | `fg=8` | How ghost text is drawn, e.g. `fg=240` for fainter |
| `AIT_EXPLAIN_KEY` | `^E` | Key binding for explain, e.g. `'^X^E'` |
| `AIT_WHY_SCREEN` | `1` | `0` stops `why` reading the terminal screen |
| `AIT_PROVIDER` | auto | `anthropic` or `nvidia` when you've saved both |
| `AIT_NVIDIA_EXPLAIN_MODEL` | `openai/gpt-oss-20b` | Any model from `ait models` |
| `NO_COLOR` | unset | `1` for output without colour |

## Privacy & safety

- **What's sent:** only the line you're working on, plus a short context (macOS version, current folder, git branch). When you type `why`, also the failed command, its exit status and the last 60 lines of its output.
- **Redacted first:** API keys, tokens, `password=…` and `user:pass@` URLs become `[REDACTED]`; lines containing them never get AI suggestions.
- **Never sent:** your shell history (used locally for suggestions only) and your files.
- **Never auto-run when risky:** the local risk check can't be skipped by the AI.
- **Keys:** stored in the macOS Keychain. Cached answers live in `~/.cache/ai-terminal/`.

See [SECURITY.md](SECURITY.md) for details and how to report a vulnerability.

## How it works

```
zsh plugin  ──Unix socket──▶  aitd (Python daemon)  ──HTTPS──▶  NVIDIA or Anthropic API
(keys, ghost text, prompts)    (prompts, redaction, cache, risk check)
```

The plugin never blocks your typing: requests go to a small local daemon over a private Unix socket, and answers come back asynchronously. The daemon keeps API connections warm, caches answers, and cancels suggestion requests you've typed past.

## Roadmap

- [x] Explain commands (`Ctrl+E`)
- [x] Plain-English → command with a risk check
- [x] Suggestions as you type
- [x] `why`: explain the last failed command
- [ ] Homebrew install (`brew install ai-terminal`)
- [ ] Local models via Ollama, fully offline

Ideas and votes welcome in [Issues](https://github.com/thejunghare/ai-terminal/issues).

## Contributing

Pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the layout, tests and guidelines. If ai-terminal helps you or your students, a ⭐ helps other people find it.

## Support the project

ai-terminal is free and always will be. If it saves you time, you can buy me a coffee ☕. It keeps the late-night commits going.

<a href="https://www.buymeacoffee.com/thejunghare"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Buy me a coffee"></a>

## License

[MIT](LICENSE) © Prasad Junghare
