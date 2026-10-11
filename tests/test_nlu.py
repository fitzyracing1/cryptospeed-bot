"""Parser tests across many phrasings, plus offline chat-reply and group-addressing tests."""
from types import SimpleNamespace

import httpx
import pytest

from cryptospeed import bot as B
from cryptospeed import fetchers as F
from cryptospeed.nlu import parse
from cryptospeed.store import AlertStore

FEES = [
    ("btc fees", ["btc"]), ("bitcoin fee", ["btc"]), ("how much to send bitcoin", ["btc"]),
    ("how much does it cost to send btc?", ["btc"]), ("bitcon fess?", ["btc"]), ("sats per vbyte now?", ["btc"]),
    ("fastest bitcoin fee", ["btc"]), ("is it a good time to send btc", ["btc"]), ("mempool status", ["btc"]),
    ("gas price", ["eth"]), ("eth gas", ["eth"]), ("what are eth fees like rn", ["eth"]),
    ("what is the etherum gass price", ["eth"]), ("how much does it cost to transfer ethereum", ["eth"]),
    ("gwei now", ["eth"]), ("ethereum gas fee in usd", ["eth"]), ("current gas fees", ["eth"]),
    ("fees", ["btc", "eth"]), ("network fees today?", ["btc", "eth"]), ("is the network busy", ["btc", "eth"]),
]


@pytest.mark.parametrize("text,chains", FEES)
def test_fees(text, chains):
    i = parse(text)
    assert i.kind == "fees", (text, i)
    assert i.chains == chains


PRICES = [
    ("price of sol and doge", ["solana", "dogecoin"]), ("btc price", ["bitcoin"]), ("doge price", ["dogecoin"]),
    ("whats the prise of etherium", ["ethereum"]), ("how much is xrp", ["ripple"]), ("how much is 1 btc", ["bitcoin"]),
    ("what's ada trading at", ["cardano"]), ("how's solana doing", ["solana"]), ("price of bitcoin cash", ["bitcoin-cash"]),
    ("usd price of litecoin", ["litecoin"]), ("whats the price of kaspa and sui", ["kaspa", "sui"]),
    ("eth", ["ethereum"]), ("btc and eth", ["bitcoin", "ethereum"]), ("ETH, SOL, XRP?", ["ethereum", "solana", "ripple"]),
    ("value of shiba inu", ["shiba-inu"]), ("pepe price pls", ["pepe"]), ("price of ether", ["ethereum"]),
    ("litecion price", ["litecoin"]), ("solana and cardano prices", ["solana", "cardano"]),
]


@pytest.mark.parametrize("text,coins", PRICES)
def test_price(text, coins):
    i = parse(text)
    assert i.kind == "price", (text, i)
    assert i.coins == coins and i.unknown_terms == []


@pytest.mark.parametrize("text,coins,unknown", [
    ("price of near and pepe", ["pepe"], ["near"]), ("price for dot and link", ["polkadot"], ["link"]),
    ("what's the price of jupiter", [], ["jupiter"]), ("render price", [], ["render"]),
])
def test_price_unknown_terms_go_to_search(text, coins, unknown):
    i = parse(text)
    assert i.kind == "price" and i.coins == coins and i.unknown_terms == unknown


def test_price_without_coin_asks():
    assert parse("what's the price?").error


@pytest.mark.parametrize("text,chain,thr", [
    ("tell me when eth gas under 1", "eth", 1.0), ("alert me if btc fees drop below 3", "btc", 3.0),
    ("notify me when gas below 0.5 gwei", "eth", 0.5), ("ping me when fees < 4 sat/vb", "btc", 4.0),
    ("alert when btc fee below 5 sat/vb please", "btc", 5.0), ("let me know when bitcoin fees hit 2", "btc", 2.0),
    ("message me if ethereum gas goes under 0,8", "eth", 0.8), ("alret me when gas is below 2", "eth", 2.0),
    ("remind me when btc is under 6 sats", "btc", 6.0),
])
def test_set_alert(text, chain, thr):
    i = parse(text)
    assert i.kind == "set_alert" and not i.error, (text, i)
    assert i.chains == [chain] and i.threshold == thr


@pytest.mark.parametrize("text", ["alert me under 2", "alert me when btc fees drop", "alert me when bitcoin price below 50000"])
def test_set_alert_incomplete_or_unsupported(text):
    i = parse(text)
    assert i.kind == "set_alert" and i.error and i.threshold is None


@pytest.mark.parametrize("text,kind", [
    ("show my alerts", "list_alerts"), ("what alerts do i have", "list_alerts"), ("alerts", "list_alerts"),
    ("list alerts", "list_alerts"), ("clear my alerts", "clear_alerts"), ("stop alerts", "clear_alerts"),
    ("remove all my notifications", "clear_alerts"), ("delete alerts", "clear_alerts"), ("unalert", "clear_alerts"),
])
def test_alert_management(text, kind):
    assert parse(text).kind == kind


@pytest.mark.parametrize("text,coin", [
    ("my tx is stuck", None), ("trasaction stuck", None), ("speed up my btc transaction", "btc"),
    ("i sent 0.1 btc 2 hours ago and it still hasn't arrived", "btc"), ("eth transfer pending for an hour", "eth"),
    ("how do i bump the fee", None), ("transfer to coinbase taking forever", None),
    ("my bitcoin is unconfirmed", "btc"), ("how to cancel my eth transaction", "eth"), ("rbf or cpfp?", None),
])
def test_stuck(text, coin):
    i = parse(text)
    assert i.kind == "stuck", (text, i)
    assert i.speed_coin == coin


@pytest.mark.parametrize("text,coin", [
    ("how do i send eth faster", "eth"), ("how long does a litecoin transfer take", "ltc"),
    ("fastest way to send bitcoin", "btc"), ("how long for solana to confirm", "sol"), ("lightning", "btc"),
    ("sol fees", "sol"), ("how many confirmations for btc", "btc"), ("send on arbitrum or base?", "eth"),
    ("cheapest way to send usdt", "tether"),
])
def test_speed(text, coin):
    i = parse(text)
    assert i.kind == "speed", (text, i)
    assert i.speed_coin == coin


@pytest.mark.parametrize("text,kind", [
    ("hi", "greeting"), ("hello there", "greeting"), ("helo", "greeting"), ("gm", "greeting"), ("good morning", "greeting"),
    ("hey!", "greeting"), ("thanks!", "thanks"), ("thank you so much", "thanks"), ("thnx", "thanks"), ("ty", "thanks"),
    ("help", "help"), ("what can you do", "help"), ("how does this work?", "help"),
    ("asdfgh", "unknown"), ("what time is it", "unknown"), ("blorp zzz", "unknown"),
    ("here is my seed phrase", "safety"), ("my private key is abc", "safety"), ("can you hold my btc", "safety"),
    ("can you send it for me", "safety"), ("i'll send you my 12 words", "safety"),
])
def test_smalltalk_and_safety(text, kind):
    assert parse(text).kind == kind, (text, parse(text))


def test_tx_detection():
    eth = "0x" + "ab" * 32
    btc = "c" * 64
    i = parse(f"is this done? {eth}")
    assert i.kind == "tx" and i.txs == [("eth", eth)]
    i = parse(f"check {btc.upper()} please")
    assert i.kind == "tx" and i.txs == [("btc", btc)]
    i = parse(f"{eth} and {btc}")
    assert i.txs == [("eth", eth), ("btc", btc)]
    assert parse("0x" + "a" * 40).kind != "tx"  # an address, not a tx hash
    assert parse("a" * 63).kind != "tx"


def test_mention_stripped():
    assert parse("@Simple1990bot btc fees", "Simple1990bot").kind == "fees"


# ---------- chat replies (offline, mocked HTTP)
def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_chat_alert_flow(tmp_path):
    st = AlertStore(str(tmp_path / "c.db"))
    c = _client(lambda r: httpx.Response(500))
    r = await B.respond_to_text("tell me when eth gas under 1", 5, c, st)
    assert "Alert #1" in r and "1 gwei" in r
    assert "≤ 1 gwei" in await B.respond_to_text("show my alerts", 5, c, st)
    assert "Cleared 1" in await B.respond_to_text("clear my alerts", 5, c, st)
    assert "didn't catch" in await B.respond_to_text("blorp", 5, c, st)
    assert "never" in await B.respond_to_text("here is my seed phrase", 5, c, st)


async def test_chat_fees_failure_is_friendly(tmp_path):
    st = AlertStore(str(tmp_path / "c.db"))
    r = await B.respond_to_text("btc fees", 5, _client(lambda r: httpx.Response(503)), st)
    assert r.startswith("⚠️") and "mempool.space" in r


async def test_chat_price_with_search(tmp_path):
    F._price_cache.clear(); F._search_cache.clear()

    def h(req):
        if req.url.path.endswith("/search"):
            return httpx.Response(200, json={"coins": [
                {"id": "near", "symbol": "NEAR", "name": "NEAR Protocol", "market_cap_rank": 19},
                {"id": "nearly", "symbol": "NEARLY", "name": "NEARLY", "market_cap_rank": 2115}]})
        ids = req.url.params["ids"].split(",")
        return httpx.Response(200, json={i: {"usd": 1.5, "usd_24h_change": 2.0} for i in ids})
    r = await B.respond_to_text("price of sol and near", 5, _client(h), AlertStore(str(tmp_path / "p.db")))
    assert "solana" in r and "near" in r and "$1.50" in r


async def test_chat_btc_tx_pending(tmp_path):
    txid = "d" * 64

    def h(req):
        p = req.url.path
        if p == f"/api/tx/{txid}":
            return httpx.Response(200, json={"fee": 282, "weight": 564, "status": {"confirmed": False}})
        if p.endswith("/fees/recommended"):
            return httpx.Response(200, json={"fastestFee": 5, "halfHourFee": 3, "hourFee": 2, "economyFee": 1, "minimumFee": 1})
        return httpx.Response(404)
    r = await B.respond_to_text(f"where is {txid}", 5, _client(h), AlertStore(str(tmp_path / "t.db")))
    assert "pending" in r and "2 sat/vB" in r and "5 sat/vB" in r and "RBF" in r


def _upd(chat_type, text, entities=(), reply_from=None):
    msg = SimpleNamespace(text=text, entities=list(entities),
                          reply_to_message=SimpleNamespace(from_user=SimpleNamespace(id=reply_from)) if reply_from else None)
    return SimpleNamespace(effective_message=msg, effective_chat=SimpleNamespace(type=chat_type))


def test_group_addressing():
    from telegram.constants import ChatType, MessageEntityType
    assert B._addressed_to_bot(_upd(ChatType.PRIVATE, "btc fees"), 99, "Simple1990bot")
    assert not B._addressed_to_bot(_upd(ChatType.GROUP, "btc fees"), 99, "Simple1990bot")
    ent = SimpleNamespace(type=MessageEntityType.MENTION, offset=0, length=14, user=None)
    assert B._addressed_to_bot(_upd(ChatType.SUPERGROUP, "@Simple1990bot fees", [ent]), 99, "Simple1990bot")
    ent2 = SimpleNamespace(type=MessageEntityType.MENTION, offset=0, length=6, user=None)
    assert not B._addressed_to_bot(_upd(ChatType.GROUP, "@other fees", [ent2]), 99, "Simple1990bot")
    assert B._addressed_to_bot(_upd(ChatType.GROUP, "and eth?", reply_from=99), 99, "Simple1990bot")
    assert not B._addressed_to_bot(_upd(ChatType.GROUP, "and eth?", reply_from=7), 99, "Simple1990bot")
