"""Telegram bot wiring (python-telegram-bot v21+/v22, async)."""
from __future__ import annotations

import logging
import os

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.constants import ChatType, MessageEntityType
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import fetchers as F
from . import formatting as T
from . import nlu
from . import swaps as S
from . import wallets as W
from .addresses import detect_chain, looks_like_private_key, normalize
from .store import AlertStore

log = logging.getLogger(__name__)

ALERT_POLL_SECONDS = int(os.environ.get("ALERT_POLL_SECONDS", "180"))
WALLET_POLL_SECONDS = int(os.environ.get("WALLET_POLL_SECONDS", "75"))
SWAP_POLL_SECONDS = int(os.environ.get("SWAP_POLL_SECONDS", "300"))


def _client(ctx: ContextTypes.DEFAULT_TYPE) -> httpx.AsyncClient:
    return ctx.bot_data["http"]


def _store(ctx: ContextTypes.DEFAULT_TYPE) -> AlertStore:
    return ctx.bot_data["store"]


async def _reply(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, T.HELP_TEXT)


async def cmd_fees(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    which = (ctx.args[0].lower() if ctx.args else "")
    try:
        if which in ("btc", "bitcoin"):
            await _reply(update, T.format_btc_fees(await F.fetch_btc_fees(_client(ctx))))
        elif which in ("eth", "ethereum"):
            await _reply(update, T.format_eth_gas(await F.fetch_eth_gas(_client(ctx))))
        else:
            await _reply(update, "Usage: /fees btc  or  /fees eth")
    except F.FetchError as e:
        await _reply(update, f"⚠️ {e}")


async def cmd_speed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, T.speed_tips(ctx.args[0] if ctx.args else None))


async def cmd_stuck(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, T.STUCK_TEXT)


async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await _reply(update, "Usage: /price &lt;coin&gt; — e.g. /price btc, /price sol")
        return
    try:
        await _reply(update, T.format_price(await F.fetch_price(_client(ctx), ctx.args[0])))
    except F.FetchError as e:
        await _reply(update, f"⚠️ {e}")


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        chain, thr = T.parse_alert_args(ctx.args or [])
        aid = _store(ctx).add(update.effective_chat.id, chain, thr)
    except ValueError as e:
        await _reply(update, str(e))
        return
    await _reply(update, f"✅ Alert #{aid} set: I'll message this chat when the {chain.upper()} fee is ≤ "
                         f"{thr:g} {T.unit_for(chain)} (checked every ~{max(1, ALERT_POLL_SECONDS // 60)} min).")


def list_alerts_text(store: AlertStore, chat_id: int) -> str:
    out = T.format_alerts(store.list_for_chat(chat_id))
    sw = T.format_swap_alerts(store.swap_alerts_for_chat(chat_id))
    return out + ("\n\n" + sw if sw else "")


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, list_alerts_text(_store(ctx), update.effective_chat.id))


async def cmd_unalert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    n = _store(ctx).clear_chat(update.effective_chat.id)
    await _reply(update, f"🗑 Cleared {n} alert(s)." if n else "You had no alerts.")


async def poll_fee_alerts_once(http: httpx.AsyncClient, store: AlertStore, send) -> int:
    """One polling pass over every fee alert. `send(chat_id, html)` delivers alerts; returns alerts sent."""
    alerts = store.all()
    if not alerts:
        return 0
    current: dict[str, float] = {}
    chains = {a[2] for a in alerts}
    try:
        if "btc" in chains:
            current["btc"] = (await F.fetch_btc_fees(http)).fastest
    except F.FetchError as e:
        log.warning("alert poll: %s", e)
    try:
        if "eth" in chains:
            current["eth"] = (await F.fetch_eth_gas(http, with_usd=False)).suggested_total_gwei
    except F.FetchError as e:
        log.warning("alert poll: %s", e)
    sent = 0
    for aid, chat_id, chain, thr in alerts:
        val = current.get(chain)
        if val is not None and val <= thr:
            try:
                await send(chat_id, T.format_alert_fired(chain, thr, val))
                sent += 1
            except Exception as e:  # noqa: BLE001 - e.g. user blocked the bot
                log.warning("could not notify chat %s: %r", chat_id, e)
            store.delete(aid)
    return sent


async def check_alerts(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async def send(chat_id, text):
        await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
    await poll_fee_alerts_once(_client(ctx), _store(ctx), send)


# ---------------------------------------------------------------- swap quotes (read-only, never executes)
async def swap_quote_flow(http: httpx.AsyncClient, amount: float, frm: str, to: str,
                          chain: str | None = None) -> str:
    """Fetch quotes from every keyless source for the pair and format a comparison. Never raises."""
    try:
        chain_key, t_in, t_out, notes = S.resolve_pair(frm, to, chain)
    except ValueError as e:
        return str(e)
    quotes, failed = await S.fetch_quotes(http, chain_key, t_in, t_out, amount)
    hint = ""
    if quotes and chain_key == "ethereum":  # gas-aware hint from live base-fee history
        try:
            gas = await F.fetch_eth_gas(http, with_usd=False)
            hist = await F.fetch_eth_base_fee_history(http)
            hint = T.gas_hint(gas.base_fee_gwei, hist)
        except F.FetchError:
            pass
    return T.format_swap_quotes(amount, t_in, t_out, chain_key, quotes, failed, hint, notes)


async def swap_alert_flow(chat_id: int, frm: str, to: str, target: float, chain: str | None,
                          http: httpx.AsyncClient, store: AlertStore) -> str:
    try:
        chain_key, t_in, t_out, _ = S.resolve_pair(frm, to, chain)
    except ValueError as e:
        return str(e)
    try:
        aid = store.add_swap_alert(chat_id, chain_key, t_in.symbol.lower(), t_out.symbol.lower(), target)
    except ValueError as e:
        return str(e)
    now = ""
    try:
        q = await S.fetch_best_rate(http, chain_key, t_in, t_out)
        now = f" Right now 1 {t_in.symbol} ≈ {T._swap_num(q.rate)} {t_out.symbol} ({q.source})."
    except F.FetchError:
        now = " (Couldn't fetch the current rate just now — I'll keep checking.)"
    return (f"✅ Swap alert #{aid} set: I'll message this chat when 1 {t_in.symbol} quotes ≥ "
            f"{T._swap_num(target)} {t_out.symbol} on {S.CHAINS[chain_key].name} "
            f"(checked every ~{max(1, SWAP_POLL_SECONDS // 60)} min).{now}\n{T.SWAP_DISCLAIMER}")


async def cmd_swap(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        amount, frm, to, chain = T.parse_swap_args(ctx.args or [])
    except ValueError as e:
        await _reply(update, str(e))
        return
    await _reply(update, await swap_quote_flow(_client(ctx), amount, frm, to, chain))


async def cmd_swapalert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        frm, to, target, chain = T.parse_swapalert_args(ctx.args or [])
    except ValueError as e:
        await _reply(update, str(e))
        return
    await _reply(update, await swap_alert_flow(update.effective_chat.id, frm, to, target, chain,
                                               _client(ctx), _store(ctx)))


async def poll_swap_alerts_once(http: httpx.AsyncClient, store: AlertStore, send) -> int:
    """One polling pass over every swap-rate alert. Returns the number of alerts sent."""
    alerts = store.all_swap_alerts()
    if not alerts:
        return 0
    cache: dict[tuple[str, str, str], S.SwapQuote | None] = {}
    sent = 0
    for aid, chat_id, chain_key, from_sym, to_sym, target in alerts:
        key = (chain_key, from_sym, to_sym)
        if key not in cache:
            try:
                toks = S.TOKENS.get(chain_key, {})
                if from_sym not in toks or to_sym not in toks:
                    raise F.FetchError(f"unknown pair {from_sym}/{to_sym} on {chain_key}")
                cache[key] = await S.fetch_best_rate(http, chain_key, toks[from_sym], toks[to_sym])
            except F.FetchError as e:
                log.warning("swap alert poll #%s: %s", aid, e)
                cache[key] = None
        q = cache[key]
        if q is not None and q.rate >= target:
            try:
                await send(chat_id, T.format_swap_alert_fired(from_sym, to_sym, target, q))
                sent += 1
            except Exception as e:  # noqa: BLE001 - e.g. user blocked the bot
                log.warning("could not notify chat %s: %r", chat_id, e)
            store.delete_swap_alert(aid)
    return sent


async def check_swap_alerts(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async def send(chat_id, text):
        await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    await poll_swap_alerts_once(_client(ctx), _store(ctx), send)


async def _post_init(app: Application) -> None:
    app.bot_data["http"] = F.make_client()
    me = await app.bot.get_me()
    log.info("Bot started as @%s (id %s), polling for updates", me.username, me.id)


async def _post_shutdown(app: Application) -> None:
    await app.bot_data["http"].aclose()


def build_app(token: str, db_path: str) -> Application:
    app = (Application.builder().token(token).post_init(_post_init).post_shutdown(_post_shutdown).build())
    app.bot_data["store"] = AlertStore(db_path)
    for name, fn in [("start", cmd_help), ("help", cmd_help), ("fees", cmd_fees), ("speed", cmd_speed),
                     ("stuck", cmd_stuck), ("price", cmd_price), ("alert", cmd_alert),
                     ("alerts", cmd_alerts), ("unalert", cmd_unalert), ("addwallet", cmd_addwallet),
                     ("wallets", cmd_wallets), ("removewallet", cmd_removewallet),
                     ("swap", cmd_swap), ("swapalert", cmd_swapalert)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.job_queue.run_repeating(check_alerts, interval=ALERT_POLL_SECONDS, first=30)
    app.job_queue.run_repeating(check_wallets, interval=WALLET_POLL_SECONDS, first=20)
    app.job_queue.run_repeating(check_swap_alerts, interval=SWAP_POLL_SECONDS, first=45)
    return app


# ---------------------------------------------------------------- natural-language chat
def _addressed_to_bot(update: Update, bot_id: int, bot_username: str) -> bool:
    """Private chats: always. Groups: only when the bot is @mentioned or the message replies to the bot."""
    msg = update.effective_message
    if update.effective_chat.type == ChatType.PRIVATE:
        return True
    if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == bot_id:
        return True
    text = msg.text or ""
    for ent in msg.entities or ():
        if ent.type == MessageEntityType.MENTION:
            if text[ent.offset:ent.offset + ent.length].lstrip("@").lower() == bot_username.lower():
                return True
        elif ent.type == MessageEntityType.TEXT_MENTION and ent.user and ent.user.id == bot_id:
            return True
    return False


async def respond_to_text(text: str, chat_id: int, http: httpx.AsyncClient, store: AlertStore,
                          bot_username: str | None = None) -> str:
    """Turn a free-text message into a reply. Every number comes from a live fetch."""
    it = nlu.parse(text, bot_username)
    if it.error:
        return it.error
    k = it.kind
    try:
        if k == "tx":
            out = []
            for chain, h in it.txs[:3]:
                if chain == "eth":
                    out.append(T.format_tx_status(await F.lookup_eth_tx(http, h)))
                else:
                    st = await F.lookup_btc_tx(http, h)
                    if not st.found:  # a bare 64-hex string might be an ETH hash pasted without 0x
                        try:
                            est = await F.lookup_eth_tx(http, "0x" + h)
                            if est.found:
                                st = est
                        except F.FetchError:
                            pass
                    out.append(T.format_tx_status(st))
            return "\n\n".join(out)
        if k == "fees":
            parts = []
            for ch in it.chains:
                try:
                    if ch == "btc":
                        parts.append(T.format_btc_fees(await F.fetch_btc_fees(http)))
                    else:
                        parts.append(T.format_eth_gas(await F.fetch_eth_gas(http)))
                except F.FetchError as e:
                    parts.append(f"⚠️ {e}")
            return "\n\n".join(parts)
        if k == "price":
            ids = list(it.coins)
            not_found: list[str] = []
            search_down = False
            for term in it.unknown_terms:
                try:
                    r = await F.search_coin(http, term)
                except F.FetchError:
                    search_down = True
                    continue
                if r and r[0] not in ids:
                    ids.append(r[0])
                elif not r and not ids:
                    not_found.append(term)
            if not ids:
                if search_down:
                    return "⚠️ CoinGecko search is unavailable right now. Try a common ticker like btc, eth, sol or doge."
                return T.format_prices([], not_found) if not_found else "Price of which coin? e.g. “price of sol and doge”."
            got = await F.fetch_prices(http, ids[:8])
            prices = [got[i] for i in ids[:8] if i in got]
            missing = [i for i in ids[:8] if i not in got]
            return T.format_prices(prices, not_found + missing, unavailable=not prices)
        if k == "speed":
            return T.speed_tips(it.speed_coin)
        if k == "stuck":
            return T.STUCK_TEXT + "\n\n💡 Paste your BTC txid or ETH tx hash and I'll check its live status."
        if k == "set_alert":
            chain, thr = it.chains[0], it.threshold
            try:
                aid = store.add(chat_id, chain, thr)
            except ValueError as e:
                return str(e)
            return (f"✅ Alert #{aid} set: I'll message this chat when the {chain.upper()} "
                    f"{'next-block fee' if chain == 'btc' else 'suggested gas'} is ≤ {thr:g} {T.unit_for(chain)} "
                    f"(checked every ~{max(1, ALERT_POLL_SECONDS // 60)} min). “show my alerts” lists them.")
        if k == "list_alerts":
            return list_alerts_text(store, chat_id)
        if k == "clear_alerts":
            n = store.clear_chat(chat_id)
            return f"🗑 Cleared {n} alert(s)." if n else "You had no alerts."
        if k == "swap":
            return await swap_quote_flow(http, it.swap_amount or 1.0, it.swap_from, it.swap_to, it.swap_chain)
        if k == "swap_alert":
            target = it.threshold / (it.swap_amount or 1.0)  # normalize "2 eth gets 8000 usdc" to per-unit
            return await swap_alert_flow(chat_id, it.swap_from, it.swap_to, target, it.swap_chain, http, store)
        if k == "add_wallet":
            return await add_wallet_flow(chat_id, it.address, it.label, http, store)
        if k == "list_wallets":
            return T.format_wallets(store.wallets_for_chat(chat_id))
        if k == "remove_wallet":
            return remove_wallet_flow(chat_id, it.target or "", store)
        if k == "address_hint":
            return (f"That looks like a {it.chains[0].upper()} address. If it's <b>yours</b>, say “watch my wallet "
                    f"{it.address}” (or /addwallet {it.address} [label]) and I'll tell you about incoming transfers.\n"
                    + T.WATCH_SAFETY)
        if k == "safety":
            return T.SAFETY_TEXT
        if k == "help":
            return T.HELP_TEXT + "\n\nYou can also just chat with me:\n" + T.EXAMPLES
        if k == "greeting":
            return T.GREETING_TEXT
        if k == "thanks":
            return T.THANKS_TEXT
    except F.FetchError as e:
        return f"⚠️ {e}"
    return T.FALLBACK_TEXT


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.text:
        return
    bot = ctx.bot
    if not _addressed_to_bot(update, bot.id, bot.username or ""):
        return
    reply = await respond_to_text(msg.text, update.effective_chat.id, _client(ctx), _store(ctx), bot.username)
    await _reply(update, reply)


# ---------------------------------------------------------------- own-wallet watching
async def add_wallet_flow(chat_id: int, address: str, label: str | None, http: httpx.AsyncClient,
                          store: AlertStore) -> str:
    chain = detect_chain(address)
    if not chain:
        return ("That doesn't look like a valid Bitcoin (bc1…/1…/3…) or Ethereum (0x + 40 hex) <b>public</b> address. "
                "Please double-check it.\n" + T.WATCH_SAFETY)
    address = normalize(address, chain)
    label = nlu.clean_label(label)
    try:
        wid = store.add_wallet(chat_id, chain, address, label)
    except ValueError as e:
        return str(e)
    # First snapshot: record current balance + known txs WITHOUT alerting.
    try:
        snap = await W.snapshot(http, chain, address)
        store.save_wallet_state(wid, snap.balance, snap.pending, W.seen_from(snap))
        return T.format_wallet_added(wid, chain, address, label, snap.balance, snap.pending)
    except F.FetchError as e:
        return T.format_wallet_added(wid, chain, address, label, None, None, err=str(e))


def remove_wallet_flow(chat_id: int, ref: str, store: AlertStore) -> str:
    if not ref.strip():
        return "Usage: /removewallet &lt;id|label|address&gt; (see /wallets)"
    w = store.find_wallet(chat_id, ref)
    if not w or not store.remove_wallet(chat_id, w.id):
        return "I couldn't find that one in this chat's watched addresses. /wallets lists them."
    name = f"{w.label} " if w.label else ""
    return f"🗑 Stopped watching {html_escape(name)}<code>{html_escape(w.address)}</code>."


def html_escape(s: str) -> str:
    import html as _h
    return _h.escape(s)


async def cmd_addwallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    if args and looks_like_private_key(" ".join(args)):
        await _reply(update, T.SAFETY_TEXT)
        return
    if not args:
        await _reply(update, "Usage: /addwallet &lt;your BTC or ETH address&gt; [label]\n" + T.WATCH_SAFETY)
        return
    label = " ".join(args[1:]) or None
    await _reply(update, await add_wallet_flow(update.effective_chat.id, args[0], label, _client(ctx), _store(ctx)))


async def cmd_wallets(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, T.format_wallets(_store(ctx).wallets_for_chat(update.effective_chat.id)))


async def cmd_removewallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, remove_wallet_flow(update.effective_chat.id, " ".join(ctx.args or []), _store(ctx)))


async def poll_wallets_once(http: httpx.AsyncClient, store: AlertStore, send) -> int:
    """One polling pass over every watched address. `send(chat_id, html)` delivers alerts.
    Returns the number of alerts sent. The first successful read of an address only snapshots."""
    sent = 0
    usd_cache: dict[str, float | None] = {}
    for w in store.all_wallets():
        try:
            snap = await W.snapshot(http, w.chain, w.address)
        except F.FetchError as e:
            log.warning("wallet poll #%s: %s", w.id, e)
            continue
        if not w.initialized:
            store.save_wallet_state(w.id, snap.balance, snap.pending, W.seen_from(snap))
            continue
        events, seen = W.diff(w.seen, w.balance, snap)
        store.save_wallet_state(w.id, snap.balance, snap.pending, seen)
        for ev in events:
            if ev.chain not in usd_cache:
                try:
                    usd_cache[ev.chain] = (await F.fetch_price(http, ev.chain)).usd
                except F.FetchError:
                    usd_cache[ev.chain] = None
            try:
                await send(w.chat_id, T.format_incoming(ev, w.label, snap.balance, snap.pending, usd_cache[ev.chain]))
                sent += 1
            except Exception as e:  # noqa: BLE001 - e.g. the user blocked the bot
                log.warning("could not notify chat %s: %r", w.chat_id, e)
    return sent


async def check_wallets(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async def send(chat_id, text):
        await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    await poll_wallets_once(_client(ctx), _store(ctx), send)
