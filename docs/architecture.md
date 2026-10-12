# Architecture

| File | Role |
|---|---|
| `cryptospeed/__main__.py` | argument parsing, logging (httpx/telegram loggers held at WARNING so URLs containing the token are not logged), start-up |
| `cryptospeed/bot.py` | `build_app`: command handlers, free-text handler, repeating jobs `check_alerts` and `check_wallets` |
| `cryptospeed/nlu.py` | rule-based intent parsing |
| `cryptospeed/fetchers.py` | public data sources (for example mempool.space for BTC and public ETH RPCs for tx lookups) |
| `cryptospeed/formatting.py` | reply text |
| `cryptospeed/store.py` | SQLite `AlertStore` |
| `cryptospeed/wallets.py`, `addresses.py` | wallet watching and address validation |
| `cryptospeed/selftest.py` | live check of every fetcher |
| `tests/` | offline pytest suites (`asyncio_mode = auto`) |
