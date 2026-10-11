"""Swap quotes v0.2.0: parser phrasings, pair resolution, quote comparison with mocked APIs,
formatting, arg parsing, and the swap-alert store + polling job. All offline."""
import httpx
import pytest

from cryptospeed import bot as B
from cryptospeed import fetchers as F
from cryptospeed import formatting as T
from cryptospeed import swaps as S
from cryptospeed.nlu import parse
from cryptospeed.store import AlertStore


# ---------------------------------------------------------------- parser phrasings
SWAP_PHRASES = [
    ("swap 1 eth to usdc", 1.0, "eth", "usdc", None),
    ("swap 0.5 eth for usdc on base", 0.5, "eth", "usdc", "base"),
    ("best rate for 500 usdc to sol", 500.0, "usdc", "sol", None),
    ("convert 2.5 eth into dai", 2.5, "eth", "dai", None),
    ("trade 100 usdt for eth", 100.0, "usdt", "eth", None),
    ("how much usdc for 1 eth", 1.0, "eth", "usdc", None),
    ("swap eth to usdc", 1.0, "eth", "usdc", None),
    ("1 eth to usdc", 1.0, "eth", "usdc", None),
    ("exchange 3 sol for bonk", 3.0, "sol", "bonk", None),
    ("swap 1,000 usdc to eth", 1000.0, "usdc", "eth", None),
    ("eth to usdc rate", 1.0, "eth", "usdc", None),
    ("swap 2 eth -> usdc on arbitrum", 2.0, "eth", "usdc", "arbitrum"),
    ("can you swap my eth to usdc", 1.0, "eth", "usdc", None),
    ("quote for eth/usdc", 1.0, "eth", "usdc", None),
]


@pytest.mark.parametrize("text,amount,frm,to,chain", SWAP_PHRASES)
def test_swap_phrases(text, amount, frm, to, chain):
    i = parse(text)
    assert i.kind == "swap", (text, i)
    assert (i.swap_amount, i.swap_from, i.swap_to, i.swap_chain) == (amount, frm, to, chain)


@pytest.mark.parametrize("text,frm,to,target", [
    ("alert me when eth/usdc hits 4000", "eth", "usdc", 4000.0),
    ("tell me when 1 eth gets 4000 usdc", "eth", "usdc", 4000.0),
    ("notify me when sol/usdc reaches 300", "sol", "usdc", 300.0),
])
def test_swap_alert_phrases(text, frm, to, target):
    i = parse(text)
    assert i.kind == "swap_alert", (text, i)
    assert (i.swap_from, i.swap_to, i.threshold) == (frm, to, target)


@pytest.mark.parametrize("text,kind", [
    # swap vocabulary must NOT swallow the existing intents
    ("tell me when eth gas under 1", "set_alert"),
    ("alert me if btc fees drop below 3", "set_alert"),
    ("price of eth", "price"),
    ("how much is 1 btc", "price"),
    ("how much to send bitcoin", "fees"),
    ("cheapest way to send usdt", "speed"),
    ("btc and eth", "price"),
])
def test_swap_vocab_does_not_break_other_intents(text, kind):
    assert parse(text).kind == kind, (text, parse(text))


# ---------------------------------------------------------------- pair resolution
def test_resolve_pair_chains():
    ck, t_in, t_out, notes = S.resolve_pair("eth", "usdc")
    assert ck == "ethereum" and t_in.symbol == "ETH" and t_out.symbol == "USDC" and not notes
    ck, t_in, t_out, _ = S.resolve_pair("usdc", "sol")       # SOL only exists on Solana
    assert ck == "solana" and t_in.decimals == 6 and t_out.decimals == 9
    ck, _, _, _ = S.resolve_pair("eth", "usdc", "base")
    assert ck == "base"
    ck, _, _, _ = S.resolve_pair("eth", "usdc", "arb")       # chain alias
    assert ck == "arbitrum"
    ck, t_in, _, notes = S.resolve_pair("btc", "eth")        # btc -> wrapped BTC, with a note
    assert ck == "ethereum" and t_in.symbol == "WBTC" and any("WBTC" in n for n in notes)


def test_resolve_pair_errors():
    with pytest.raises(ValueError, match="same token"):
        S.resolve_pair("eth", "ether")
    with pytest.raises(ValueError, match="don't know the chain"):
        S.resolve_pair("eth", "usdc", "dogechain")
    with pytest.raises(ValueError, match="Supported pairs"):
        S.resolve_pair("doge", "usdc")
    with pytest.raises(ValueError, match="Supported pairs"):
        S.resolve_pair("sol", "usdc", "ethereum")            # SOL isn't on Ethereum


# ---------------------------------------------------------------- quote comparison (mocked APIs)
def _mock_quotes_handler(req: httpx.Request) -> httpx.Response:
    host = req.url.host
    if host == "api.paraswap.io":
        return httpx.Response(200, json={"priceRoute": {
            "destAmount": "2500000000", "gasCostUSD": "0.10", "srcUSD": "2537.00", "destUSD": "2500.00"}})
    if host == "aggregator-api.kyberswap.com":
        return httpx.Response(200, json={"code": 0, "data": {"routeSummary": {
            "amountOut": "2510000000", "amountInUsd": "2537", "amountOutUsd": "2505", "gasUsd": "0.40"}}})
    if host == "api.cow.fi":
        return httpx.Response(200, json={"quote": {
            "buyAmount": "2490000000", "sellAmount": "999000000000000000", "feeAmount": "1000000000000000"}})
    if host == "li.quest":
        return httpx.Response(503)  # one source down -> listed under "no answer from"
    return httpx.Response(500)      # ETH RPCs for the gas hint: unavailable -> hint skipped


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_quotes_compares_and_sorts():
    ck, t_in, t_out, _ = S.resolve_pair("eth", "usdc")
    quotes, failed = await S.fetch_quotes(_client(_mock_quotes_handler), ck, t_in, t_out, 1.0)
    assert [q.source for q in quotes] == ["KyberSwap", "ParaSwap", "CoW Swap"]
    assert failed == ["Li.Fi"]
    best = quotes[0]
    assert best.amount_out == pytest.approx(2510.0)
    assert best.rate == pytest.approx(2510.0)
    assert best.gas_usd == pytest.approx(0.40)
    assert best.net_out_usd == pytest.approx(2505 - 0.40)
    assert quotes[1].price_impact_pct == pytest.approx((1 - 2500 / 2537) * 100)
    assert "WETH" in quotes[2].note  # CoW quotes native ETH sells via WETH


async def test_cow_requests_use_dummy_address_never_a_user(monkeypatch):
    seen = {}

    def h(req):
        if req.url.host == "api.cow.fi":
            import json
            seen.update(json.loads(req.content))
            return httpx.Response(200, json={"quote": {"buyAmount": "1"}})
        return httpx.Response(500)
    ck, t_in, t_out, _ = S.resolve_pair("eth", "usdc")
    await S.fetch_quotes(_client(h), ck, t_in, t_out, 1.0)
    assert seen["from"] == S.DUMMY_FROM and seen["kind"] == "sell"


async def test_all_sources_down_is_friendly_and_never_invents_numbers():
    ck, t_in, t_out, _ = S.resolve_pair("eth", "usdc")
    quotes, failed = await S.fetch_quotes(_client(lambda r: httpx.Response(503)), ck, t_in, t_out, 1.0)
    assert quotes == [] and set(failed) == {"ParaSwap", "KyberSwap", "CoW Swap", "Li.Fi"}
    msg = T.format_swap_quotes(1.0, t_in, t_out, ck, quotes, failed)
    assert "None of the quote sources responded" in msg and "try again" in msg
    assert not any(ch.isdigit() and ch != "1" for ch in msg.split("→")[1].split("on")[0])  # no invented output


async def test_solana_sources_jupiter_and_raydium():
    def h(req):
        if req.url.host == "lite-api.jup.ag" and "swap/v1/quote" in req.url.path:
            return httpx.Response(503)  # primary endpoint down -> ultra fallback
        if req.url.host == "lite-api.jup.ag":
            return httpx.Response(200, json={"outAmount": "111800000", "priceImpactPct": "0.0005"})
        if req.url.host == "transaction-v1.raydium.io":
            return httpx.Response(200, json={"success": True, "data": {
                "outputAmount": "111500000", "priceImpactPct": 0.04}})
        return httpx.Response(500)
    ck, t_in, t_out, _ = S.resolve_pair("sol", "usdc")
    quotes, failed = await S.fetch_quotes(_client(h), ck, t_in, t_out, 1.0)
    assert failed == [] and [q.source for q in quotes] == ["Jupiter", "Raydium"]
    assert quotes[0].amount_out == pytest.approx(111.8)
    assert quotes[0].price_impact_pct == pytest.approx(0.05)   # Jupiter fraction -> percent
    assert quotes[1].price_impact_pct == pytest.approx(0.04)   # Raydium is already percent


# ---------------------------------------------------------------- formatting
def test_format_swap_quotes_output():
    ck, t_in, t_out, _ = S.resolve_pair("eth", "usdc")
    q1 = S.SwapQuote("KyberSwap", ck, t_in, t_out, 1.0, 2510.0, price_impact_pct=1.26,
                     gas_usd=0.40, in_usd=2537.0, out_usd=2505.0)
    q2 = S.SwapQuote("ParaSwap", ck, t_in, t_out, 1.0, 2500.0, price_impact_pct=1.46, gas_usd=0.10,
                     in_usd=2537.0, out_usd=2500.0)
    s = T.format_swap_quotes(1.0, t_in, t_out, ck, [q1, q2], ["Li.Fi"], hint="⛽ Gas check: test")
    assert "1 ETH → USDC on Ethereum" in s
    assert "Best: <b>KyberSwap</b>" in s and "2,510 USDC" in s
    assert "price impact ~1.26%" in s and "est. gas ≈ $0.4" in s and "net after gas ≈ $2,504.60" in s
    assert "ParaSwap: 2,500 USDC" in s
    assert "Price impact up to ~1.5%" in s and "slippage" in s      # warning above 1%
    assert "no answer from: Li.Fi" in s
    assert "⛽ Gas check: test" in s
    assert "not predictions or financial advice" in s and "never ask for keys" in s


def test_gas_hint_tiers():
    hist = [10.0] * 100
    assert "🟢" in T.gas_hint(5.0, hist) and "lower than 100%" in T.gas_hint(5.0, hist)
    assert "🔴" in T.gas_hint(20.0, hist)
    mixed = [1.0] * 50 + [10.0] * 50
    assert "🟡" in T.gas_hint(5.0, mixed)
    assert T.gas_hint(5.0, []) == ""


def test_parse_swap_args():
    assert T.parse_swap_args(["1", "eth", "usdc"]) == (1.0, "eth", "usdc", None)
    assert T.parse_swap_args(["1", "eth", "to", "usdc"]) == (1.0, "eth", "usdc", None)
    assert T.parse_swap_args(["0,5", "eth", "usdc", "base"]) == (0.5, "eth", "usdc", "base")
    for bad in ([], ["eth", "usdc"], ["x", "eth", "usdc"], ["-1", "eth", "usdc"], ["0", "eth", "usdc"]):
        with pytest.raises(ValueError):
            T.parse_swap_args(bad)


def test_parse_swapalert_args():
    assert T.parse_swapalert_args(["eth", "usdc", "4000"]) == ("eth", "usdc", 4000.0, None)
    assert T.parse_swapalert_args(["eth", "to", "usdc", "at", "4000", "base"]) == ("eth", "usdc", 4000.0, "base")
    for bad in ([], ["eth", "usdc"], ["eth", "usdc", "x"], ["eth", "usdc", "0"]):
        with pytest.raises(ValueError):
            T.parse_swapalert_args(bad)


def test_help_and_examples_mention_swaps():
    assert "/swap" in T.HELP_TEXT and "/swapalert" in T.HELP_TEXT
    assert "swap 1 eth to usdc" in T.EXAMPLES


# ---------------------------------------------------------------- chat flow end-to-end (offline)
async def test_chat_swap_quote(tmp_path):
    st = AlertStore(str(tmp_path / "s.db"))
    r = await B.respond_to_text("swap 1 eth to usdc", 5, _client(_mock_quotes_handler), st)
    assert "Best: <b>KyberSwap</b>" in r and "2,510 USDC" in r
    assert "not predictions or financial advice" in r
    r = await B.respond_to_text("swap 1 sol to usdc on ethereum", 5, _client(_mock_quotes_handler), st)
    assert "Supported pairs" in r  # SOL isn't an Ethereum token -> friendly pair error


async def test_chat_swap_quote_all_down(tmp_path):
    st = AlertStore(str(tmp_path / "s.db"))
    r = await B.respond_to_text("swap 1 eth to usdc", 5, _client(lambda r: httpx.Response(503)), st)
    assert "None of the quote sources responded" in r


# ---------------------------------------------------------------- swap alerts: store + polling
def test_swap_alert_store(tmp_path):
    st = AlertStore(str(tmp_path / "sa.db"))
    aid = st.add_swap_alert(1, "ethereum", "eth", "usdc", 4000)
    st.add_swap_alert(2, "solana", "sol", "usdc", 300)
    rows = st.swap_alerts_for_chat(1)
    assert rows == [(aid, "ethereum", "eth", "usdc", 4000.0)]
    assert len(st.all_swap_alerts()) == 2
    assert "1 ETH ≥ 4,000 USDC" in T.format_swap_alerts(rows)
    assert st.clear_chat(1) == 1 and st.swap_alerts_for_chat(1) == []
    st.delete_swap_alert(st.swap_alerts_for_chat(2)[0][0])
    assert st.all_swap_alerts() == []


async def test_chat_swap_alert_set_and_listed(tmp_path, monkeypatch):
    async def fake_best(c, ck, t_in, t_out, amount=1.0):
        return S.SwapQuote("ParaSwap", ck, t_in, t_out, 1.0, 2500.0)
    monkeypatch.setattr(S, "fetch_best_rate", fake_best)
    st = AlertStore(str(tmp_path / "sb.db"))
    c = _client(lambda r: httpx.Response(500))
    r = await B.respond_to_text("alert me when eth/usdc hits 4000", 5, c, st)
    assert "Swap alert #1" in r and "≥ 4,000 USDC" in r and "2,500 USDC" in r
    r = await B.respond_to_text("show my alerts", 5, c, st)
    assert "swap-rate alerts" in r.lower() and "1 ETH ≥ 4,000 USDC" in r
    assert "Cleared 1" in await B.respond_to_text("clear my alerts", 5, c, st)


async def test_swap_alert_job_fires_once(tmp_path, monkeypatch):
    st = AlertStore(str(tmp_path / "sj.db"))
    st.add_swap_alert(7, "ethereum", "eth", "usdc", 2400)   # fires: 2500 >= 2400
    st.add_swap_alert(8, "ethereum", "eth", "usdc", 2600)   # stays: 2500 < 2600
    calls = []

    async def fake_best(c, ck, t_in, t_out, amount=1.0):
        calls.append((ck, t_in.symbol, t_out.symbol))
        return S.SwapQuote("ParaSwap", ck, t_in, t_out, 1.0, 2500.0)
    monkeypatch.setattr(S, "fetch_best_rate", fake_best)
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))
    n = await B.poll_swap_alerts_once(None, st, send)
    assert n == 1 and sent[0][0] == 7
    assert "2,500 USDC" in sent[0][1] and "≥ 2,400" in sent[0][1]
    assert len(calls) == 1                                   # quote cached per pair within a pass
    assert [a[0] for a in st.all_swap_alerts()] == [2]       # only the unfired alert remains


async def test_swap_alert_poll_survives_api_failure(tmp_path, monkeypatch):
    st = AlertStore(str(tmp_path / "sf.db"))
    st.add_swap_alert(7, "ethereum", "eth", "usdc", 2400)

    async def boom(c, ck, t_in, t_out, amount=1.0):
        raise F.FetchError("down")
    monkeypatch.setattr(S, "fetch_best_rate", boom)
    assert await B.poll_swap_alerts_once(None, st, None) == 0
    assert len(st.all_swap_alerts()) == 1                    # kept for the next poll
