"""Read-only swap quotes from free, keyless aggregator/DEX APIs. The bot NEVER executes swaps,
never builds or signs transactions, and never asks for keys or wallet connections — it only
fetches public quote data so users can compare rates before swapping in their own wallet.

Keyless sources (verified to work without an API key):
  EVM   : ParaSwap /prices, KyberSwap aggregator /routes, CoW Protocol /quote, Li.Fi /quote
  Solana: Jupiter lite-api (swap/v1/quote, with ultra/v1/order as fallback), Raydium trade API
Not usable keyless: 0x (requires an API key), Odos (public endpoint unreachable).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from .fetchers import FetchError

log = logging.getLogger(__name__)

NATIVE_EVM = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"  # pseudo-address for the native coin
ZERO_ADDR = "0x0000000000000000000000000000000000000000"
DUMMY_FROM = "0x1111111111111111111111111111111111111111"  # quote APIs want *a* from-address; never a real user


@dataclass(frozen=True)
class Token:
    symbol: str          # display symbol, e.g. "ETH", "USDC"
    address: str         # EVM contract address / Solana mint; NATIVE_EVM for the native coin
    decimals: int


@dataclass(frozen=True)
class Chain:
    key: str             # "ethereum" | "base" | "arbitrum" | "solana"
    name: str            # display name
    kind: str            # "evm" | "solana"
    chain_id: int        # EVM chain id (0 for solana)
    kyber_slug: str = ""
    cow_slug: str = ""   # empty -> CoW not available on this chain


CHAINS: dict[str, Chain] = {
    "ethereum": Chain("ethereum", "Ethereum", "evm", 1, "ethereum", "mainnet"),
    "base": Chain("base", "Base", "evm", 8453, "base", "base"),
    "arbitrum": Chain("arbitrum", "Arbitrum", "evm", 42161, "arbitrum", "arbitrum_one"),
    "solana": Chain("solana", "Solana", "solana", 0),
}
CHAIN_ALIASES = {
    "eth": "ethereum", "ethereum": "ethereum", "mainnet": "ethereum", "l1": "ethereum",
    "base": "base",
    "arb": "arbitrum", "arbitrum": "arbitrum",
    "sol": "solana", "solana": "solana",
}
# Order used to infer the chain when the user doesn't name one.
CHAIN_ORDER = ["ethereum", "solana", "base", "arbitrum"]

_WETH = {"ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
         "base": "0x4200000000000000000000000000000000000006",
         "arbitrum": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"}

TOKENS: dict[str, dict[str, Token]] = {
    "ethereum": {
        "eth": Token("ETH", NATIVE_EVM, 18),
        "weth": Token("WETH", _WETH["ethereum"], 18),
        "wbtc": Token("WBTC", "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8),
        "usdc": Token("USDC", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
        "usdt": Token("USDT", "0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
        "dai": Token("DAI", "0x6B175474E89094C44Da98b954EedeAC495271d0F", 18),
        "link": Token("LINK", "0x514910771AF9Ca656af840dff83E8264EcF986CA", 18),
        "uni": Token("UNI", "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", 18),
    },
    "base": {
        "eth": Token("ETH", NATIVE_EVM, 18),
        "weth": Token("WETH", _WETH["base"], 18),
        "usdc": Token("USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
    },
    "arbitrum": {
        "eth": Token("ETH", NATIVE_EVM, 18),
        "weth": Token("WETH", _WETH["arbitrum"], 18),
        "usdc": Token("USDC", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
        "usdt": Token("USDT", "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6),
        "arb": Token("ARB", "0x912CE59144191C1204E64559FE8253a0e49E6548", 18),
    },
    "solana": {
        "sol": Token("SOL", "So11111111111111111111111111111111111111112", 9),
        "usdc": Token("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
        "usdt": Token("USDT", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
        "jup": Token("JUP", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", 6),
        "bonk": Token("BONK", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", 5),
        "wif": Token("WIF", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", 6),
    },
}
# Spoken aliases -> token key. "btc"/"bitcoin" become wrapped BTC on EVM chains (noted to the user).
SYMBOL_ALIASES = {"ether": "eth", "ethereum": "eth", "bitcoin": "wbtc", "btc": "wbtc",
                  "tether": "usdt", "solana": "sol", "chainlink": "link", "uniswap": "uni"}

ALL_SWAP_WORDS: set[str] = (
    {s for toks in TOKENS.values() for s in toks} | set(SYMBOL_ALIASES)
)


def supported_pairs_text() -> str:
    return "; ".join(f"{CHAINS[c].name}: " + ", ".join(t.upper() for t in TOKENS[c]) for c in CHAIN_ORDER)


def resolve_pair(frm: str, to: str, chain: Optional[str] = None) -> tuple[str, Token, Token, list[str]]:
    """Map user words to (chain_key, sell_token, buy_token, notes). Raises ValueError with a
    user-facing message when the pair isn't supported."""
    notes: list[str] = []
    f_raw, t_raw = frm.strip().lower(), to.strip().lower()
    if chain:
        ck = CHAIN_ALIASES.get(chain.strip().lower())
        if not ck:
            raise ValueError(f"I don't know the chain '{chain}'. I can quote on: "
                             + ", ".join(CHAINS[c].name for c in CHAIN_ORDER) + ".")
        candidates = [ck]
    else:
        candidates = CHAIN_ORDER
    for ck in candidates:
        toks = TOKENS[ck]
        f_key = f_raw if f_raw in toks else SYMBOL_ALIASES.get(f_raw, f_raw)
        t_key = t_raw if t_raw in toks else SYMBOL_ALIASES.get(t_raw, t_raw)
        if f_key in toks and t_key in toks:
            if f_key == t_key:
                raise ValueError("Those are the same token — pick two different ones, e.g. /swap 1 eth usdc")
            if f_raw in ("btc", "bitcoin") or t_raw in ("btc", "bitcoin"):
                notes.append("BTC on an EVM chain means wrapped BTC (WBTC); native Bitcoin can't be swapped on-chain here.")
            return ck, toks[f_key], toks[t_key], notes
    raise ValueError(f"I can't quote {frm.upper()} → {to.upper()}"
                     + (f" on {chain}" if chain else "")
                     + ". Supported pairs — " + supported_pairs_text() + ".")


@dataclass
class SwapQuote:
    source: str                  # aggregator name
    chain: str                   # chain key
    sell: Token
    buy: Token
    amount_in: float             # human units the user asked to sell
    amount_out: float            # human units of expected output
    price_impact_pct: Optional[float] = None   # percent (1.0 == 1%)
    gas_usd: Optional[float] = None            # estimated gas cost in USD
    in_usd: Optional[float] = None             # USD value of the input, when the API reports it
    out_usd: Optional[float] = None            # USD value of the output
    note: Optional[str] = None
    fetched_at: float = field(default_factory=time.time)

    @property
    def rate(self) -> float:
        return self.amount_out / self.amount_in if self.amount_in else 0.0

    @property
    def net_out_usd(self) -> Optional[float]:
        if self.out_usd is None or self.gas_usd is None:
            return None
        return self.out_usd - self.gas_usd


def _base_units(amount: float, decimals: int) -> int:
    return round(amount * 10**decimals)


def _impact_from_usd(in_usd: Optional[float], out_usd: Optional[float]) -> Optional[float]:
    if not in_usd or out_usd is None or in_usd <= 0:
        return None
    return (1 - out_usd / in_usd) * 100


# ---------------------------------------------------------------- EVM sources
async def quote_paraswap(c: httpx.AsyncClient, chain: Chain, sell: Token, buy: Token, amount: float) -> SwapQuote:
    r = await c.get("https://api.paraswap.io/prices", params={
        "srcToken": sell.address, "destToken": buy.address, "amount": str(_base_units(amount, sell.decimals)),
        "srcDecimals": sell.decimals, "destDecimals": buy.decimals, "side": "SELL", "network": chain.chain_id})
    r.raise_for_status()
    pr = r.json()["priceRoute"]
    in_usd = float(pr["srcUSD"]) if pr.get("srcUSD") else None
    out_usd = float(pr["destUSD"]) if pr.get("destUSD") else None
    return SwapQuote("ParaSwap", chain.key, sell, buy, amount,
                     int(pr["destAmount"]) / 10**buy.decimals,
                     price_impact_pct=_impact_from_usd(in_usd, out_usd),
                     gas_usd=float(pr["gasCostUSD"]) if pr.get("gasCostUSD") else None,
                     in_usd=in_usd, out_usd=out_usd)


async def quote_kyber(c: httpx.AsyncClient, chain: Chain, sell: Token, buy: Token, amount: float) -> SwapQuote:
    r = await c.get(f"https://aggregator-api.kyberswap.com/{chain.kyber_slug}/api/v1/routes", params={
        "tokenIn": sell.address, "tokenOut": buy.address,
        "amountIn": str(_base_units(amount, sell.decimals)), "gasInclude": "true"})
    r.raise_for_status()
    d = r.json()
    if d.get("code") not in (0, None) or "data" not in d:
        raise RuntimeError(f"KyberSwap error: {d.get('message')}")
    rs = d["data"]["routeSummary"]
    in_usd = float(rs["amountInUsd"]) if rs.get("amountInUsd") else None
    out_usd = float(rs["amountOutUsd"]) if rs.get("amountOutUsd") else None
    return SwapQuote("KyberSwap", chain.key, sell, buy, amount,
                     int(rs["amountOut"]) / 10**buy.decimals,
                     price_impact_pct=_impact_from_usd(in_usd, out_usd),
                     gas_usd=float(rs["gasUsd"]) if rs.get("gasUsd") else None,
                     in_usd=in_usd, out_usd=out_usd)


async def quote_cow(c: httpx.AsyncClient, chain: Chain, sell: Token, buy: Token, amount: float) -> SwapQuote:
    if not chain.cow_slug:
        raise RuntimeError(f"CoW not available on {chain.name}")
    sell_addr, note = sell.address, "fee included in quote; CoW settles without a separate gas payment"
    if sell.address == NATIVE_EVM:  # CoW can buy native ETH but not sell it; quote via WETH
        sell_addr = _WETH[chain.key]
        note = "quoted as WETH (CoW can't sell native ETH); " + note
    r = await c.post(f"https://api.cow.fi/{chain.cow_slug}/api/v1/quote", json={
        "sellToken": sell_addr, "buyToken": buy.address,
        "sellAmountBeforeFee": str(_base_units(amount, sell.decimals)),
        "kind": "sell", "from": DUMMY_FROM})
    r.raise_for_status()
    q = r.json()["quote"]
    return SwapQuote("CoW Swap", chain.key, sell, buy, amount,
                     int(q["buyAmount"]) / 10**buy.decimals, gas_usd=None, note=note)


async def quote_lifi(c: httpx.AsyncClient, chain: Chain, sell: Token, buy: Token, amount: float) -> SwapQuote:
    def addr(t: Token) -> str:
        return ZERO_ADDR if t.address == NATIVE_EVM else t.address
    r = await c.get("https://li.quest/v1/quote", params={
        "fromChain": chain.chain_id, "toChain": chain.chain_id, "fromToken": addr(sell),
        "toToken": addr(buy), "fromAmount": str(_base_units(amount, sell.decimals)), "fromAddress": DUMMY_FROM})
    r.raise_for_status()
    est = r.json()["estimate"]
    in_usd = float(est["fromAmountUSD"]) if est.get("fromAmountUSD") else None
    out_usd = float(est["toAmountUSD"]) if est.get("toAmountUSD") else None
    gas = sum(float(g.get("amountUSD") or 0) for g in est.get("gasCosts", [])) or None
    return SwapQuote("Li.Fi", chain.key, sell, buy, amount,
                     int(est["toAmount"]) / 10**buy.decimals,
                     price_impact_pct=_impact_from_usd(in_usd, out_usd),
                     gas_usd=gas, in_usd=in_usd, out_usd=out_usd)


# ---------------------------------------------------------------- Solana sources
async def quote_jupiter(c: httpx.AsyncClient, chain: Chain, sell: Token, buy: Token, amount: float) -> SwapQuote:
    params = {"inputMint": sell.address, "outputMint": buy.address,
              "amount": str(_base_units(amount, sell.decimals))}
    last: Exception | None = None
    for url, extra in (("https://lite-api.jup.ag/swap/v1/quote", {"slippageBps": 50}),
                       ("https://lite-api.jup.ag/ultra/v1/order", {})):
        try:
            r = await c.get(url, params={**params, **extra})
            r.raise_for_status()
            d = r.json()
            impact = None
            if d.get("priceImpactPct") is not None:
                impact = float(d["priceImpactPct"]) * 100  # Jupiter reports a fraction (0.01 == 1%)
            return SwapQuote("Jupiter", chain.key, sell, buy, amount,
                             int(d["outAmount"]) / 10**buy.decimals, price_impact_pct=impact,
                             gas_usd=None, note="Solana network fee is typically well under $0.01")
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"Jupiter quote failed: {last!r}")


async def quote_raydium(c: httpx.AsyncClient, chain: Chain, sell: Token, buy: Token, amount: float) -> SwapQuote:
    r = await c.get("https://transaction-v1.raydium.io/compute/swap-base-in", params={
        "inputMint": sell.address, "outputMint": buy.address,
        "amount": str(_base_units(amount, sell.decimals)), "slippageBps": 50, "txVersion": "V0"})
    r.raise_for_status()
    d = r.json()
    if not d.get("success"):
        raise RuntimeError(f"Raydium error: {d.get('msg') or d}")
    data = d["data"]
    impact = float(data["priceImpactPct"]) if data.get("priceImpactPct") is not None else None  # already percent
    return SwapQuote("Raydium", chain.key, sell, buy, amount,
                     int(data["outputAmount"]) / 10**buy.decimals, price_impact_pct=impact,
                     gas_usd=None, note="Solana network fee is typically well under $0.01")


EVM_SOURCES: list[tuple[str, Callable]] = [
    ("ParaSwap", quote_paraswap), ("KyberSwap", quote_kyber), ("CoW Swap", quote_cow), ("Li.Fi", quote_lifi),
]
SOLANA_SOURCES: list[tuple[str, Callable]] = [("Jupiter", quote_jupiter), ("Raydium", quote_raydium)]


def sources_for(chain_key: str) -> list[tuple[str, Callable]]:
    return SOLANA_SOURCES if CHAINS[chain_key].kind == "solana" else EVM_SOURCES


async def fetch_quotes(c: httpx.AsyncClient, chain_key: str, sell: Token, buy: Token,
                       amount: float) -> tuple[list[SwapQuote], list[str]]:
    """Query every source for the chain in parallel. Returns (quotes sorted best rate first,
    names of sources that failed). Never raises; an empty quote list means every source failed."""
    chain = CHAINS[chain_key]
    srcs = sources_for(chain_key)
    results = await asyncio.gather(*(fn(c, chain, sell, buy, amount) for _, fn in srcs),
                                   return_exceptions=True)
    quotes, failed = [], []
    for (name, _), res in zip(srcs, results):
        if isinstance(res, SwapQuote) and res.amount_out > 0:
            quotes.append(res)
        else:
            log.warning("swap quote %s failed: %r", name, res)
            failed.append(name)
    quotes.sort(key=lambda q: q.amount_out, reverse=True)
    return quotes, failed


async def fetch_best_rate(c: httpx.AsyncClient, chain_key: str, sell: Token, buy: Token,
                          amount: float = 1.0) -> SwapQuote:
    """Best quote across sources, for alert polling. Raises FetchError when no source responds."""
    quotes, _ = await fetch_quotes(c, chain_key, sell, buy, amount)
    if not quotes:
        raise FetchError(f"No swap source could quote {sell.symbol} → {buy.symbol} right now.")
    return quotes[0]
