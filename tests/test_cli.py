"""Terminal mode: dispatch of slash commands and plain English, HTML stripping, and the REPL loop.
All offline (mocked HTTP)."""
import httpx

from cryptospeed import cli as C
from cryptospeed.store import AlertStore


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_strip_html():
    assert C.strip_html("<b>bold</b> &lt;x&gt; &amp; <i>it</i>") == "bold <x> & it"
    assert C.strip_html('a\n<code>bc</code>\n<a href="u">d</a>') == "a\nbc\nd"


async def test_dispatch_help_and_unknown(tmp_path):
    st = AlertStore(str(tmp_path / "c.db"))
    c = _client(lambda r: httpx.Response(500))
    out = await C.dispatch("/help", c, st)
    assert "/swap" in out and "/fees" in out
    out = await C.dispatch("/frobnicate", c, st)
    assert "Unknown command /frobnicate" in out
    assert await C.dispatch("", c, st) == ""
    assert await C.dispatch("exit", c, st) is None
    assert await C.dispatch("/quit", c, st) is None


async def test_dispatch_alert_roundtrip_offline(tmp_path):
    st = AlertStore(str(tmp_path / "c.db"))
    c = _client(lambda r: httpx.Response(500))
    out = await C.dispatch("/alert btc 5", c, st)
    assert "Alert #1" in out
    out = await C.dispatch("/alerts", c, st)
    assert "≤ 5 sat/vB" in out
    out = await C.dispatch("/unalert", c, st)
    assert "Cleared 1" in out
    # plain English goes through the same NLU as Telegram
    out = await C.dispatch("tell me when eth gas under 1", c, st)
    assert "Alert #" in out and "1 gwei" in out


async def test_dispatch_fees_failure_friendly(tmp_path):
    st = AlertStore(str(tmp_path / "c.db"))
    out = await C.dispatch("/fees btc", _client(lambda r: httpx.Response(503)), st)
    assert out.startswith("⚠️") and "mempool.space" in out


async def test_dispatch_swap(tmp_path):
    def h(req):
        if req.url.host == "api.paraswap.io":
            return httpx.Response(200, json={"priceRoute": {
                "destAmount": "2500000000", "gasCostUSD": "0.10", "srcUSD": "2537.00", "destUSD": "2500.00"}})
        return httpx.Response(503)
    st = AlertStore(str(tmp_path / "c.db"))
    out = await C.dispatch("/swap 1 eth usdc", _client(h), st)
    assert "Best: <b>ParaSwap</b>" in out and "2,500 USDC" in out
    out = await C.dispatch("/swap", _client(h), st)
    assert out.startswith("Usage:")
    out = await C.dispatch("/swapalert eth usdc 4000", _client(h), st)
    assert "Swap alert #1" in out
    out = await C.dispatch("/alerts", _client(h), st)
    assert "1 ETH ≥ 4,000 USDC" in out


async def test_repl_loop_prints_plain_text(tmp_path):
    st = AlertStore(str(tmp_path / "c.db"))
    c = _client(lambda r: httpx.Response(500))
    lines = iter(["/help", "hi", "blorp zzz", "exit"])
    outputs = []

    def fake_input(prompt):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    await C.repl(c, st, fake_input, outputs.append)
    assert outputs[-1] == "bye! 👋"
    joined = "\n".join(outputs)
    assert "/swap <amount> <from> <to> [chain]" in joined        # HTML entities unescaped
    assert "<b>" not in joined and "&lt;" not in joined          # tags stripped
    assert "CryptoSpeed" in joined                               # greeting answered
    assert "didn't catch" in joined                              # fallback answered


async def test_repl_survives_dispatch_crash(tmp_path, monkeypatch):
    st = AlertStore(str(tmp_path / "c.db"))
    c = _client(lambda r: httpx.Response(500))

    async def boom(*a, **kw):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(C.B, "respond_to_text", boom)
    lines = iter(["anything", "exit"])
    outputs = []
    await C.repl(c, st, lambda p: next(lines), outputs.append)
    assert any("Something went wrong" in o for o in outputs)
    assert outputs[-1] == "bye! 👋"
