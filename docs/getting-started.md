# Getting started

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Install:

```bash
git clone https://github.com/fitzyracing1/cryptospeed-bot.git && cd cryptospeed-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

3. Run. The bot uses long polling, so it needs no webhook or public URL.

```bash
export TELEGRAM_BOT_TOKEN="<your token>"
.venv/bin/python -m cryptospeed            # optional: --db /path/alerts.db
```

Without `TELEGRAM_BOT_TOKEN` the program exits with code 2. Keep the token out of git; `.telegram_token` and `*.db` are gitignored.
