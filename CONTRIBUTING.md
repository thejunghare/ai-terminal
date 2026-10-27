# Contributing

Thanks for wanting to help! Intelligence in Terminal is small on purpose, so it's easy to find your way around.

## Layout

| Path | What lives there |
|---|---|
| `plugin/ai-terminal.zsh` | The zsh side: key bindings, ghost suggestions, `why`, `ait` command |
| `plugin/ai-terminal.bash` | The bash side: `# requests` via `PROMPT_COMMAND`, `why`, `ait`; keys on bash 4+ |
| `daemon/aitd/client.py` | Tiny socket client the bash plugin uses (keep its imports light) |
| `daemon/aitd/` | The Python daemon: talks to the model providers |
| `daemon/aitd/prompts.py` | Every prompt sent to the model |
| `daemon/aitd/render.py` | How explanations are drawn in the terminal |
| `tests/` | Unit tests (`python -m unittest discover tests`) |

## Getting started

```bash
git clone https://github.com/thejunghare/ai-terminal.git
cd ai-terminal
./install.sh
python3 -m unittest discover tests     # run from the repo root, using .venv/bin/python
zsh -n plugin/ai-terminal.zsh          # syntax checks
/bin/bash -n plugin/ai-terminal.bash   # macOS /bin/bash is 3.2: keep the bash plugin 3.2-compatible
```

After changing the daemon run `ait restart`; after changing the plugin open a new tab.

## Guidelines

- **Never auto-run something risky.** Anything that can lose data must ask first.
- **Keep it native.** Output should look like it belongs in Terminal.app: plain, quiet, no decoration.
- **Don't send more than needed** to the model provider, and keep redaction in front of it.
- Keep pull requests focused, and describe how you tested them.
