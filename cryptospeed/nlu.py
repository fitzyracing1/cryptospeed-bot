"""Rule-based natural-language intent parser. No LLM and no API key: keyword rules, regexes, and
difflib fuzzy matching for typos. It's pure (no network), so it's fully unit-testable.

Coins the parser can't map are returned in `unknown_terms`, and the bot resolves them via CoinGecko search.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

from .addresses import find_addresses, looks_like_private_key
from .swaps import ALL_SWAP_WORDS

# ---------------------------------------------------------------- coin vocabulary
# word -> CoinGecko id. Ambiguous English words (op, near, one, link, ton, gas, sand...) are deliberately NOT here;
# they only become coins through CoinGecko search when the message is clearly a price question.
COIN_WORDS: dict[str, str] = {
    "btc": "bitcoin", "bitcoin": "bitcoin", "bitcoins": "bitcoin", "xbt": "bitcoin",
    "eth": "ethereum", "ether": "ethereum", "ethereum": "ethereum", "ethe": "ethereum",
    "sol": "solana", "solana": "solana",
    "ltc": "litecoin", "litecoin": "litecoin",
    "doge": "dogecoin", "dogecoin": "dogecoin",
    "xrp": "ripple", "ripple": "ripple",
    "ada": "cardano", "cardano": "cardano",
    "usdt": "tether", "tether": "tether", "usdc": "usd-coin",
    "bnb": "binancecoin", "trx": "tron", "tron": "tron",
    "dot": "polkadot", "polkadot": "polkadot",
    "avax": "avalanche-2", "avalanche": "avalanche-2",
    "matic": "matic-network", "polygon": "polygon-ecosystem-token", "pol": "polygon-ecosystem-token",
    "shib": "shiba-inu", "shiba": "shiba-inu", "pepe": "pepe",
    "chainlink": "chainlink", "bch": "bitcoin-cash", "xlm": "stellar", "stellar": "stellar",
    "xmr": "monero", "monero": "monero", "atom": "cosmos", "cosmos": "cosmos",
    "arbitrum": "arbitrum", "optimism": "optimism", "toncoin": "the-open-network",
    "sui": "sui", "apt": "aptos", "aptos": "aptos", "uni": "uniswap", "uniswap": "uniswap",
    "hbar": "hedera-hashgraph", "hedera": "hedera-hashgraph", "kas": "kaspa", "kaspa": "kaspa",
    "etc": "ethereum-classic", "fil": "filecoin", "filecoin": "filecoin", "icp": "internet-computer",
    "bonk": "bonk", "wif": "dogwifcoin", "trump": "official-trump",
}
MULTIWORD_COINS = {"bitcoin cash": "bitcoin-cash", "ethereum classic": "ethereum-classic",
                   "shiba inu": "shiba-inu", "internet computer": "internet-computer", "the open network": "the-open-network"}

BTC_WORDS = {"btc", "bitcoin", "bitcoins", "xbt", "sat", "sats", "satoshi", "satoshis", "vb", "vbyte", "vbytes",
             "mempool", "lightning"}
ETH_WORDS = {"eth", "ether", "ethereum", "gas", "gwei", "erc20", "metamask"}
L2_WORDS = {"arbitrum", "base", "optimism", "arb", "op", "l2", "layer2", "zksync", "polygon"}

# ---------------------------------------------------------------- keyword vocab (for typo correction)
KEYWORDS = {
    "price", "prices", "worth", "value", "trading", "quote", "rate", "usd", "dollar", "dollars",
    "fee", "fees", "gas", "gwei", "cost", "costs", "expensive", "cheap", "mempool",
    "fast", "faster", "fastest", "quick", "quicker", "quickly", "speed", "slow", "confirm", "confirmed",
    "confirmation", "confirmations", "confirming", "stuck", "pending", "unconfirmed", "accelerate", "bump",
    "replace", "cancel", "transaction", "transactions", "transfer", "alert", "alerts", "notify",
    "notification", "notifications", "remind", "below", "under", "drops", "lower", "clear", "remove",
    "delete", "list", "show", "unalert", "hello", "wallet", "wallets", "watch", "track", "monitor", "thanks", "thank", "help", "commands", "lightning", "network",
}
# Common words we never "correct" (avoids send->sand style mistakes).
COMMON = {
    "send", "sent", "sending", "much", "what", "whats", "when", "where", "which", "show", "time", "have", "there",
    "then", "this", "that", "with", "from", "your", "mine", "please", "about", "would", "could", "should", "will",
    "tell", "know", "need", "want", "like", "make", "does", "today", "right", "doing", "going", "good", "best",
    "long", "take", "takes", "many", "some", "drop", "goes", "gets", "under", "over", "into", "just", "money",
    "coin", "coins", "crypto", "now", "them", "they", "here", "hours", "hour", "minutes", "message", "ping",
    "less", "than", "below", "above", "hits", "reach", "reaches", "falls", "fall", "lets", "okay", "cool",
    "sure", "base", "near", "taking", "making", "seed", "phrase", "wallet", "words", "hold", "keep", "store",
    "safe", "address", "account", "exchange", "forever", "ages", "receive", "received", "chat", "work", "works", "hows", "back", "wait", "waiting", "days", "still",
}
_VOCAB = sorted(KEYWORDS | {w for w in COIN_WORDS if len(w) >= 4} | {"hello", "thanks"})

STOPWORDS = (COMMON - {"near", "base"}) | {
    "the", "a", "an", "of", "for", "and", "or", "is", "are", "to", "in", "on", "at", "me", "my", "i", "you",
    "it", "its", "be", "do", "how", "whats", "what's", "price", "prices", "worth", "value", "current", "currently",
    "rn", "pls", "plz", "check", "get", "give", "quote", "usd", "dollar", "dollars", "trading", "today",
    "vs", "versus", "much", "is", "was", "of", "per", "one", "hey", "hi", "bot", "skill", "can", "u", "ur",
    "also", "too", "both", "rate", "market", "cap", "up", "down", "now", "at", "doing", "lookup", "look",
    "and", "plus", "&", "with", "x", "?",
}

GREETING = {"hi", "hello", "hey", "heya", "hiya", "yo", "sup", "gm", "howdy", "hola", "morning", "evening", "greetings"}
THANKS = {"thanks", "thank", "thx", "thnx", "thanx", "tnx", "ty", "tysm", "cheers", "appreciate", "appreciated"}

# ---------------------------------------------------------------- swap quotes (read-only)
_SWAP_TOK = r"(?:%s)" % "|".join(sorted(ALL_SWAP_WORDS, key=len, reverse=True))
_SWAP_AMT = r"\d+(?:\.\d+)?"
_SWAP_SEP = r"(?:\s+(?:to|for|into)\s+|\s*(?:->|/)\s*)"
SWAP_VERB_RE = re.compile(
    rf"\b(?:swap|swapping|swaping|convert|converting|trade|trading|exchange|exchanging)\s+"
    rf"(?:my\s+|all\s+)?(?:({_SWAP_AMT})\s*)?({_SWAP_TOK})\b{_SWAP_SEP}({_SWAP_TOK})\b")
SWAP_PAIR_AMT_RE = re.compile(rf"\b({_SWAP_AMT})\s*({_SWAP_TOK})\b{_SWAP_SEP}({_SWAP_TOK})\b")
SWAP_HOWMUCH_RE = re.compile(
    rf"\bhow (?:much|many)\s+({_SWAP_TOK})\s+(?:for|per)\s+(?:({_SWAP_AMT})\s*)?({_SWAP_TOK})\b")
SWAP_RATE_PAIR_RE = re.compile(rf"\b({_SWAP_TOK})\b{_SWAP_SEP}({_SWAP_TOK})\b")
SWAP_RATE_WORD_RE = re.compile(r"\b(?:rate|rates|quote|quotes|swap|convert|exchange|price)\b")
SWAP_CHAIN_RE = re.compile(r"\b(?:on|via|using)\s+(ethereum|mainnet|l1|base|arbitrum|arb|solana|sol)\b")
SWAP_ALERT_WORD_RE = re.compile(r"\b(?:alert|notify|notification|remind|ping|tell me|let me know|message me|warn me)\b")
SWAP_ALERT_GETS_RE = re.compile(
    rf"\b(?:({_SWAP_AMT})\s*)?({_SWAP_TOK})\s+(?:gets?|buys?|is worth|fetches|hits?|reaches)\s+({_SWAP_AMT})\s*({_SWAP_TOK})\b")
SWAP_ALERT_PAIR_RE = re.compile(
    rf"\b({_SWAP_TOK})\s*(?:/|->|\s+to\s+|\s+vs\s+)\s*({_SWAP_TOK})\b\D{{0,40}}?({_SWAP_AMT})")


def _swap_norm(text: str) -> str:
    t = text.lower().replace("→", "->").replace("’", "'")
    t = re.sub(r"(\d),(\d{3})\b", r"\1\2", t)      # 1,000 -> 1000
    t = re.sub(r"(\d),(\d{1,2})\b", r"\1.\2", t)   # 0,5 -> 0.5
    return t


def _swap_intent(text: str) -> Optional[Intent]:
    t = _swap_norm(text)
    chain_m = SWAP_CHAIN_RE.search(t)
    chain = chain_m.group(1) if chain_m else None
    if SWAP_ALERT_WORD_RE.search(t):
        m = SWAP_ALERT_GETS_RE.search(t)
        if m and m.group(2) != m.group(4):
            amount = float(m.group(1)) if m.group(1) else 1.0
            return Intent("swap_alert", swap_amount=amount, swap_from=m.group(2), swap_to=m.group(4),
                          swap_chain=chain, threshold=float(m.group(3)))
        m = SWAP_ALERT_PAIR_RE.search(t)
        if m and m.group(1) != m.group(2):
            return Intent("swap_alert", swap_amount=1.0, swap_from=m.group(1), swap_to=m.group(2),
                          swap_chain=chain, threshold=float(m.group(3)))
        return None  # alert wording without a token pair -> fee-alert logic handles it
    m = SWAP_VERB_RE.search(t)
    if m and m.group(2) != m.group(3):
        return Intent("swap", swap_amount=float(m.group(1)) if m.group(1) else 1.0,
                      swap_from=m.group(2), swap_to=m.group(3), swap_chain=chain)
    m = SWAP_PAIR_AMT_RE.search(t)
    if m and m.group(2) != m.group(3):
        return Intent("swap", swap_amount=float(m.group(1)), swap_from=m.group(2), swap_to=m.group(3),
                      swap_chain=chain)
    m = SWAP_HOWMUCH_RE.search(t)
    if m and m.group(1) != m.group(3):
        return Intent("swap", swap_amount=float(m.group(2)) if m.group(2) else 1.0,
                      swap_from=m.group(3), swap_to=m.group(1), swap_chain=chain)
    m = SWAP_RATE_PAIR_RE.search(t)
    if m and m.group(1) != m.group(2) and SWAP_RATE_WORD_RE.search(t):
        return Intent("swap", swap_amount=1.0, swap_from=m.group(1), swap_to=m.group(2), swap_chain=chain)
    return None


BTC_TXID_RE = re.compile(r"(?<![0-9a-fA-Fx])([0-9a-fA-F]{64})(?![0-9a-fA-F])")
ETH_TX_RE = re.compile(r"\b(0x[0-9a-fA-F]{64})(?![0-9a-fA-F])")
NUM_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)(?![\w.])|(?<![\w.])(\.\d+)(?![\w.])")


@dataclass
class Intent:
    kind: str                       # tx | safety | fees | price | speed | stuck | set_alert | list_alerts | clear_alerts | help | greeting | thanks | unknown
    chains: list[str] = field(default_factory=list)     # for fees/alerts: subset of ["btc","eth"]
    coins: list[str] = field(default_factory=list)      # CoinGecko ids, in mention order
    unknown_terms: list[str] = field(default_factory=list)  # possible coins to resolve via search (price intent)
    threshold: Optional[float] = None
    txs: list[tuple[str, str]] = field(default_factory=list)  # (chain_guess, hash)
    speed_coin: Optional[str] = None  # btc/eth/sol/ltc/other
    address: Optional[str] = None     # wallet watching: public address
    label: Optional[str] = None       # wallet watching: optional label
    target: Optional[str] = None      # remove_wallet: id / label / address
    error: Optional[str] = None       # user-facing hint when an intent is recognised but incomplete
    swap_amount: Optional[float] = None   # swap / swap_alert: amount of the from-token
    swap_from: Optional[str] = None       # swap / swap_alert: from-token word as typed
    swap_to: Optional[str] = None         # swap / swap_alert: to-token word
    swap_chain: Optional[str] = None      # swap / swap_alert: chain word ("base", "solana", ...) or None


def _tokenize(text: str) -> list[str]:
    t = text.lower().replace("’", "'")
    t = re.sub(r"(\d),(\d{3})\b", r"\1\2", t)      # 50,000 -> 50000
    t = re.sub(r"(\d),(\d{1,2})\b", r"\1.\2", t)   # 0,8 -> 0.8 (decimal comma)
    t = re.sub(r"sat\s*/\s*v?b(yte)?s?", " sat vb ", t)
    t = re.sub(r"[^a-z0-9.,'<&\s/-]", " ", t)
    toks = []
    for raw in re.split(r"[\s/,]+", t):
        w = raw.strip(".'-?!")
        w = w.replace("'", "")
        if w:
            toks.append(w)
    return toks


def _correct(tok: str) -> str:
    if len(tok) < 4 or tok in COMMON or tok in KEYWORDS or tok in COIN_WORDS or tok.isdigit():
        return tok
    m = difflib.get_close_matches(tok, _VOCAB, n=1, cutoff=0.75)
    return m[0] if m else tok


def _has(toks: set[str], *words: str) -> bool:
    return any(w in toks for w in words)


def _phrase(text: str, *patterns: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def _coins_in(toks: list[str], text: str) -> list[str]:
    found: list[str] = []
    for phrase, cid in MULTIWORD_COINS.items():
        if phrase in text and cid not in found:
            found.append(cid)
    for t in toks:
        cid = COIN_WORDS.get(t)
        if cid and cid not in found:
            # "bitcoin cash" shouldn't also add bitcoin, etc.
            if any(cid != m and t in p for p, m in MULTIWORD_COINS.items() if m in found):
                continue
            found.append(cid)
    return found


def _chains_in(toks: set[str]) -> list[str]:
    out = []
    if toks & BTC_WORDS:
        out.append("btc")
    if toks & ETH_WORDS or toks & (L2_WORDS - {"polygon"}):
        out.append("eth")
    return out


def _speed_coin(coins: list[str], toks: set[str]) -> Optional[str]:
    m = {"bitcoin": "btc", "ethereum": "eth", "solana": "sol", "litecoin": "ltc"}
    for c in coins:
        if c in m:
            return m[c]
    if toks & BTC_WORDS:
        return "btc"
    if toks & ETH_WORDS or toks & L2_WORDS:
        return "eth"
    return coins[0] if coins else None


def parse(text: str, bot_username: Optional[str] = None) -> Intent:
    if bot_username:
        text = re.sub(rf"@{re.escape(bot_username)}\b", " ", text, flags=re.I)
    # 0) never accept secrets
    if looks_like_private_key(text):
        return Intent("safety")
    # 0b) own-wallet watching (public addresses)
    w = _wallet_intent(text)
    if w:
        return w
    # 1) transaction hashes win over everything
    eth = ETH_TX_RE.findall(text)
    stripped = ETH_TX_RE.sub(" ", text)
    btc = BTC_TXID_RE.findall(stripped)
    if eth or btc:
        return Intent("tx", txs=[("eth", h.lower()) for h in eth] + [("btc", h.lower()) for h in btc])

    raw_toks = _tokenize(text)
    # custody / secret-material questions get a safety answer before anything else
    lowraw = " ".join(raw_toks)
    if _phrase(lowraw, r"\bseed( phrase| words)?\b", r"\bprivate keys?\b", r"\bmnemonic\b", r"\brecovery (phrase|words)\b",
               r"\bsecret (phrase|recovery)\b", r"\b(hold|keep|store|custody|safeguard) (my|our|the) (funds|money|coins|crypto|btc|eth|bitcoin)",
               r"\bsend (you|it to you)\b", r"\b(can|will) you (send|transfer|hold|accelerate it for)\b",
               r"\b12 words\b", r"\b24 words\b"):
        return Intent("safety")

    # 1b) swap quotes / swap-rate alerts (read-only) — before fee/price intents, which share keywords
    sw = _swap_intent(text)
    if sw:
        return sw

    toks = [_correct(t) for t in raw_toks]
    ts = set(toks)
    low = " ".join(toks)
    coins = _coins_in(toks, low)
    chains = _chains_in(ts)

    alert_word = _has(ts, "alert", "alerts", "notify", "notification", "notifications", "remind", "ping") or \
        _phrase(low, r"\btell me\b", r"\blet me know\b", r"\bmessage me\b", r"\btext me\b", r"\bwarn me\b")
    # 2) alert management
    if alert_word and _has(ts, "clear", "remove", "delete", "cancel", "stop", "unalert", "disable", "reset") :
        return Intent("clear_alerts")
    if _has(ts, "unalert"):
        return Intent("clear_alerts")
    if _has(ts, "alerts", "notifications") and (_has(ts, "list", "show", "my", "what", "which", "active", "view", "see")
                                                or len(ts) <= 2):
        return Intent("list_alerts")

    # 3) set alert
    cond = _has(ts, "below", "under", "drops", "drop", "lower", "less", "falls", "fall", "<", "hits", "reaches",
                "reach", "cheaper", "goes", "at")
    if alert_word and _has(ts, "price", "prices", "worth", "value", "usd", "dollar", "dollars"):
        return Intent("set_alert", error="I can only alert on network fees (BTC sat/vB or ETH gwei), not prices. "
                                         "e.g. “alert me when btc fees drop below 3”.")
    if alert_word and (cond or NUM_RE.search(low)) and (chains or _has(ts, "fee", "fees", "gas") or NUM_RE.search(low)):
        nums = [float((a or b).replace(",", ".")) for a, b in NUM_RE.findall(low)]
        nums = [n for n in nums if n > 0]
        chain = None
        if "eth" in chains and "btc" not in chains:
            chain = "eth"
        elif "btc" in chains and "eth" not in chains:
            chain = "btc"
        elif "gwei" in ts or "gas" in ts:
            chain = "eth"
        elif ts & {"sat", "sats", "vb"}:
            chain = "btc"
        if chain is None:
            return Intent("set_alert", error="Which chain? e.g. “alert me when btc fees drop below 3” or “tell me when eth gas is under 1”.")
        if not nums:
            unit = "sat/vB" if chain == "btc" else "gwei"
            return Intent("set_alert", chains=[chain], error=f"Below what? Give a number in {unit}, e.g. “alert me when {chain} fees drop below {'3' if chain == 'btc' else '1'}”.")
        return Intent("set_alert", chains=[chain], threshold=nums[0])
    if alert_word and not (coins or chains) and _has(ts, "alert", "alerts"):
        return Intent("list_alerts") if _has(ts, "my") else Intent("help")

    # 4) stuck tx
    if _has(ts, "stuck", "unconfirmed", "rbf", "cpfp", "accelerate", "bump", "dropped") or \
            _phrase(low, r"\bpending\b", r"\bnot confirm", r"\bnot (been )?confirmed", r"\bhasnt confirmed",
                    r"\bnot going through", r"\bnever arrived", r"\bstill waiting", r"\bspeed up my\b",
                    r"\bcancel (my |a |the |this )?(\w+ )?(tx|transaction|transfer)\b", r"\bwhere is my\b", r"\bhasnt arrived",
                    r"\bnot arrived", r"\bnot showing up", r"\btaking (forever|ages|so long|too long|long)\b",
                    r"\bstill (not|hasnt|hasn't)\b"):
        return Intent("stuck", speed_coin=_speed_coin(coins, ts))

    # 5) fees
    fee_words = _has(ts, "fee", "fees", "gas", "gwei", "mempool", "sat", "sats", "vb", "vbyte", "vbytes")
    send_cost = _phrase(low, r"\bhow much (does it cost |is it |would it cost )?to (send|transfer|move)\b",
                        r"\bcost(s)? to (send|transfer|move)\b", r"\b(transaction|transfer|tx|network) (cost|costs|price)\b",
                        r"\bgood time to (send|transfer)\b", r"\bis (the )?network (busy|congested)\b",
                        r"\bhow (expensive|cheap)\b")
    if fee_words or send_cost:
        if not chains:
            m = {"bitcoin": "btc", "ethereum": "eth"}
            chains = [m[c] for c in coins if c in m]
        others = [c for c in coins if c not in ("bitcoin", "ethereum")]
        if not chains and others:
            return Intent("speed", speed_coin=_speed_coin(others, ts), coins=others)
        return Intent("fees", chains=chains or ["btc", "eth"])

    # 6) speed tips
    if _has(ts, "fast", "faster", "fastest", "quick", "quicker", "quickly", "speed", "slow", "confirmation",
            "confirmations", "confirm", "lightning", "l2", "layer2") or \
            (ts & L2_WORDS and _has(ts, "send", "sending", "transfer", "bridge", "use", "which", "move")) or \
            _phrase(low, r"\bhow long\b", r"\bcheap(est|er)? way\b", r"\bbest (way|network)\b", r"\bhow many (blocks|confirmations)\b", r"\bblock time\b"):
        return Intent("speed", speed_coin=_speed_coin(coins, ts), coins=coins)

    # 7) price
    price_words = _has(ts, "price", "prices", "worth", "value", "trading", "quote", "usd", "dollar", "dollars", "rate") or \
        _phrase(low, r"\bhow much is\b", r"\bhow much are\b", r"\bwhat'?s .* at\b", r"\bhow('?s| is) .* doing\b")
    unknown = [t for t in toks if t not in STOPWORDS and t not in COIN_WORDS and _correct(t) not in COIN_WORDS
               and not t.replace(".", "").isdigit() and len(t) >= 2 and t not in KEYWORDS
               and t not in GREETING and t not in THANKS and not any(t in p.split() for p in MULTIWORD_COINS)]
    if price_words and (coins or unknown):
        return Intent("price", coins=coins, unknown_terms=unknown[:5])
    if price_words:
        return Intent("price", error="Price of which coin? e.g. “price of sol and doge”.")
    # a message that is only coin names/tickers -> price
    if coins and all((t in COIN_WORDS or t in STOPWORDS or t in GREETING) for t in toks):
        return Intent("price", coins=coins)

    # 8) small talk / help
    if _has(ts, "help", "commands", "command", "menu", "start") or _phrase(low, r"\bwhat can you do\b", r"\bhow (does|do) (this|it|you) work\b", r"\bwho are you\b", r"\bwhat are you\b"):
        return Intent("help")
    if ts & THANKS or _phrase(low, r"\bthank you\b"):
        return Intent("thanks")
    if ts & GREETING or _phrase(low, r"\bgood (morning|afternoon|evening)\b", r"^gm\b"):
        return Intent("greeting")
    if coins:
        return Intent("price", coins=coins)
    return Intent("unknown")


# ---------------------------------------------------------------- wallet watching intents
_REMOVE_RE = re.compile(r"\b(stop (watching|tracking|monitoring|following)|unwatch|untrack|unfollow|remove|delete|forget|drop)\b")
_ADD_RE = re.compile(r"\b(watch|track|monitor|follow|add|register|keep an eye|notify|alert|tell me|let me know|ping|my (wallet|address|addy))\b")
_WALLET_WORD_RE = re.compile(r"\b(wallet|wallets|address|addresses|addy|addys)\b")
_LIST_RE = re.compile(r"\b(list|show|see|view|which|what|my|all)\b")
_LABEL_RE = re.compile(r"\b(?:as|called|named|label(?:led|ed)?|name it|call it)\s+[\"'“]?([^\"'”\n]{1,40})", re.I)


def clean_label(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    lab = re.sub(r"\s+", " ", raw).strip(" .,!?\"'“”")
    lab = re.sub(r"[<>]", "", lab)[:32].strip()
    return lab or None


def _wallet_intent(text: str) -> Optional[Intent]:
    low = text.lower()
    addrs = find_addresses(text)
    if addrs:
        chain, addr = addrs[0]
        if _REMOVE_RE.search(low):
            return Intent("remove_wallet", target=addr)
        if _ADD_RE.search(low) or _WALLET_WORD_RE.search(low):
            # label = text after "as/called/named" following the address
            after = text[text.lower().find(addr.lower()) + len(addr):] if addr.lower() in text.lower() else ""
            m = _LABEL_RE.search(after) or _LABEL_RE.search(text.replace(addr, " "))
            return Intent("add_wallet", chains=[chain], address=addr, label=clean_label(m.group(1)) if m else None)
        return Intent("address_hint", chains=[chain], address=addr)
    if not _WALLET_WORD_RE.search(low) and not re.search(r"\b(stop (watching|tracking|monitoring)|unwatch|untrack)\b", low):
        return None
    if _REMOVE_RE.search(low):
        m = re.search(r"(?:stop (?:watching|tracking|monitoring|following)|unwatch|untrack|unfollow|remove|delete|forget|drop)\s+"
                      r"(?:my\s+|the\s+|this\s+)?(?:wallet|address|addy)?\s*(?:called|named|labell?ed|label|number|no\.?)?\s*"
                      r"[\"'“]?([#\w][\w \-#]{0,31})", low)
        tgt = m.group(1).strip() if m else ""
        tgt = re.sub(r"\b(wallet|address|addy|please|pls)\b", "", tgt).strip()
        if not tgt or tgt in ("my", "the", "a", "all", "wallets", "addresses"):
            return Intent("remove_wallet", error="Which one? Say e.g. “stop watching savings” or “remove wallet #2” "
                                                 "(see “list my wallets”), or /removewallet &lt;id|label|address&gt;.")
        return Intent("remove_wallet", target=tgt)
    if _ADD_RE.search(low) and not _LIST_RE.search(low.replace("my wallet", "").replace("my address", "")) \
            or re.search(r"\b(watch|track|monitor|add)\b", low):
        if re.search(r"\b(list|show|see|view|which|what)\b", low):
            return Intent("list_wallets")
        return Intent("add_wallet", error="Send the public address too, e.g. “watch my wallet bc1q…” or “track this address 0x…”. "
                                          "Only add addresses that are yours. Never send a private key or seed phrase.")
    if re.search(r"\b(wallets|addresses|addys)\b", low) or _LIST_RE.search(low):
        return Intent("list_wallets")
    return None
