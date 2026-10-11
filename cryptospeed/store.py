"""Tiny sqlite store for fee alerts. Only chat ids and thresholds are stored — nothing sensitive."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import Optional


class AlertStore:
    def __init__(self, path: str = "alerts.db"):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS alerts ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " chat_id INTEGER NOT NULL,"
            " chain TEXT NOT NULL CHECK (chain IN ('btc','eth')),"
            " threshold REAL NOT NULL,"
            " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS wallets ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " chat_id INTEGER NOT NULL,"
            " chain TEXT NOT NULL CHECK (chain IN ('btc','eth')),"
            " address TEXT NOT NULL,"
            " label TEXT,"
            " balance TEXT,"            # confirmed balance in base units (sats / wei) as a decimal string
            " pending TEXT,"            # BTC unconfirmed net balance in sats (may be negative)
            " seen TEXT NOT NULL DEFAULT '{}',"   # JSON {txid: 1 confirmed | 0 pending}
            " initialized INTEGER NOT NULL DEFAULT 0,"
            " created_at TEXT DEFAULT CURRENT_TIMESTAMP,"
            " UNIQUE(chat_id, address))"
        )
        self._db.commit()

    MAX_PER_CHAT = 10
    MAX_WALLETS_PER_CHAT = 10

    def add(self, chat_id: int, chain: str, threshold: float) -> int:
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM alerts WHERE chat_id=?", (chat_id,)).fetchone()[0]
            if n >= self.MAX_PER_CHAT:
                raise ValueError(f"You already have {n} alerts (max {self.MAX_PER_CHAT}). Use /unalert to clear.")
            cur = self._db.execute("INSERT INTO alerts (chat_id, chain, threshold) VALUES (?,?,?)",
                                   (chat_id, chain, threshold))
            self._db.commit()
            return cur.lastrowid

    def list_for_chat(self, chat_id: int) -> list[tuple[int, str, float]]:
        with self._lock:
            return self._db.execute("SELECT id, chain, threshold FROM alerts WHERE chat_id=? ORDER BY id",
                                    (chat_id,)).fetchall()

    def clear_chat(self, chat_id: int) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM alerts WHERE chat_id=?", (chat_id,))
            self._db.commit()
            return cur.rowcount

    def all(self) -> list[tuple[int, int, str, float]]:
        with self._lock:
            return self._db.execute("SELECT id, chat_id, chain, threshold FROM alerts").fetchall()

    def delete(self, alert_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
            self._db.commit()

    # ------------------------------------------------------------ watched wallets (public addresses only)
    def add_wallet(self, chat_id: int, chain: str, address: str, label: Optional[str]) -> int:
        with self._lock:
            if self._db.execute("SELECT 1 FROM wallets WHERE chat_id=? AND address=?", (chat_id, address)).fetchone():
                raise ValueError("You're already watching that address. /wallets lists them.")
            n = self._db.execute("SELECT COUNT(*) FROM wallets WHERE chat_id=?", (chat_id,)).fetchone()[0]
            if n >= self.MAX_WALLETS_PER_CHAT:
                raise ValueError(f"You already watch {n} addresses (max {self.MAX_WALLETS_PER_CHAT}). "
                                 "Remove one with /removewallet first.")
            cur = self._db.execute("INSERT INTO wallets (chat_id, chain, address, label) VALUES (?,?,?,?)",
                                   (chat_id, chain, address, label))
            self._db.commit()
            return cur.lastrowid

    def _wallet_rows(self, where: str, args: tuple) -> list["Wallet"]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, chat_id, chain, address, label, balance, pending, seen, initialized FROM wallets "
                + where + " ORDER BY id", args).fetchall()
        return [Wallet(r[0], r[1], r[2], r[3], r[4], None if r[5] is None else int(r[5]),
                       None if r[6] is None else int(r[6]), json.loads(r[7] or "{}"), bool(r[8])) for r in rows]

    def wallets_for_chat(self, chat_id: int) -> list["Wallet"]:
        return self._wallet_rows("WHERE chat_id=?", (chat_id,))

    def all_wallets(self) -> list["Wallet"]:
        return self._wallet_rows("", ())

    def find_wallet(self, chat_id: int, ref: str) -> Optional["Wallet"]:
        """Match by id (#3 / 3), exact address (case-insensitive), or label (case-insensitive)."""
        ref = ref.strip()
        ws = self.wallets_for_chat(chat_id)
        r = ref.lstrip("#")
        if r.isdigit():
            for w in ws:
                if w.id == int(r):
                    return w
        for w in ws:
            if w.address.lower() == ref.lower():
                return w
        for w in ws:
            if w.label and w.label.lower() == ref.lower():
                return w
        return None

    def remove_wallet(self, chat_id: int, wallet_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM wallets WHERE id=? AND chat_id=?", (wallet_id, chat_id))
            self._db.commit()
            return cur.rowcount > 0

    def save_wallet_state(self, wallet_id: int, balance: Optional[int], pending: Optional[int], seen: dict) -> None:
        if len(seen) > 400:  # keep the store small; newest entries are appended last
            seen = dict(list(seen.items())[-400:])
        with self._lock:
            self._db.execute("UPDATE wallets SET balance=?, pending=?, seen=?, initialized=1 WHERE id=?",
                             (None if balance is None else str(balance), None if pending is None else str(pending),
                              json.dumps(seen), wallet_id))
            self._db.commit()


@dataclass
class Wallet:
    id: int
    chat_id: int
    chain: str
    address: str
    label: Optional[str]
    balance: Optional[int]   # sats or wei (confirmed)
    pending: Optional[int]   # BTC mempool net sats
    seen: dict
    initialized: bool
