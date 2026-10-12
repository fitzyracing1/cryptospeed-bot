# cryptospeed-bot (Skill)

This is the source for **Skill**, a Telegram bot ([@Simple1990bot](https://t.me/Simple1990bot)). It answers plain-English questions about crypto fees, prices and stuck transactions, sends threshold alerts, and watches wallet addresses for incoming transfers. It only reads public data. It never holds funds or asks for keys.

- Python 3.10+, `python-telegram-bot[job-queue]` and `httpx` (`requirements.txt`)
- Language understanding is rule-based (`cryptospeed/nlu.py`: keywords, regexes, difflib typo correction). No LLM is involved.
- Alerts and wallets are stored in SQLite (default `alerts.db`)

Pages: [Getting started](getting-started.md) · [Usage](usage.md) · [Architecture](architecture.md) · [Contributing](contributing.md)

The repo has no CI workflow and no LICENSE file.

Copyright (c) 2026 Joshua Almeida
