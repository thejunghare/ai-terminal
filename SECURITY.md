# Security

ai-terminal sits inside your shell, so it is built to be careful:

- **Your API key** is stored in the macOS Keychain, never in a file or in your shell history.
- **Secrets are redacted** (API keys, tokens, `password=…`, `user:pass@` URLs) before anything is sent to an AI provider, and lines containing one never get AI suggestions.
- **Shell history is never sent.** It is only read locally for history suggestions.
- **Nothing risky runs without asking.** Commands written from a plain-English request that delete, overwrite, use `sudo`, change permissions recursively, stop processes or run downloaded scripts always ask `y/n` first. This check runs locally and does not depend on the AI.
- The daemon listens on a Unix socket only you can access (`~/.ai-terminal.sock`, mode 600).

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/thejunghare/ai-terminal/security/advisories/new)
instead, and include steps to reproduce. You'll get a reply as soon as possible.
