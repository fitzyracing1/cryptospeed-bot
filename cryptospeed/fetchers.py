"""Live data fetchers. Every number shown by the bot comes from one of these calls.

All sources are free and keyless:
  * mempool.space        - Bitcoin fee estimates
  * public Ethereum RPCs - eth_feeHistory / eth_maxPriorityFeePerGas / eth_gasPrice
  * CoinGecko            - simple price (fallback: Coinbase public spot price)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "cryptospeed-bot/0.1 (+non-custodial fee helper)"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)

MEMPOOL_FEES_URL = "https://mempool.space/api/v1/fees/recommended"
ETH_RPC_URLS = [
    "https://ethereum-rpc.publicnode.com",
    "https://eth.drpc.org",
    "https://rpc.flashbots.net",
    "https://rpc.mevblocker.io",
]
COINGECKO_SIMPLE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/{sym}-USD/spot"

ETH_TRANSFER_GAS = 21_000

# Ticker -> CoinGecko id. Unknown inputs are passed through as a CoinGecko id.
COIN_IDS = {
    "btc": "bitcoin", "bitcoin": "bitcoin",
    "eth": "ethereum", "ethereum": "ethereum",
    "sol": "solana", "solana": "solana",
    "ltc": "litecoin", "litecoin": "litecoin",
    "usdt": "tether", "usdc": "usd-coin",
    "xrp": "ripple", "doge": "dogecoin", "ada": "cardano",
    "trx": "tron", "bnb": "binancecoin", "matic": "matic-network",
    "pol": "polygon-ecosystem-token", "avax": "avalanche-2", "dot": "polkadot",
    "arb": "arbitrum", "op": "optimism", "ton": "the-open-network",
}
# CoinGecko id -> Coinbase symbol for the fallback price source.
COINBASE_SYMBOLS = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "litecoin": "LTC",
    "tether": "USDT", "usd-coin": "USDC", "ripple": "XRP", "dogecoin": "DOGE",
    "cardano": "ADA", "polkadot": "DOT", "avalanche-2": "AVAX",
}


class FetchError(Exception):
    """Raised when a live source could not provide data. Message is user-safe."""


@dataclass
class BtcFees:
    fastest: float      # next block
    half_hour: float
    hour: float
    economy: float
    minimum: float
    source: str = "mempool.space"
    fetched_at: float = field(default_factory=time.time)


@dataclass
class EthGas:
    base_fee_gwei: float          # base fee of the pending (next) block
    priority_fee_gwei: float      # suggested tip: median of per-block 50th-percentile tips (last 10 blocks)
    low_priority_fee_gwei: float  # median of per-block 25th-percentile tips
    source: str
    eth_usd: Optional[float] = None
    fetched_at: float = field(default_factory=time.time)

    @property
    def suggested_total_gwei(self) -> float:
        return self.base_fee_gwei + self.priority_fee_gwei

    @property
    def transfer_cost_eth(self) -> float:
        return self.suggested_total_gwei * ETH_TRANSFER_GAS / 1e9

    @property
    def transfer_cost_usd(self) -> Optional[float]:
        return None if self.eth_usd is None else self.transfer_cost_eth * self.eth_usd


@dataclass
class Price:
    coin_id: str
    usd: float
    change_24h: Optional[float]
    source: str
    fetched_at: float = field(default_factory=time.time)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})


async def _get_json(client: httpx.AsyncClient, url: str, **kw):
    r = await client.get(url, **kw)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- Bitcoin
async def fetch_btc_fees(client: httpx.AsyncClient) -> BtcFees:
    try:
        d = await _get_json(client, MEMPOOL_FEES_URL)
        return BtcFees(
            fastest=float(d["fastestFee"]), half_hour=float(d["halfHourFee"]),
            hour=float(d["hourFee"]), economy=float(d["economyFee"]),
            minimum=float(d["minimumFee"]),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("mempool.space fee fetch failed: %r", e)
        raise FetchError("Couldn't reach mempool.space for Bitcoin fees right now. Please try again in a minute.") from e


# ---------------------------------------------------------------- Ethereum
async def _rpc(client: httpx.AsyncClient, url: str, method: str, params: list):
    r = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    r.raise_for_status()
    d = r.json()
    if "error" in d or "result" not in d:
        raise RuntimeError(f"RPC error from {url}: {d.get('error')}")
    return d["result"]


async def _eth_gas_from(client: httpx.AsyncClient, url: str) -> EthGas:
    # feeHistory over the last 10 blocks; baseFeePerGas has N+1 entries, the last is the NEXT block's base fee.
    # We derive the tip from what was actually paid (reward percentiles) because eth_maxPriorityFeePerGas
    # returns 0 or near-0 on several public RPCs.
    hist = await _rpc(client, url, "eth_feeHistory", ["0xa", "latest", [25, 50]])
    base_next = int(hist["baseFeePerGas"][-1], 16)
    rows = [r for r in hist.get("reward", []) if r and len(r) >= 2]
    if not rows:
        raise RuntimeError("feeHistory returned no reward data")
    def _median(xs: list[int]) -> int:
        xs = sorted(xs)
        return xs[len(xs) // 2]
    low = _median([int(r[0], 16) for r in rows])
    mid = _median([int(r[1], 16) for r in rows])
    host = httpx.URL(url).host
    return EthGas(base_fee_gwei=base_next / 1e9, priority_fee_gwei=mid / 1e9, low_priority_fee_gwei=low / 1e9,
                  source=f"{host} (eth_feeHistory, last 10 blocks)")


async def fetch_eth_gas(client: httpx.AsyncClient, with_usd: bool = True) -> EthGas:
    last: Exception | None = None
    gas: EthGas | None = None
    for url in ETH_RPC_URLS:
        try:
            gas = await _eth_gas_from(client, url)
            break
        except Exception as e:  # noqa: BLE001
            log.warning("ETH RPC %s failed: %r", url, e)
            last = e
    if gas is None:
        raise FetchError("Couldn't reach any public Ethereum RPC for gas prices right now. Please try again shortly.") from last
    if with_usd:
        try:
            gas.eth_usd = (await fetch_price(client, "eth")).usd
        except FetchError:
            gas.eth_usd = None  # USD estimate is optional
    return gas


# ---------------------------------------------------------------- Prices
def resolve_coin_id(coin: str) -> str:
    c = coin.strip().lower()
    return COIN_IDS.get(c, c)


PRICE_TTL = 60.0  # seconds; CoinGecko's free tier rate-limits aggressively
_price_cache: dict[str, Price] = {}


async def fetch_price(client: httpx.AsyncClient, coin: str) -> Price:
    cid = resolve_coin_id(coin)
    cached = _price_cache.get(cid)
    if cached and time.time() - cached.fetched_at < PRICE_TTL:
        return cached
    p = await _fetch_price_uncached(client, coin, cid)
    _price_cache[cid] = p
    return p


async def _fetch_price_uncached(client: httpx.AsyncClient, coin: str, cid: str) -> Price:
    try:
        d = await _get_json(client, COINGECKO_SIMPLE_URL,
                            params={"ids": cid, "vs_currencies": "usd", "include_24hr_change": "true"})
        if cid in d and "usd" in d[cid]:
            return Price(cid, float(d[cid]["usd"]), d[cid].get("usd_24h_change"), "CoinGecko")
        if not d:
            raise FetchError(f"Unknown coin '{coin}'. Try a ticker like btc, eth, sol, ltc or a CoinGecko id.")
    except FetchError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("CoinGecko failed for %s: %r", cid, e)
    sym = COINBASE_SYMBOLS.get(cid)
    if sym:
        try:
            d = await _get_json(client, COINBASE_SPOT_URL.format(sym=sym))
            return Price(cid, float(d["data"]["amount"]), None, "Coinbase (fallback)")
        except Exception as e:  # noqa: BLE001
            log.warning("Coinbase fallback failed for %s: %r", sym, e)
    raise FetchError(f"Couldn't fetch a price for '{coin}' right now (CoinGecko may be rate-limiting). Please try again soon.")


# ---------------------------------------------------------------- batch prices + coin search
async def fetch_prices(client: httpx.AsyncClient, coin_ids: list[str]) -> dict[str, Price]:
    """Fetch several CoinGecko ids in ONE request (cached per id). Missing ids are simply absent."""
    out: dict[str, Price] = {}
    need = []
    for cid in dict.fromkeys(coin_ids):
        c = _price_cache.get(cid)
        if c and time.time() - c.fetched_at < PRICE_TTL:
            out[cid] = c
        else:
            need.append(cid)
    if need:
        try:
            d = await _get_json(client, COINGECKO_SIMPLE_URL,
                                params={"ids": ",".join(need), "vs_currencies": "usd", "include_24hr_change": "true"})
            for cid in need:
                if cid in d and "usd" in d[cid]:
                    p = Price(cid, float(d[cid]["usd"]), d[cid].get("usd_24h_change"), "CoinGecko")
                    _price_cache[cid] = out[cid] = p
        except Exception as e:  # noqa: BLE001
            log.warning("CoinGecko batch failed: %r", e)
            for cid in need:  # per-coin path includes the Coinbase fallback
                try:
                    out[cid] = await fetch_price(client, cid)
                except FetchError:
                    pass
    return out


COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"
SEARCH_TTL = 24 * 3600.0
_search_cache: dict[str, tuple[float, Optional[tuple[str, str, str]]]] = {}


async def search_coin(client: httpx.AsyncClient, term: str) -> Optional[tuple[str, str, str]]:
    """Resolve a free-text coin name/ticker to (coingecko_id, SYMBOL, Name) via CoinGecko search.
    Only exact id/symbol/name matches with a market-cap rank are accepted, so random words don't
    turn into obscure tokens. Results (including misses) are cached for a day. Raises FetchError if the
    API is unreachable."""
    key = term.strip().lower()
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < SEARCH_TTL:
        return hit[1]
    try:
        d = await _get_json(client, COINGECKO_SEARCH_URL, params={"query": key})
    except Exception as e:  # noqa: BLE001
        log.warning("CoinGecko search failed for %s: %r", key, e)
        raise FetchError("CoinGecko search is unavailable right now (it may be rate-limiting). Try a common ticker like btc or eth.") from e
    best = None
    for c in d.get("coins", []):
        exact = key in (str(c.get("id", "")).lower(), str(c.get("symbol", "")).lower(), str(c.get("name", "")).lower())
        rank = c.get("market_cap_rank")
        if exact and rank is not None and (best is None or rank < best[0]):
            best = (rank, (c["id"], str(c.get("symbol", "")).upper(), c.get("name", c["id"])))
    res = best[1] if best else None
    _search_cache[key] = (time.time(), res)
    return res


# ---------------------------------------------------------------- transaction lookups
MEMPOOL_TX_URL = "https://mempool.space/api/tx/{txid}"
MEMPOOL_TIP_URL = "https://mempool.space/api/blocks/tip/height"


@dataclass
class TxStatus:
    chain: str                  # "btc" | "eth"
    txid: str
    found: bool
    confirmed: bool = False
    confirmations: int = 0
    block_height: Optional[int] = None
    fee_rate: Optional[float] = None      # BTC sat/vB (pending or confirmed)
    eth_success: Optional[bool] = None    # ETH receipt status
    eth_max_fee_gwei: Optional[float] = None
    eth_tip_gwei: Optional[float] = None
    current_fast: Optional[float] = None  # current BTC next-block sat/vB, or current ETH base fee gwei (for comparison)
    source: str = ""


async def lookup_btc_tx(client: httpx.AsyncClient, txid: str) -> TxStatus:
    try:
        r = await client.get(MEMPOOL_TX_URL.format(txid=txid))
        if r.status_code in (400, 404):
            return TxStatus("btc", txid, found=False, source="mempool.space")
        r.raise_for_status()
        tx = r.json()
        st = tx.get("status", {})
        vsize = tx.get("weight", 0) / 4 or None
        rate = (tx["fee"] / vsize) if vsize and "fee" in tx else None
        res = TxStatus("btc", txid, found=True, confirmed=bool(st.get("confirmed")),
                       block_height=st.get("block_height"), fee_rate=rate, source="mempool.space")
        if res.confirmed and res.block_height:
            tip = int((await client.get(MEMPOOL_TIP_URL)).text)
            res.confirmations = tip - res.block_height + 1
        else:
            try:
                res.current_fast = (await fetch_btc_fees(client)).fastest
            except FetchError:
                pass
        return res
    except Exception as e:  # noqa: BLE001
        log.warning("BTC tx lookup failed: %r", e)
        raise FetchError("Couldn't reach mempool.space to look up that Bitcoin transaction. Try again shortly.") from e


async def lookup_eth_tx(client: httpx.AsyncClient, txhash: str) -> TxStatus:
    last: Exception | None = None
    for url in ETH_RPC_URLS:
        host = httpx.URL(url).host
        try:
            rcpt = await _rpc(client, url, "eth_getTransactionReceipt", [txhash])
            if rcpt and rcpt.get("blockNumber"):
                latest = int(await _rpc(client, url, "eth_blockNumber", []), 16)
                bn = int(rcpt["blockNumber"], 16)
                return TxStatus("eth", txhash, found=True, confirmed=True, block_height=bn,
                                confirmations=latest - bn + 1, eth_success=(rcpt.get("status") == "0x1"),
                                source=f"{host} (Ethereum mainnet)")
            tx = await _rpc(client, url, "eth_getTransactionByHash", [txhash])
            if not tx:
                return TxStatus("eth", txhash, found=False, source=f"{host} (Ethereum mainnet)")
            res = TxStatus("eth", txhash, found=True, confirmed=False, source=f"{host} (Ethereum mainnet)")
            if tx.get("maxFeePerGas"):
                res.eth_max_fee_gwei = int(tx["maxFeePerGas"], 16) / 1e9
                res.eth_tip_gwei = int(tx.get("maxPriorityFeePerGas", "0x0"), 16) / 1e9
            elif tx.get("gasPrice"):
                res.eth_max_fee_gwei = int(tx["gasPrice"], 16) / 1e9
            try:
                g = await _eth_gas_from(client, url)
                res.current_fast = g.base_fee_gwei
            except Exception:  # noqa: BLE001
                pass
            return res
        except Exception as e:  # noqa: BLE001
            log.warning("ETH tx lookup via %s failed: %r", url, e)
            last = e
    raise FetchError("Couldn't reach a public Ethereum RPC to look up that transaction. Try again shortly.") from last
