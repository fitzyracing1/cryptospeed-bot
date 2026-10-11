"""Public-address detection/validation for BTC and ETH. Also flags things that look like PRIVATE keys so the
bot can refuse them. Pure functions, no network."""
from __future__ import annotations

import hashlib
import re
from typing import Optional

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

ETH_ADDR_RE = re.compile(r"(?<![0-9a-zA-Z])(0x[0-9a-fA-F]{40})(?![0-9a-zA-Z])")
BTC_LEGACY_RE = re.compile(r"(?<![0-9a-zA-Z])([13][1-9A-HJ-NP-Za-km-z]{25,34})(?![0-9a-zA-Z])")
BTC_BECH32_RE = re.compile(r"(?<![0-9a-zA-Z])((?:bc1|BC1)[02-9ac-hj-np-zAC-HJ-NP-Z]{11,71})(?![0-9a-zA-Z])")
# Things that look like secrets: WIF private keys, extended private keys, raw 64-hex keys handled by tx parser.
PRIVATE_KEY_RE = re.compile(r"(?<![0-9a-zA-Z])([5KL][1-9A-HJ-NP-Za-km-z]{50,51}|[xyzt]prv[1-9A-HJ-NP-Za-km-z]{100,112})(?![0-9a-zA-Z])")


def _b58check_ok(s: str) -> bool:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            return False
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    raw = b"\x00" * (len(s) - len(s.lstrip("1"))) + raw
    if len(raw) != 25:
        return False
    payload, chk = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != chk:
        return False
    return payload[0] in (0x00, 0x05)  # P2PKH or P2SH mainnet


def _bech32_polymod(values) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_ok(addr: str) -> bool:
    if addr.lower() != addr and addr.upper() != addr:
        return False
    a = addr.lower()
    hrp, _, data = a.rpartition("1")
    if hrp != "bc" or len(data) < 6:
        return False
    try:
        d = [_BECH32.index(c) for c in data]
    except ValueError:
        return False
    pm = _bech32_polymod([ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + d)
    witver = d[0]
    if witver == 0:
        return pm == 1 and len(a) in (42, 62)        # bech32 (P2WPKH / P2WSH)
    return pm == 0x2BC830A3 and witver <= 16          # bech32m (taproot and future versions)


def detect_chain(addr: str) -> Optional[str]:
    """Return 'btc' or 'eth' for a valid mainnet public address, else None."""
    a = addr.strip()
    if ETH_ADDR_RE.fullmatch(a):
        return "eth"
    if BTC_BECH32_RE.fullmatch(a) and _bech32_ok(a):
        return "btc"
    if BTC_LEGACY_RE.fullmatch(a) and _b58check_ok(a):
        return "btc"
    return None


def normalize(addr: str, chain: str) -> str:
    a = addr.strip()
    if chain == "eth" or a.lower().startswith("bc1"):
        return a.lower()
    return a  # base58 is case-sensitive


def find_addresses(text: str) -> list[tuple[str, str]]:
    """All valid (chain, address) pairs in free text, in order of appearance, de-duplicated."""
    hits = []
    for rx in (ETH_ADDR_RE, BTC_BECH32_RE, BTC_LEGACY_RE):
        for m in rx.finditer(text):
            c = detect_chain(m.group(1))
            if c:
                hits.append((m.start(), c, normalize(m.group(1), c)))
    seen, out = set(), []
    for _, c, a in sorted(hits):
        if a not in seen:
            seen.add(a)
            out.append((c, a))
    return out


def looks_like_private_key(text: str) -> bool:
    return bool(PRIVATE_KEY_RE.search(text))


def short(addr: str) -> str:
    return addr if len(addr) <= 16 else f"{addr[:8]}…{addr[-6:]}"
