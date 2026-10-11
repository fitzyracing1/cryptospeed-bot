"""Live self-test: exercises every fetcher against the real APIs. No Telegram token required."""
from __future__ import annotations

import re

from . import fetchers as F
from . import formatting as T


def _plain(html_text: str) -> str:
    return re.sub(r"<[^>]+>", "", html_text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


async def run() -> int:
    results: list[tuple[str, bool, str]] = []
    async with F.make_client() as c:
        async def check(name, coro, fmt):
            try:
                obj = await coro
                raw = repr(obj)
                print(f"\n=== {name}: OK ===\n{_plain(fmt(obj))}\nraw: {raw[:400]}{'…' if len(raw) > 400 else ''}")
                results.append((name, True, ""))
            except F.FetchError as e:
                print(f"\n=== {name}: FAILED ===\n{e}")
                results.append((name, False, str(e)))

        await check("BTC fees (mempool.space)", F.fetch_btc_fees(c), T.format_btc_fees)
        await check("ETH gas (public RPC + CoinGecko USD)", F.fetch_eth_gas(c), T.format_eth_gas)
        # Each ETH RPC individually, to report which ones work.
        for url in F.ETH_RPC_URLS:
            try:
                g = await F._eth_gas_from(c, url)
                print(f"   RPC {url}: OK base={g.base_fee_gwei:.4f} gwei tip={g.priority_fee_gwei:.4f} gwei")
            except Exception as e:  # noqa: BLE001
                print(f"   RPC {url}: FAILED ({type(e).__name__}: {e})")
        for coin in ("btc", "eth", "sol", "ltc"):
            await check(f"Price {coin} (CoinGecko)", F.fetch_price(c, coin), T.format_price)

        async def batch():
            got = await F.fetch_prices(c, ["solana", "dogecoin", "ripple"])
            if len(got) != 3:
                raise F.FetchError(f"batch returned {len(got)}/3 coins")
            return list(got.values())
        await check("Batch price sol+doge+xrp (CoinGecko, 1 request)", batch(), lambda ps: T.format_prices(ps, []))

        async def search():
            out = []
            for term in ("near", "bonk"):
                r = await F.search_coin(c, term)
                if not r:
                    raise F.FetchError(f"search found nothing for {term}")
                out.append(r)
            return out
        await check("Coin search near/bonk (CoinGecko /search)", search(), lambda rs: "\n".join(f"{t} -> {r}" for t, r in zip(("near", "bonk"), rs)))

        # Live tx lookups using real, current transaction ids pulled from the chains themselves.
        async def btc_confirmed():
            h = (await c.get("https://mempool.space/api/blocks/tip/hash")).text.strip()
            txid = (await c.get(f"https://mempool.space/api/block/{h}/txid/1")).text.strip()
            st = await F.lookup_btc_tx(c, txid)
            if not (st.found and st.confirmed):
                raise F.FetchError("expected a confirmed tx")
            return st
        await check("BTC tx lookup, confirmed (mempool.space)", btc_confirmed(), T.format_tx_status)

        async def btc_pending():
            txid = (await c.get("https://mempool.space/api/mempool/recent")).json()[0]["txid"]
            st = await F.lookup_btc_tx(c, txid)
            if not st.found:
                raise F.FetchError("recent mempool tx not found")
            return st
        await check("BTC tx lookup, mempool (mempool.space)", btc_pending(), T.format_tx_status)

        async def eth_tx():
            blk = await F._rpc(c, F.ETH_RPC_URLS[0], "eth_getBlockByNumber", ["latest", False])
            # use a tx from a few blocks back so it has >1 confirmation
            n = int(blk["number"], 16) - 3
            blk = await F._rpc(c, F.ETH_RPC_URLS[0], "eth_getBlockByNumber", [hex(n), False])
            st = await F.lookup_eth_tx(c, blk["transactions"][0])
            if not (st.found and st.confirmed):
                raise F.FetchError("expected an included tx")
            return st
        await check("ETH tx lookup (public RPC receipt + blockNumber)", eth_tx(), T.format_tx_status)

        async def eth_missing():
            st = await F.lookup_eth_tx(c, "0x" + "ab" * 32)
            if st.found:
                raise F.FetchError("fake hash unexpectedly found")
            return st
        await check("ETH tx lookup, nonexistent hash -> friendly not-found", eth_missing(), T.format_tx_status)
        # Fallback price source, tested directly.
        try:
            d = (await c.get(F.COINBASE_SPOT_URL.format(sym="BTC"))).json()
            print(f"\n   Coinbase fallback BTC-USD: OK {d['data']['amount']}")
        except Exception as e:  # noqa: BLE001
            print(f"\n   Coinbase fallback: FAILED ({e!r})")
        # Graceful failure path for an unknown coin.
        try:
            await F.fetch_price(c, "definitely-not-a-coin-xyz")
            print("   unknown-coin handling: unexpected success")
        except F.FetchError as e:
            print(f"   unknown-coin handling: OK -> friendly message: {e}")

        # Own-wallet watching: well-known PUBLIC addresses only (no keys involved).
        from . import wallets as W
        GENESIS = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"            # Bitcoin genesis-block coinbase address
        DEPOSIT = "0x00000000219ab540356cbb839cbe05303d7705fa"    # Ethereum beacon-chain deposit contract

        def fmt_snap(sn):
            ev, _ = W.diff({}, None, sn)  # what a first registration would do: snapshot only, never alert
            first = [e for e in ev if e.kind == "balance_delta"]
            return (f"{sn.address}: balance {T.fmt_amount(sn.balance, sn.chain)}"
                    + (f", unconfirmed {T.fmt_amount(sn.pending, sn.chain)}" if sn.pending else "")
                    + f"\nrecent txs seen: {len(sn.txs)} (incoming: {sum(1 for t in sn.txs if t['amount_in'] > 0)})"
                    + f", tx history source ok: {sn.history_ok}, source: {sn.source}"
                    + f"\nfirst-registration alerts (should be 0 balance-delta): {len(first)}"
                    + (f"\nlatest: {sn.txs[0]['txid']} amount_in={sn.txs[0]['amount_in']} from={sn.txs[0]['from']}" if sn.txs else ""))

        await check("Wallet watch BTC genesis address (mempool.space address + txs)", W.snapshot_btc(c, GENESIS), fmt_snap)

        async def eth_snap():
            sn = await W.snapshot_eth(c, DEPOSIT)
            if not sn.history_ok:
                raise F.FetchError("Blockscout tx history unavailable (balance-only fallback would be used)")
            return sn
        await check("Wallet watch ETH deposit contract (RPC eth_getBalance + Blockscout history)", eth_snap(), fmt_snap)

    ok = sum(1 for _, s, _ in results if s)
    print(f"\nSUMMARY: {ok}/{len(results)} live fetchers returned real data")
    for name, s, err in results:
        print(f"  [{'PASS' if s else 'FAIL'}] {name}{'' if s else ' - ' + err}")
    return 0 if ok == len(results) else 1
