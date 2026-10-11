"""Own-wallet watching: public addresses only. Reads public chain data and never touches keys or funds.

BTC: mempool.space  /api/address/<a> (balance) + /api/address/<a>/txs (recent txs, mempool first)
ETH: public RPC eth_getBalance (balance) + Blockscout /api/v2/addresses/<a>/transactions?filter=to
     (keyless tx history). If Blockscout is unreachable we fall back to balance-delta detection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

from . import fetchers as F

log = logging.getLogger(__name__)

MEMPOOL_ADDR_URL = "https://mempool.space/api/address/{a}"
MEMPOOL_ADDR_TXS_URL = "https://mempool.space/api/address/{a}/txs"
BLOCKSCOUT_TXS_URL = "https://eth.blockscout.com/api/v2/addresses/{a}/transactions"


@dataclass
class Snapshot:
    chain: str
    address: str
    balance: int                    # confirmed, base units
    pending: Optional[int] = None   # BTC mempool net (sats)
    txs: list[dict] = field(default_factory=list)  # normalized: {txid, confirmed, amount_in, from, block}
    history_ok: bool = True         # False if the tx-history source failed (ETH -> balance-delta only)
    source: str = ""


@dataclass
class IncomingEvent:
    chain: str
    address: str
    txid: Optional[str]             # None for balance-delta events
    amount: int                     # base units received (sats / wei)
    sender: Optional[str]
    confirmed: bool
    block: Optional[int]
    kind: str = "tx"                # tx | confirmed_later | balance_delta
    note: Optional[str] = None


# ---------------------------------------------------------------- fetch
async def snapshot_btc(client: httpx.AsyncClient, address: str) -> Snapshot:
    try:
        r = await client.get(MEMPOOL_ADDR_URL.format(a=address))
        r.raise_for_status()
        d = r.json()
        cs, ms = d["chain_stats"], d["mempool_stats"]
        bal = cs["funded_txo_sum"] - cs["spent_txo_sum"]
        pend = ms["funded_txo_sum"] - ms["spent_txo_sum"]
        r = await client.get(MEMPOOL_ADDR_TXS_URL.format(a=address))
        r.raise_for_status()
        txs = []
        for t in r.json():
            got = sum(v.get("value", 0) for v in t.get("vout", []) if v.get("scriptpubkey_address") == address)
            spent = sum((i.get("prevout") or {}).get("value", 0) for i in t.get("vin", [])
                        if (i.get("prevout") or {}).get("scriptpubkey_address") == address)
            senders = list(dict.fromkeys((i.get("prevout") or {}).get("scriptpubkey_address")
                                         for i in t.get("vin", []) if (i.get("prevout") or {}).get("scriptpubkey_address")))
            st = t.get("status", {})
            txs.append({"txid": t["txid"], "confirmed": bool(st.get("confirmed")), "block": st.get("block_height"),
                        "amount_in": got - spent,
                        "from": (senders[0] if len(senders) == 1 else (f"{len(senders)} inputs" if senders else "coinbase (mining reward)"))})
        return Snapshot("btc", address, bal, pend, txs, True, "mempool.space")
    except Exception as e:  # noqa: BLE001
        log.warning("BTC address fetch failed: %r", e)
        raise F.FetchError("Couldn't reach mempool.space to read that Bitcoin address right now.") from e


async def eth_balance(client: httpx.AsyncClient, address: str) -> tuple[int, str]:
    last = None
    for url in F.ETH_RPC_URLS:
        try:
            return int(await F._rpc(client, url, "eth_getBalance", [address, "latest"]), 16), httpx.URL(url).host
        except Exception as e:  # noqa: BLE001
            last = e
    raise F.FetchError("Couldn't reach a public Ethereum RPC to read that address right now.") from last


async def snapshot_eth(client: httpx.AsyncClient, address: str) -> Snapshot:
    bal, host = await eth_balance(client, address)
    txs, ok = [], True
    try:
        r = await client.get(BLOCKSCOUT_TXS_URL.format(a=address), params={"filter": "to"})
        r.raise_for_status()
        for t in r.json().get("items", []):
            if t.get("block_number") is None:   # pending: report once it's mined
                continue
            to = ((t.get("to") or {}).get("hash") or "").lower()
            if to != address.lower():
                continue
            ok_exec = t.get("status") == "ok"
            txs.append({"txid": t["hash"], "confirmed": True, "block": t.get("block_number"),
                        "amount_in": int(t.get("value") or 0) if ok_exec else 0,
                        "from": (t.get("from") or {}).get("hash")})
    except Exception as e:  # noqa: BLE001
        log.warning("Blockscout history failed, balance-delta only: %r", e)
        ok = False
    return Snapshot("eth", address, bal, None, txs, ok, f"{host} + eth.blockscout.com" if ok else host)


async def snapshot(client: httpx.AsyncClient, chain: str, address: str) -> Snapshot:
    return await (snapshot_btc(client, address) if chain == "btc" else snapshot_eth(client, address))


# ---------------------------------------------------------------- diff (pure)
def seen_from(snap: Snapshot) -> dict:
    return {t["txid"]: 1 if t["confirmed"] else 0 for t in snap.txs}


def diff(prev_seen: dict, prev_balance: Optional[int], snap: Snapshot) -> tuple[list[IncomingEvent], dict]:
    """Compare a fresh snapshot with stored state. Returns (incoming events, updated seen-map).
    Only *incoming* value creates events. Outgoing txs just update state."""
    events: list[IncomingEvent] = []
    seen = dict(prev_seen)
    # API lists newest first; emit oldest first
    for t in reversed(snap.txs):
        tid, conf = t["txid"], t["confirmed"]
        before = seen.get(tid)
        if before is None:
            if t["amount_in"] > 0:
                events.append(IncomingEvent(snap.chain, snap.address, tid, t["amount_in"], t["from"], conf, t["block"]))
        elif before == 0 and conf and t["amount_in"] > 0:
            events.append(IncomingEvent(snap.chain, snap.address, tid, t["amount_in"], t["from"], True, t["block"],
                                        kind="confirmed_later"))
        seen[tid] = 1 if conf else 0
    # ETH: balance went up but no plain incoming tx explains it (internal/contract transfer, or history source down)
    if snap.chain == "eth" and prev_balance is not None and snap.balance > prev_balance and not events:
        note = ("tx details unavailable right now (history source didn't respond)" if not snap.history_ok
                else "no plain ETH transfer found — likely an internal/contract transfer")
        events.append(IncomingEvent("eth", snap.address, None, snap.balance - prev_balance, None, True, None,
                                    kind="balance_delta", note=note))
    return events, seen
