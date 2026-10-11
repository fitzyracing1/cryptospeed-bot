"""Own-wallet watching: address detection, store add/list/remove, first-snapshot-no-alert, mocked incoming alerts.
All HTTP is mocked; amounts are test fixtures."""
import json

import httpx
import pytest

from cryptospeed import bot as B
from cryptospeed import fetchers as F
from cryptospeed import formatting as T
from cryptospeed import wallets as W
from cryptospeed.addresses import detect_chain, find_addresses, looks_like_private_key, normalize
from cryptospeed.nlu import parse
from cryptospeed.store import AlertStore

BTC_LEGACY = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
BTC_P2SH = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"
BTC_BECH32 = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
BTC_TAPROOT = "bc1p567vvhxrpe28ppdazajpjsgng22sunxlrk0dn3rfuechf2mx828qns8zks"
ETH = "0x00000000219ab540356cBB839Cbe05303d7705Fa"
SENDER = "bc1p567vvhxrpe28ppdazajpjsgng22sunxlrk0dn3rfuechf2mx828qns8zks"


@pytest.mark.parametrize("addr,chain", [(BTC_LEGACY, "btc"), (BTC_P2SH, "btc"), (BTC_BECH32, "btc"),
                                        (BTC_BECH32.upper(), "btc"), (BTC_TAPROOT, "btc"), (ETH, "eth"),
                                        (ETH.lower(), "eth")])
def test_detect_valid(addr, chain):
    assert detect_chain(addr) == chain


@pytest.mark.parametrize("addr", [
    BTC_LEGACY[:-1] + "b",          # bad base58 checksum
    BTC_BECH32[:-1] + "x",          # bad bech32 checksum
    "0x123", "0x" + "g" * 40, "0x" + "a" * 64,  # wrong length / not hex / tx hash
    "tb1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",  # testnet hrp
    "hello", "",
])
def test_detect_invalid(addr):
    assert detect_chain(addr) is None


def test_find_and_normalize():
    found = find_addresses(f"watch {ETH} and {BTC_BECH32.upper()} and {BTC_LEGACY}")
    assert found == [("eth", ETH.lower()), ("btc", BTC_BECH32), ("btc", BTC_LEGACY)]
    assert normalize(BTC_LEGACY, "btc") == BTC_LEGACY  # base58 keeps case


def test_private_key_shapes_refused():
    assert looks_like_private_key("5" + "H" * 50)
    assert looks_like_private_key("xprv" + "A" * 107)
    assert not looks_like_private_key(BTC_LEGACY)
    assert parse("K" + "z" * 51).kind == "safety"


@pytest.mark.parametrize("text,kind,addr,label,target", [
    (f"watch my wallet {ETH}", "add_wallet", ETH.lower(), None, None),
    (f"track this address {BTC_BECH32}", "add_wallet", BTC_BECH32, None, None),
    (f"watch my wallet {BTC_BECH32} as savings", "add_wallet", BTC_BECH32, "savings", None),
    (f"monitor {ETH} called cold storage", "add_wallet", ETH.lower(), "cold storage", None),
    (f"my wallet address is {BTC_LEGACY}", "add_wallet", BTC_LEGACY, None, None),
    (f"stop watching {BTC_BECH32}", "remove_wallet", None, None, BTC_BECH32),
    ("stop watching savings", "remove_wallet", None, None, "savings"),
    ("remove wallet #2", "remove_wallet", None, None, "#2"),
    ("unwatch cold storage", "remove_wallet", None, None, "cold storage"),
    ("forget wallet called trading", "remove_wallet", None, None, "trading"),
    ("list my wallets", "list_wallets", None, None, None),
    ("show my wallets", "list_wallets", None, None, None),
    ("wallets", "list_wallets", None, None, None),
    ("which addresses am i watching", "list_wallets", None, None, None),
    (BTC_BECH32, "address_hint", BTC_BECH32, None, None),
])
def test_wallet_nlu(text, kind, addr, label, target):
    i = parse(text)
    assert i.kind == kind, (text, i)
    if addr:
        assert i.address == addr
    assert i.label == label and i.target == target and not i.error


@pytest.mark.parametrize("text", ["watch my wallet", "remove my wallet", "stop watching"])
def test_wallet_nlu_incomplete(text):
    assert parse(text).error


def test_existing_intents_unaffected():
    assert parse("show my alerts").kind == "list_alerts"
    assert parse("clear my alerts").kind == "clear_alerts"
    assert parse("btc fees").kind == "fees"


# ---------------- store
def test_store_wallets(tmp_path):
    st = AlertStore(str(tmp_path / "w.db"))
    a = st.add_wallet(1, "btc", BTC_BECH32, "savings")
    b = st.add_wallet(1, "eth", ETH.lower(), None)
    st.add_wallet(2, "btc", BTC_BECH32, None)  # another chat may watch the same address
    with pytest.raises(ValueError):
        st.add_wallet(1, "btc", BTC_BECH32, None)
    assert [w.id for w in st.wallets_for_chat(1)] == [a, b]
    assert st.find_wallet(1, "SAVINGS").id == a
    assert st.find_wallet(1, f"#{b}").id == b
    assert st.find_wallet(1, ETH).id == b  # case-insensitive address match
    assert st.find_wallet(2, "savings") is None
    st.save_wallet_state(a, 123, -5, {"t1": 1})
    w = st.find_wallet(1, "savings")
    assert (w.balance, w.pending, w.seen, w.initialized) == (123, -5, {"t1": 1}, True)
    assert st.remove_wallet(1, a) and not st.remove_wallet(1, a)
    assert not st.remove_wallet(1, 999)


def test_wallet_cap(tmp_path):
    st = AlertStore(str(tmp_path / "cap.db"))
    for i in range(10):
        st.add_wallet(1, "eth", "0x" + f"{i:040x}", None)
    with pytest.raises(ValueError, match="max 10"):
        st.add_wallet(1, "eth", "0x" + "f" * 40, None)


# ---------------- mocked chain data
class FakeChain:
    """Mutable fake for mempool.space + ETH RPC + Blockscout + CoinGecko."""
    def __init__(self):
        self.btc_txs = []          # mempool.space /txs format, newest first
        self.btc_funded = 100_000
        self.btc_mempool_funded = 0
        self.eth_wei = 10**18
        self.eth_txs = []          # blockscout items
        self.blockscout_up = True

    def handler(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        if host == "mempool.space" and path.endswith("/txs"):
            return httpx.Response(200, json=self.btc_txs)
        if host == "mempool.space" and path.startswith("/api/address/"):
            return httpx.Response(200, json={
                "chain_stats": {"funded_txo_sum": self.btc_funded, "spent_txo_sum": 0},
                "mempool_stats": {"funded_txo_sum": self.btc_mempool_funded, "spent_txo_sum": 0}})
        if host == "eth.blockscout.com":
            return httpx.Response(200, json={"items": self.eth_txs}) if self.blockscout_up else httpx.Response(503)
        if host == "api.coingecko.com":
            ids = req.url.params["ids"].split(",")
            return httpx.Response(200, json={i: {"usd": 1000.0} for i in ids})
        if req.method == "POST":  # ETH RPC
            body = json.loads(req.content)
            assert body["method"] == "eth_getBalance"
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(self.eth_wei)})
        return httpx.Response(404)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def btc_tx(txid, to_addr, value, confirmed, block=None, sender=SENDER):
    return {"txid": txid, "status": {"confirmed": confirmed, "block_height": block},
            "vin": [{"prevout": {"scriptpubkey_address": sender, "value": value + 1000}}],
            "vout": [{"scriptpubkey_address": to_addr, "value": value}]}


def eth_tx(h, to, value, block, sender="0x" + "1" * 40):
    return {"hash": h, "to": {"hash": to}, "from": {"hash": sender}, "value": str(value),
            "block_number": block, "status": "ok"}


@pytest.fixture(autouse=True)
def _clear_caches():
    F._price_cache.clear()


async def test_btc_first_snapshot_no_alert_then_incoming_and_confirm(tmp_path):
    fc = FakeChain()
    fc.btc_txs = [btc_tx("old1", BTC_BECH32, 100_000, True, 900_000)]  # history: must NOT alert
    st = AlertStore(str(tmp_path / "b.db"))
    http = fc.client()
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    reply = await B.respond_to_text(f"watch my wallet {BTC_BECH32} as savings", 77, http, st)
    assert "Watching BTC address #1" in reply and "0.001 BTC" in reply and "never" in reply
    assert await B.poll_wallets_once(http, st, send) == 0 and sent == []

    # new incoming mempool tx
    fc.btc_txs.insert(0, btc_tx("new1", BTC_BECH32, 250_000, False))
    fc.btc_mempool_funded = 250_000
    assert await B.poll_wallets_once(http, st, send) == 1
    chat, text = sent[-1]
    assert chat == 77 and "Incoming BTC" in text and "+0.0025 BTC" in text and "savings" in text
    assert "unconfirmed" in text and "https://mempool.space/tx/new1" in text and SENDER in text
    assert "≈ $2.50" in text  # 0.0025 * 1000 (mock price)
    # nothing new -> no repeat
    assert await B.poll_wallets_once(http, st, send) == 0
    # it confirms -> one "confirmed" follow-up
    fc.btc_txs[0] = btc_tx("new1", BTC_BECH32, 250_000, True, 900_010)
    fc.btc_funded, fc.btc_mempool_funded = 350_000, 0
    assert await B.poll_wallets_once(http, st, send) == 1
    assert "Incoming BTC confirmed" in sent[-1][1] and "block 900010" in sent[-1][1] and "0.0035 BTC" in sent[-1][1]
    # outgoing tx does not alert
    fc.btc_txs.insert(0, {"txid": "out1", "status": {"confirmed": True, "block_height": 900_011},
                          "vin": [{"prevout": {"scriptpubkey_address": BTC_BECH32, "value": 50_000}}],
                          "vout": [{"scriptpubkey_address": SENDER, "value": 49_000}]})
    assert await B.poll_wallets_once(http, st, send) == 0


async def test_eth_incoming_tx_and_balance_delta(tmp_path):
    fc = FakeChain()
    addr = ETH.lower()
    fc.eth_txs = [eth_tx("0xold", addr, 5 * 10**17, 100)]
    st = AlertStore(str(tmp_path / "e.db"))
    http = fc.client()
    sent = []

    async def send(chat_id, text):
        sent.append(text)

    r = await B.add_wallet_flow(5, ETH, "main", http, st)
    assert "Watching ETH address" in r and "1 ETH" in r
    assert await B.poll_wallets_once(http, st, send) == 0

    fc.eth_txs.insert(0, eth_tx("0xnew", addr, 2 * 10**17, 101))
    fc.eth_wei += 2 * 10**17
    assert await B.poll_wallets_once(http, st, send) == 1
    assert "Incoming ETH" in sent[-1] and "+0.2 ETH" in sent[-1] and "https://etherscan.io/tx/0xnew" in sent[-1]
    assert "1.2 ETH" in sent[-1] and "0x1111111111" in sent[-1]

    # Blockscout down + balance up -> balance-delta alert with honest note
    fc.blockscout_up = False
    fc.eth_wei += 10**17
    assert await B.poll_wallets_once(http, st, send) == 1
    assert "balance increased" in sent[-1] and "+0.1 ETH" in sent[-1] and "unavailable" in sent[-1]
    # balance down -> nothing
    fc.eth_wei -= 5 * 10**17
    assert await B.poll_wallets_once(http, st, send) == 0


async def test_snapshot_deferred_when_api_down(tmp_path):
    st = AlertStore(str(tmp_path / "d.db"))
    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    r = await B.add_wallet_flow(1, BTC_LEGACY, None, down, st)
    assert "Couldn't read the current balance yet" in r
    fc = FakeChain()
    fc.btc_txs = [btc_tx("hist", BTC_LEGACY, 5000, True, 1)]
    sent = []

    async def send(c, t):
        sent.append(t)
    # first successful poll only snapshots
    assert await B.poll_wallets_once(fc.client(), st, send) == 0 and sent == []
    assert st.wallets_for_chat(1)[0].initialized


async def test_add_list_remove_via_commands_text(tmp_path):
    fc = FakeChain()
    st = AlertStore(str(tmp_path / "c.db"))
    http = fc.client()
    assert "not a valid" in (await B.add_wallet_flow(1, "0x123", None, http, st)).replace("doesn't look like a valid", "not a valid")
    await B.add_wallet_flow(1, BTC_BECH32, "savings", http, st)
    assert "already watching" in await B.add_wallet_flow(1, BTC_BECH32.upper(), None, http, st)
    lst = await B.respond_to_text("list my wallets", 1, http, st)
    assert "savings" in lst and "0.001 BTC" in lst
    assert "couldn't find" in await B.respond_to_text("stop watching nothing-here", 1, http, st)
    assert "Stopped watching" in await B.respond_to_text("stop watching savings", 1, http, st)
    assert "not watching any" in T.format_wallets(st.wallets_for_chat(1))


def test_help_mentions_wallet_safety():
    assert "/addwallet" in T.HELP_TEXT and "never search" in T.HELP_TEXT
    assert "never search for other people's wallets" in T.WATCH_SAFETY


def test_diff_pure():
    snap = W.Snapshot("btc", BTC_BECH32, 0, 0, [{"txid": "a", "confirmed": True, "block": 1, "amount_in": 10, "from": "x"}])
    ev, seen = W.diff({}, None, snap)
    assert len(ev) == 1 and seen == {"a": 1}
    ev, _ = W.diff(seen, 0, snap)
    assert ev == []
