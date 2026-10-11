"""Entry point: `python -m cryptospeed` runs the bot; `python -m cryptospeed --selftest` checks live APIs."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(prog="cryptospeed", description=__doc__)
    ap.add_argument("--selftest", action="store_true", help="hit every live data source and print results (no Telegram token needed)")
    ap.add_argument("--db", default=os.environ.get("ALERTS_DB", "alerts.db"), help="sqlite file for alerts")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx/telegram log request URLs, which contain the bot token, at INFO. Keep them at WARNING.
    for noisy in ("httpx", "httpcore", "telegram", "telegram.ext", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if a.selftest:
        from .selftest import run
        return asyncio.run(run())

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather and export the token.", file=sys.stderr)
        return 2
    from .bot import build_app
    build_app(token, a.db).run_polling()
    return 0


if __name__ == "__main__":
    sys.exit(main())
