"""Terminal mode: `python -m cryptospeed --cli` opens an interactive REPL that accepts the same
plain-English messages and slash commands as the Telegram bot and prints plain-text replies.
No Telegram token needed. Fee/swap/wallet alerts poll in the background and print in-session."""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
from typing import Callable, Optional

import httpx

from . import __version__
from . import bot as B
from . import fetchers as F
from . import formatting as T
from .addresses import looks_like_private_key
from .store import AlertStore

log = logging.getLogger(__name__)

CLI_CHAT_ID = 0  # single local "chat"; alerts and wallets registered in the CLI stay in the CLI db row

BANNER = (f"⚡ CryptoSpeed {__version__} — terminal mode (no Telegram needed)\n"
          "Type a question in plain English ('btc fees', 'swap 1 eth to usdc') or a slash command\n"
          "(/help lists them). Alerts print here while the session is open. 'exit' or Ctrl-D quits.")


def strip_html(s: str) -> str:
    """Telegram-HTML reply -> plain terminal text."""
    return _html.unescape(re.sub(r"<[^>]+>", "", s))


async def dispatch(text: str, http: httpx.AsyncClient, store: AlertStore,
                   chat_id: int = CLI_CHAT_ID) -> Optional[str]:
    """One REPL input -> reply text (still Telegram-HTML; callers strip it). None means quit."""
    t = text.strip()
    if not t:
        return ""
    if t.lower() in ("exit", "quit", "q", "/exit", "/quit", ":q"):
        return None
    if not t.startswith("/"):
        return await B.respond_to_text(t, chat_id, http, store)

    parts = t.split()
    cmd, args = parts[0].lstrip("/").split("@")[0].lower(), parts[1:]
    try:
        if cmd in ("start", "help"):
            return T.HELP_TEXT + "\n\nYou can also just type questions:\n" + T.EXAMPLES
        if cmd == "fees":
            which = args[0].lower() if args else ""
            if which in ("btc", "bitcoin"):
                return T.format_btc_fees(await F.fetch_btc_fees(http))
            if which in ("eth", "ethereum"):
                return T.format_eth_gas(await F.fetch_eth_gas(http))
            return "Usage: /fees btc  or  /fees eth"
        if cmd == "speed":
            return T.speed_tips(args[0] if args else None)
        if cmd == "stuck":
            return T.STUCK_TEXT
        if cmd == "price":
            if not args:
                return "Usage: /price <coin> — e.g. /price btc, /price sol"
            return T.format_price(await F.fetch_price(http, args[0]))
        if cmd == "alert":
            try:
                chain, thr = T.parse_alert_args(args)
                aid = store.add(chat_id, chain, thr)
            except ValueError as e:
                return str(e)
            return (f"✅ Alert #{aid} set: I'll print a message here when the {chain.upper()} fee is ≤ "
                    f"{thr:g} {T.unit_for(chain)} (while this session is open).")
        if cmd == "alerts":
            return B.list_alerts_text(store, chat_id)
        if cmd == "unalert":
            n = store.clear_chat(chat_id)
            return f"🗑 Cleared {n} alert(s)." if n else "You had no alerts."
        if cmd == "swap":
            try:
                amount, frm, to, chain = T.parse_swap_args(args)
            except ValueError as e:
                return str(e)
            return await B.swap_quote_flow(http, amount, frm, to, chain)
        if cmd == "swapalert":
            try:
                frm, to, target, chain = T.parse_swapalert_args(args)
            except ValueError as e:
                return str(e)
            return await B.swap_alert_flow(chat_id, frm, to, target, chain, http, store)
        if cmd == "addwallet":
            if args and looks_like_private_key(" ".join(args)):
                return T.SAFETY_TEXT
            if not args:
                return "Usage: /addwallet <your BTC or ETH address> [label]\n" + T.WATCH_SAFETY
            return await B.add_wallet_flow(chat_id, args[0], " ".join(args[1:]) or None, http, store)
        if cmd == "wallets":
            return T.format_wallets(store.wallets_for_chat(chat_id))
        if cmd == "removewallet":
            return B.remove_wallet_flow(chat_id, " ".join(args), store)
    except F.FetchError as e:
        return f"⚠️ {e}"
    return f"Unknown command /{cmd} — /help lists the commands."


async def repl(http: httpx.AsyncClient, store: AlertStore,
               input_fn: Callable[[str], str], output_fn: Callable[[str], None],
               chat_id: int = CLI_CHAT_ID) -> None:
    """The REPL loop, separated from setup so tests can drive it with scripted input/output."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, input_fn, "you> ")
        except (EOFError, KeyboardInterrupt):
            output_fn("bye! 👋")
            return
        try:
            reply = await dispatch(line, http, store, chat_id)
        except Exception as e:  # noqa: BLE001 - the REPL must survive anything
            log.exception("dispatch failed")
            reply = f"⚠️ Something went wrong ({type(e).__name__}). Please try again."
        if reply is None:
            output_fn("bye! 👋")
            return
        if reply:
            output_fn(strip_html(reply))


async def run_cli(db_path: str) -> int:
    store = AlertStore(db_path)
    http = F.make_client()

    async def send(_chat_id: int, text: str) -> None:
        print("\n" + strip_html(text) + "\nyou> ", end="", flush=True)

    async def poll_loop(interval: int, poll) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await poll(http, store, send)
            except Exception as e:  # noqa: BLE001 - background polling must never die
                log.warning("background poll failed: %r", e)

    tasks = [
        asyncio.create_task(poll_loop(B.ALERT_POLL_SECONDS, B.poll_fee_alerts_once)),
        asyncio.create_task(poll_loop(B.SWAP_POLL_SECONDS, B.poll_swap_alerts_once)),
        asyncio.create_task(poll_loop(B.WALLET_POLL_SECONDS, B.poll_wallets_once)),
    ]
    print(BANNER)
    try:
        await repl(http, store, input, print)
    finally:
        for t in tasks:
            t.cancel()
        await http.aclose()
    return 0
