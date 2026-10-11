"""Offline tests: formatting, parsing, store, fetcher error handling, and command handlers (no network, no token).
Numbers here are fixtures for testing the formatter only; the bot itself only shows live API values."""
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from cryptospeed import fetchers as F
from cryptospeed import formatting as T
from cryptospeed import bot as B
from cryptospeed.store import AlertStore


def test_num():
    assert T._num(1.0) == "1"
    assert T._num(1.5) == "1.5"
    assert T._num(0.4313) == "0.431"
    assert T._num(12345.678) == "12,345.68"


def test_btc_format():
    f = F.BtcFees(fastest=12, half_hour=8, hour=5, economy=2, minimum=1, fetched_at=0)
    s = T.format_btc_fees(f)
    assert "12 sat/vB" in s and "8 sat/vB" in s and "5 sat/vB" in s
    assert "1692 sats" in s.replace(",", "")  # 12 * 141


def test_eth_format_with_and_without_usd():
    g = F.EthGas(base_fee_gwei=10, priority_fee_gwei=2, low_priority_fee_gwei=1, source="x", eth_usd=2000, fetched_at=0)
    assert g.transfer_cost_eth == pytest.approx(12 * 21000 / 1e9)
    s = T.format_eth_gas(g)
    assert "10 gwei" in s and "12 gwei" in s and "$0.504" in s
    g.eth_usd = None
    assert "USD estimate unavailable" in T.format_eth_gas(g)


def test_price_format():
    s = T.format_price(F.Price("bitcoin", 50000.5, -1.234, "CoinGecko", fetched_at=0))
    assert "$50,000.50" in s and "-1.23%" in s and "📉" in s


@pytest.mark.parametrize("coin,needle", [("btc", "Lightning"), ("bitcoin", "Lightning"), ("eth", "Arbitrum"),
                                         ("base", "Optimism"), ("sol", "400 ms"), ("ltc", "2.5 min"),
                                         ("doge", "General tips"), (None, "Usage")])
def test_speed_tips(coin, needle):
    assert needle in T.speed_tips(coin)


def test_stuck_mentions_methods():
    for k in ("RBF", "CPFP", "same nonce", "https://mempool.space/tx/", "seed phrase"):
        assert k in T.STUCK_TEXT


def test_help_lists_all_commands():
    for c in ("/fees", "/speed", "/stuck", "/price", "/alert", "/alerts", "/unalert"):
        assert c in T.HELP_TEXT


def test_parse_alert_args():
    assert T.parse_alert_args(["btc", "5"]) == ("btc", 5.0)
    assert T.parse_alert_args(["ETH", "0,5"]) == ("eth", 0.5)
    for bad in ([], ["btc"], ["sol", "1"], ["btc", "abc"], ["btc", "-1"], ["btc", "0"]):
        with pytest.raises(ValueError):
            T.parse_alert_args(bad)


def test_store(tmp_path):
    st = AlertStore(str(tmp_path / "a.db"))
    a = st.add(1, "btc", 5)
    st.add(1, "eth", 1.5)
    st.add(2, "btc", 3)
    assert [x[1] for x in st.list_for_chat(1)] == ["btc", "eth"]
    st.delete(a)
    assert len(st.list_for_chat(1)) == 1
    assert st.clear_chat(1) == 1 and st.list_for_chat(1) == []
    assert "no active" in T.format_alerts([])
    assert "≤ 3 sat/vB" in T.format_alerts(st.list_for_chat(2))


# ---------- fetchers with a mock transport (no network)
def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_btc_fetch_parses_and_fails_gracefully():
    ok = _client(lambda r: httpx.Response(200, json={"fastestFee": 9, "halfHourFee": 7, "hourFee": 4,
                                                      "economyFee": 2, "minimumFee": 1}))
    f = await F.fetch_btc_fees(ok)
    assert (f.fastest, f.hour) == (9, 4)
    bad = _client(lambda r: httpx.Response(503))
    with pytest.raises(F.FetchError, match="mempool.space"):
        await F.fetch_btc_fees(bad)


async def test_eth_fetch_falls_back_between_rpcs():
    calls = []

    def h(req):
        calls.append(str(req.url))
        if "publicnode" in str(req.url):
            return httpx.Response(500)
        body = json.loads(req.content)
        assert body["method"] == "eth_feeHistory"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {
            "baseFeePerGas": [hex(10**9)] * 10 + [hex(3 * 10**9)],
            "reward": [[hex(10**8), hex(2 * 10**9)]] * 10}})
    g = await F.fetch_eth_gas(_client(h), with_usd=False)
    assert g.base_fee_gwei == 3 and g.priority_fee_gwei == 2 and g.low_priority_fee_gwei == 0.1
    assert "drpc" in g.source and len(calls) == 2


async def test_eth_all_rpcs_down():
    with pytest.raises(F.FetchError):
        await F.fetch_eth_gas(_client(lambda r: httpx.Response(502)), with_usd=False)


async def test_price_coingecko_then_coinbase_fallback():
    F._price_cache.clear()

    def h(req):
        if "coingecko" in req.url.host:
            return httpx.Response(429)
        return httpx.Response(200, json={"data": {"amount": "123.45"}})
    p = await F.fetch_price(_client(h), "sol")
    assert p.usd == 123.45 and "Coinbase" in p.source
    F._price_cache.clear()
    with pytest.raises(F.FetchError):  # no coinbase mapping for this id, coingecko down
        await F.fetch_price(_client(lambda r: httpx.Response(429)), "some-obscure-id")


# ---------- command handlers with fake Update/Context
class FakeMsg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


def make_update_ctx(args, tmp_path, http=None):
    msg = FakeMsg()
    upd = SimpleNamespace(effective_message=msg, effective_chat=SimpleNamespace(id=42))
    ctx = SimpleNamespace(args=args, bot_data={"store": AlertStore(str(tmp_path / "h.db")), "http": http})
    return upd, ctx, msg


async def test_handlers_text(tmp_path, monkeypatch):
    async def fake_btc(c):
        return F.BtcFees(4, 3, 2, 1, 1, fetched_at=time.time())
    monkeypatch.setattr(F, "fetch_btc_fees", fake_btc)

    upd, ctx, msg = make_update_ctx(["btc"], tmp_path)
    await B.cmd_fees(upd, ctx)
    assert "4 sat/vB" in msg.sent[-1]

    ctx.args = ["doge"]
    await B.cmd_fees(upd, ctx)
    assert msg.sent[-1].startswith("Usage")

    ctx.args = ["btc", "3"]
    await B.cmd_alert(upd, ctx)
    assert "Alert #1 set" in msg.sent[-1]
    await B.cmd_alerts(upd, ctx)
    assert "≤ 3 sat/vB" in msg.sent[-1]
    await B.cmd_unalert(upd, ctx)
    assert "Cleared 1" in msg.sent[-1]

    await B.cmd_help(upd, ctx)
    assert "/fees" in msg.sent[-1]
    ctx.args = ["eth"]
    await B.cmd_speed(upd, ctx)
    assert "Arbitrum" in msg.sent[-1]


async def test_handler_reports_fetch_error(tmp_path, monkeypatch):
    async def boom(c, **kw):
        raise F.FetchError("Couldn't reach any public Ethereum RPC")
    monkeypatch.setattr(F, "fetch_eth_gas", boom)
    upd, ctx, msg = make_update_ctx(["eth"], tmp_path)
    await B.cmd_fees(upd, ctx)
    assert msg.sent[-1].startswith("⚠️")


async def test_alert_job_fires_once(tmp_path, monkeypatch):
    async def fake_btc(c):
        return F.BtcFees(2, 1, 1, 1, 1)
    monkeypatch.setattr(F, "fetch_btc_fees", fake_btc)
    sent = []

    async def send_message(chat_id, text, **kw):
        sent.append((chat_id, text))
    store = AlertStore(str(tmp_path / "j.db"))
    store.add(7, "btc", 3)    # should fire (2 <= 3)
    store.add(8, "btc", 1)    # should not fire
    ctx = SimpleNamespace(bot=SimpleNamespace(send_message=send_message), bot_data={"store": store, "http": None})
    await B.check_alerts(ctx)
    assert len(sent) == 1 and sent[0][0] == 7 and "2 sat/vB" in sent[0][1]
    assert [a[1] for a in store.all()] == [8]
