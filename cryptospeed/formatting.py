"""Pure text formatting (Telegram HTML parse mode). No network access here, so it is unit-testable."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Iterable, Optional

from .fetchers import BtcFees, EthGas, Price, ETH_TRANSFER_GAS

SAFETY_LINE = ("🔒 This bot never holds, receives or sends funds and will never ask for your "
               "private key or seed phrase. Anyone who does is scamming you.")


def _num(x: float, max_dec: int = 2) -> str:
    """1.0 -> '1', 1.50 -> '1.5', 0.0412 -> '0.041' (more decimals for tiny values)."""
    if x == 0:
        return "0"
    dec = max_dec
    if abs(x) < 1:
        dec = max(max_dec, 3)
    if abs(x) < 0.01:
        dec = 4
    s = f"{x:,.{dec}f}".rstrip("0").rstrip(".")
    return s


def _usd(x: float) -> str:
    if x >= 1:
        return f"${x:,.2f}"
    if x >= 0.01:
        return f"${x:.3f}".rstrip("0")
    if x <= 0:
        return "$0"
    import math
    dec = min(12, -math.floor(math.log10(x)) + 3)  # keep ~4 significant digits for micro-priced tokens
    return f"${x:.{dec}f}"


def _ts(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%H:%M UTC")


HELP_TEXT = (
    "⚡ <b>CryptoSpeed</b> — honest help for getting crypto transfers confirmed faster.\n\n"
    "<b>Commands</b>\n"
    "/fees btc — live Bitcoin fee estimates (sat/vB)\n"
    "/fees eth — live Ethereum gas (gwei + ~USD for a transfer)\n"
    "/speed &lt;coin&gt; — tips to confirm faster (btc, eth, sol, ltc, …)\n"
    "/stuck — how to unstick a pending transaction\n"
    "/price &lt;coin&gt; — current USD price\n"
    "/alert btc &lt;sat_per_vb&gt; — ping me when BTC next-block fee ≤ value\n"
    "/alert eth &lt;gwei&gt; — ping me when ETH suggested gas ≤ value\n"
    "/alerts — list your alerts · /unalert — clear them\n"
    "/addwallet &lt;address&gt; [label] — watch YOUR OWN BTC/ETH address for incoming transfers\n"
    "/wallets — list watched addresses · /removewallet &lt;id|label|address&gt;\n\n"
    + SAFETY_LINE + "\n👀 Wallet watching only covers public addresses you add yourself. I never search for other people's wallets."
)


def format_btc_fees(f: BtcFees) -> str:
    return (
        "₿ <b>Bitcoin fees</b> (mempool.space)\n"
        f"🚀 Next block (~10 min): <b>{_num(f.fastest)} sat/vB</b>\n"
        f"⏱ ~30 min: <b>{_num(f.half_hour)} sat/vB</b>\n"
        f"🕐 ~1 hour: <b>{_num(f.hour)} sat/vB</b>\n"
        f"🐢 Economy: {_num(f.economy)} sat/vB · Minimum: {_num(f.minimum)} sat/vB\n\n"
        f"A typical 1-input/2-output SegWit tx is ~141 vB → next-block ≈ {_num(f.fastest * 141, 0)} sats.\n"
        f"<i>Fetched {_ts(f.fetched_at)}. Estimates, not guarantees.</i>"
    )


def format_eth_gas(g: EthGas) -> str:
    lines = [
        "Ξ <b>Ethereum gas</b>",
        f"Base fee (next block): <b>{_num(g.base_fee_gwei)} gwei</b>",
        f"Priority fee (tip): <b>{_num(g.priority_fee_gwei)} gwei</b> typical · {_num(g.low_priority_fee_gwei)} gwei low",
        f"Suggested total: <b>{_num(g.suggested_total_gwei)} gwei</b>",
        f"Simple ETH transfer ({ETH_TRANSFER_GAS:,} gas) ≈ {g.transfer_cost_eth:.8f} ETH",
    ]
    if g.transfer_cost_usd is not None and g.eth_usd is not None:
        lines.append(f"≈ <b>{_usd(g.transfer_cost_usd)}</b> at ETH = {_usd(g.eth_usd)}")
    else:
        lines.append("(USD estimate unavailable — price source didn't respond)")
    lines.append("Token transfers/swaps use more gas (≈65k for an ERC-20 transfer, 100k+ for swaps).")
    lines.append("Tip: set max fee ≈ 2× base fee + tip so a base-fee rise doesn't strand your tx; you only pay what's used.")
    lines.append(f"<i>Source: {html.escape(g.source)}. Fetched {_ts(g.fetched_at)}.</i>")
    return "\n".join(lines)


def format_price(p: Price, query: Optional[str] = None) -> str:
    name = html.escape(p.coin_id)
    s = f"💲 <b>{name}</b>: <b>{_usd(p.usd)}</b>"
    if p.change_24h is not None:
        arrow = "📈" if p.change_24h >= 0 else "📉"
        s += f"  {arrow} {p.change_24h:+.2f}% (24h)"
    s += f"\n<i>Source: {html.escape(p.source)}. Fetched {_ts(p.fetched_at)}.</i>"
    return s


SPEED_TIPS = {
    "btc": (
        "₿ <b>Faster Bitcoin confirmations</b>\n"
        "• Blocks arrive every ~10 min on average (it's random — 1 min or 40 min both happen).\n"
        "• Pay the <b>next-block fee</b> (see /fees btc). Miners pick the highest sat/vB first.\n"
        "• Make sure your wallet has <b>RBF</b> (Replace-By-Fee) on, so you can bump the fee later.\n"
        "• Use SegWit/Taproot addresses (bc1…) — smaller vbytes = cheaper for the same priority.\n"
        "• Fewer inputs = smaller tx. Consolidate small UTXOs when fees are low.\n"
        "• For instant small payments use the <b>Lightning Network</b> (settles in seconds) if both sides support it.\n"
        "• Exchanges often wait for 1–6 confirmations before crediting; check their policy."
    ),
    "eth": (
        "Ξ <b>Faster Ethereum confirmations</b>\n"
        "• A new block every 12 s. A tx with enough fee is usually included in 1–2 blocks; "
        "finality takes ~13 min (2 epochs).\n"
        "• Set a competitive <b>priority fee</b> (tip) and a max fee with headroom above the base fee (see /fees eth).\n"
        "• Stuck? Later txs from your address wait behind the stuck nonce — see /stuck.\n"
        "• Layer-2s — <b>Arbitrum, Base, Optimism</b> — confirm in ~1–2 s at a fraction of L1 cost. "
        "If the receiver (e.g. an exchange) supports deposits on that L2, send there directly.\n"
        "• Native bridge deposits L1→L2 take minutes; withdrawals L2→L1 via the official bridge take ~7 days "
        "(optimistic rollups). Third-party bridges are faster but add their own risk/fees.\n"
        "• Always pick the <b>same network</b> on both sides — wrong-network sends can be lost."
    ),
    "sol": (
        "◎ <b>Faster Solana confirmations</b>\n"
        "• Slots are ~400 ms; a tx is typically confirmed in a few seconds and finalized in ~13 s (32 slots).\n"
        "• Under congestion, add a <b>priority fee</b> (compute-unit price) — most wallets have a 'priority' setting.\n"
        "• If a tx 'expired' (blockhash too old, ~60–90 s), it was never included — just resend it; you weren't charged.\n"
        "• Exchanges may wait for 'finalized' status before crediting."
    ),
    "ltc": (
        "Ł <b>Faster Litecoin confirmations</b>\n"
        "• Blocks every ~2.5 min on average — about 4× more often than Bitcoin.\n"
        "• Fees are usually very low; pay a bit above the wallet's default when blocks are full.\n"
        "• Use a wallet that supports RBF or CPFP so you can bump a stuck tx.\n"
        "• Exchanges commonly require several confirmations (often 3–12) before crediting."
    ),
    "generic": (
        "🔎 <b>General tips for faster transfers</b>\n"
        "• Check the network's current fee/congestion on a block explorer and pay the 'fast' fee.\n"
        "• Use the <b>same network</b> on sender and receiver (e.g. USDT on Tron vs Ethereum vs Solana).\n"
        "• Cheaper/faster networks or L2s are often available for the same asset — ask the receiver what they support.\n"
        "• Exchanges credit only after N confirmations; that wait is on their side, not the network.\n"
        "• Nobody can 'speed up' a tx by you sending them crypto or your seed phrase — that's a scam."
    ),
}
_ALIASES = {"bitcoin": "btc", "ethereum": "eth", "ether": "eth", "solana": "sol", "litecoin": "ltc",
            "arb": "eth", "arbitrum": "eth", "base": "eth", "op": "eth", "optimism": "eth"}


def speed_tips(coin: Optional[str]) -> str:
    if not coin:
        return "Usage: /speed &lt;coin&gt; — e.g. /speed btc, /speed eth, /speed sol, /speed ltc\n\n" + SPEED_TIPS["generic"]
    c = coin.strip().lower()
    c = _ALIASES.get(c, c)
    if c in SPEED_TIPS and c != "generic":
        return SPEED_TIPS[c]
    return f"No specific tips for '{html.escape(coin)}' yet — here are general ones:\n\n" + SPEED_TIPS["generic"]


STUCK_TEXT = (
    "🧯 <b>Unsticking a pending transaction</b>\n\n"
    "<b>Bitcoin</b>\n"
    "• Look it up: <code>https://mempool.space/tx/&lt;txid&gt;</code> — shows its fee rate vs. current fees and ETA.\n"
    "• <b>RBF (Replace-By-Fee)</b>: in your wallet choose 'Bump fee' / 'Speed up'. It rebroadcasts the same payment "
    "with a higher fee. Works if the tx signaled RBF; Bitcoin Core 28+ nodes also accept replacements by default, "
    "but your wallet must support creating one.\n"
    "• <b>CPFP (Child-Pays-For-Parent)</b>: spend an output of the stuck tx (your change, or the receiver's output) "
    "in a new tx with a high fee. Miners include both to collect the combined fee.\n"
    "• If you do nothing, low-fee txs are usually dropped from most mempools after ~2 weeks and the funds reappear in your wallet.\n\n"
    "<b>Ethereum (and EVM L2s)</b>\n"
    "• Look it up: <code>https://etherscan.io/tx/&lt;hash&gt;</code>\n"
    "• <b>Replace with the same nonce</b> and a higher fee: use 'Speed up' in your wallet, or send a new tx with the "
    "<b>same nonce</b> and both max fee and priority fee raised by at least ~10%.\n"
    "• To cancel: send 0 ETH to yourself with the same nonce and a higher fee.\n"
    "• A stuck low nonce blocks all later txs from that address — fix the oldest one first.\n\n"
    "<b>Solana</b>: txs don't sit pending for long — if it expired, just resend.\n\n"
    + SAFETY_LINE
)


def parse_alert_args(args: list[str]) -> tuple[str, float]:
    """Return (chain, threshold) or raise ValueError with a user-facing message."""
    usage = "Usage: /alert btc &lt;sat_per_vb&gt;  or  /alert eth &lt;gwei&gt;  (e.g. /alert btc 5)"
    if len(args) != 2:
        raise ValueError(usage)
    chain = args[0].lower()
    chain = {"bitcoin": "btc", "ethereum": "eth"}.get(chain, chain)
    if chain not in ("btc", "eth"):
        raise ValueError("Alerts support btc and eth only.\n" + usage)
    try:
        thr = float(args[1].replace(",", "."))
    except ValueError:
        raise ValueError(usage) from None
    if not (0 < thr < 100_000):
        raise ValueError("Threshold must be a positive number.\n" + usage)
    return chain, thr


def unit_for(chain: str) -> str:
    return "sat/vB" if chain == "btc" else "gwei"


def format_alerts(alerts: Iterable[tuple[int, str, float]]) -> str:
    alerts = list(alerts)
    if not alerts:
        return "You have no active fee alerts. Set one with /alert btc 5 or /alert eth 1"
    lines = ["🔔 <b>Your fee alerts</b> (each fires once, then is removed):"]
    for _id, chain, thr in alerts:
        what = "BTC next-block fee" if chain == "btc" else "ETH suggested gas (base+tip)"
        lines.append(f"• #{_id}: {what} ≤ {_num(thr)} {unit_for(chain)}")
    lines.append("Clear all with /unalert")
    return "\n".join(lines)


def format_alert_fired(chain: str, thr: float, current: float) -> str:
    if chain == "btc":
        return (f"🔔 BTC fee alert: next-block fee is now <b>{_num(current)} sat/vB</b> "
                f"(your threshold: ≤ {_num(thr)}). See /fees btc")
    return (f"🔔 ETH gas alert: suggested gas is now <b>{_num(current)} gwei</b> "
            f"(your threshold: ≤ {_num(thr)}). See /fees eth")


# ---------------------------------------------------------------- natural-language chat replies
from .fetchers import TxStatus  # noqa: E402

EXAMPLES = (
    "• “btc fees” or “how much to send bitcoin?”\n"
    "• “gas price” / “eth fees”\n"
    "• “price of sol and doge”\n"
    "• “how do I send eth faster?”\n"
    "• “my transaction is stuck”, or paste a BTC txid / ETH tx hash\n"
    "• “tell me when eth gas is under 1”\n"
    "• “alert me if btc fees drop below 3”\n"
    "• “show my alerts” / “clear my alerts”\n"
    "• “watch my wallet &lt;your address&gt;” / “list my wallets”"
)

GREETING_TEXT = ("👋 Hi! I'm CryptoSpeed. I help you get transfers confirmed faster with live fees and honest tips. "
                 "Just ask in plain words, for example:\n" + EXAMPLES)
THANKS_TEXT = "You're welcome! 🙌 Ask any time — e.g. “btc fees” or “price of eth”."
FALLBACK_TEXT = ("🤔 Sorry, I didn't catch that. I understand things like:\n" + EXAMPLES +
                 "\n\nOr use /help for the command list.")
SAFETY_TEXT = (
    "🔒 <b>Safety first</b>\n"
    "I never hold, receive or send funds, and I will <b>never</b> ask for your private key or seed phrase. "
    "Don't share them with anyone — not with me, not with “support”, not with an “accelerator”. "
    "Anyone who asks for them can steal everything in the wallet.\n\n"
    "What I <i>can</i> do: show live fees, tell you how to speed up your own transaction from your own wallet (/stuck), "
    "and alert you when fees drop."
)


def format_prices(prices: list, not_found: list[str], unavailable: bool = False) -> str:
    lines = []
    for p in prices:
        s = f"💲 <b>{html.escape(p.coin_id)}</b>: <b>{_usd(p.usd)}</b>"
        if p.change_24h is not None:
            s += f"  {'📈' if p.change_24h >= 0 else '📉'} {p.change_24h:+.2f}% (24h)"
        lines.append(s)
    if not_found:
        lines.append("❓ Couldn't find: " + ", ".join(html.escape(x) for x in not_found) +
                     " (try the ticker or full name).")
    if unavailable and not prices:
        lines.append("⚠️ Price sources didn't respond (CoinGecko may be rate-limiting). Please try again in a minute.")
    if prices:
        srcs = sorted({p.source for p in prices})
        lines.append(f"<i>Source: {html.escape(', '.join(srcs))}. Fetched {_ts(max(p.fetched_at for p in prices))}.</i>")
    return "\n".join(lines) if lines else "Price of which coin? e.g. “price of sol and doge”."


def _short(h: str) -> str:
    return h[:10] + "…" + h[-6:]


def format_tx_status(t: TxStatus) -> str:
    if t.chain == "btc":
        link = f"https://mempool.space/tx/{t.txid}"
        if not t.found:
            return (f"🔎 Bitcoin tx <code>{_short(t.txid)}</code> wasn't found on mempool.space. It may not have been "
                    "broadcast yet, may have been dropped/replaced, or it's on another chain.")
        if t.confirmed:
            return (f"✅ Bitcoin tx <code>{_short(t.txid)}</code> is <b>confirmed</b> — {t.confirmations} confirmation(s) "
                    f"(block {t.block_height}).\n{link}")
        lines = [f"⏳ Bitcoin tx <code>{_short(t.txid)}</code> is <b>pending</b> (in the mempool, not yet in a block)."]
        if t.fee_rate is not None:
            lines.append(f"Its fee rate: <b>{_num(t.fee_rate)} sat/vB</b>")
        if t.current_fast is not None:
            lines.append(f"Current next-block fee: <b>{_num(t.current_fast)} sat/vB</b>")
            if t.fee_rate is not None and t.fee_rate < t.current_fast:
                lines.append("It's paying less than the next-block rate, so it may wait. To speed it up from <b>your own wallet</b>: "
                             "use RBF (“Bump fee”) if your wallet supports it, or CPFP by spending its output with a high fee. See /stuck")
            elif t.fee_rate is not None:
                lines.append("Its fee is at or above the next-block rate, so it should confirm in the next block or two "
                             "(blocks average ~10 min but vary).")
        lines.append(link)
        return "\n".join(lines)
    # eth
    link = f"https://etherscan.io/tx/{t.txid}"
    if not t.found:
        return (f"🔎 Ethereum tx <code>{_short(t.txid)}</code> wasn't found on Ethereum mainnet. It may have been "
                "dropped/replaced, or it was sent on an L2 or other EVM chain (Arbitrum, Base, Optimism, BNB…) — check "
                "that chain's explorer.")
    if t.confirmed:
        status = "succeeded" if t.eth_success else "<b>FAILED</b> (reverted; the fee was still spent)"
        return (f"✅ Ethereum tx <code>{_short(t.txid)}</code> is included in block {t.block_height} — "
                f"{t.confirmations} confirmation(s); execution {status}.\n{link}")
    lines = [f"⏳ Ethereum tx <code>{_short(t.txid)}</code> is <b>pending</b>."]
    if t.eth_max_fee_gwei is not None:
        s = f"Its max fee: <b>{_num(t.eth_max_fee_gwei)} gwei</b>"
        if t.eth_tip_gwei is not None:
            s += f", tip: {_num(t.eth_tip_gwei)} gwei"
        lines.append(s)
    if t.current_fast is not None:
        lines.append(f"Current base fee: <b>{_num(t.current_fast)} gwei</b>")
        if t.eth_max_fee_gwei is not None and t.eth_max_fee_gwei < t.current_fast:
            lines.append("Its max fee is below the current base fee, so it can't be included until gas drops.")
    lines.append("To speed it up from <b>your own wallet</b>: “Speed up”, or resend with the <b>same nonce</b> and max fee + tip "
                 "raised by at least ~10%. A stuck low nonce blocks later txs. See /stuck")
    lines.append(link)
    return "\n".join(lines)


# ---------------------------------------------------------------- own-wallet watching
from .addresses import short as _short_addr  # noqa: E402

WATCH_SAFETY = ("🔒 I only watch public addresses <b>you</b> add. I never ask for or hold private keys or seed phrases, "
                "can't move funds, and never search for other people's wallets.")


def fmt_amount(units: int, chain: str) -> str:
    if chain == "btc":
        s = f"{units / 1e8:,.8f}".rstrip("0").rstrip(".")
        return f"{s} BTC"
    v = units / 1e18
    s = f"{v:,.6f}".rstrip("0").rstrip(".") if abs(v) >= 0.000001 or v == 0 else f"{v:.10f}".rstrip("0")
    return f"{s} ETH"


def _wname(label: Optional[str], address: str) -> str:
    a = f"<code>{html.escape(_short_addr(address))}</code>"
    return f"<b>{html.escape(label)}</b> ({a})" if label else a


def addr_link(chain: str, address: str) -> str:
    return f"https://mempool.space/address/{address}" if chain == "btc" else f"https://etherscan.io/address/{address}"


def tx_link(chain: str, txid: str) -> str:
    return f"https://mempool.space/tx/{txid}" if chain == "btc" else f"https://etherscan.io/tx/{txid}"


def format_balance(chain: str, balance: Optional[int], pending: Optional[int] = None) -> str:
    if balance is None:
        return "balance not fetched yet"
    s = fmt_amount(balance, chain)
    if chain == "btc" and pending:
        s += f" ({'+' if pending > 0 else '−'}{fmt_amount(abs(pending), chain)} unconfirmed)"
    return s


def format_wallet_added(wid: int, chain: str, address: str, label: Optional[str],
                        balance: Optional[int], pending: Optional[int], err: Optional[str] = None) -> str:
    lines = [f"👀 Watching {chain.upper()} address #{wid}: {_wname(label, address)}"]
    if err:
        lines.append(f"Couldn't read the current balance yet ({html.escape(err)}). I'll take a snapshot on the next check "
                     "and won't alert for past transactions.")
    else:
        lines.append(f"Current balance: <b>{format_balance(chain, balance, pending)}</b> (snapshot saved, so I won't alert on past transactions)")
    lines.append("I'll message this chat when a new incoming transfer shows up (checked about every minute or so).")
    if chain == "eth":
        lines.append("ETH: plain ETH transfers are reported with sender + tx; other balance increases (contract/internal) as a balance change. ERC-20 tokens aren't tracked yet.")
    lines.append(WATCH_SAFETY)
    return "\n".join(lines)


def format_wallets(wallets: list) -> str:
    if not wallets:
        return ("You're not watching any addresses. Add your own with /addwallet &lt;address&gt; [label] "
                "or say “watch my wallet &lt;address&gt;”.\n" + WATCH_SAFETY)
    lines = ["👛 <b>Watched addresses</b> (this chat):"]
    for w in wallets:
        lines.append(f"• #{w.id} {w.chain.upper()} {_wname(w.label, w.address)} — {format_balance(w.chain, w.balance, w.pending)}")
    lines.append("Remove with /removewallet &lt;id|label|address&gt;.")
    return "\n".join(lines)


def format_incoming(ev, label: Optional[str], balance: Optional[int], pending: Optional[int],
                    usd: Optional[float] = None) -> str:
    coin = ev.chain.upper()
    amt = fmt_amount(ev.amount, ev.chain)
    usd_s = ""
    if usd is not None:
        usd_s = f" (≈ {_usd(ev.amount / (1e8 if ev.chain == 'btc' else 1e18) * usd)})"
    if ev.kind == "balance_delta":
        return "\n".join([
            f"💰 <b>{coin} balance increased</b> on {_wname(label, ev.address)}",
            f"Change: <b>+{amt}</b>{usd_s}",
            f"<i>{html.escape(ev.note or '')}</i>",
            f"New balance: <b>{format_balance(ev.chain, balance, pending)}</b>",
            addr_link(ev.chain, ev.address),
        ])
    if ev.kind == "confirmed_later":
        head = f"✅ <b>Incoming {coin} confirmed</b> on {_wname(label, ev.address)}"
    else:
        head = f"💰 <b>Incoming {coin}</b> to {_wname(label, ev.address)}"
    status = (f"✅ confirmed (block {ev.block})" if ev.confirmed and ev.block else
              "✅ confirmed" if ev.confirmed else "⏳ unconfirmed (in mempool) — I'll tell you when it confirms")
    lines = [head, f"Amount: <b>+{amt}</b>{usd_s}"]
    if ev.sender:
        lines.append(f"From: <code>{html.escape(ev.sender)}</code>")
    lines += [f"Status: {status}", f"Tx: {tx_link(ev.chain, ev.txid)}",
              f"New balance: <b>{format_balance(ev.chain, balance, pending)}</b>"]
    return "\n".join(lines)
