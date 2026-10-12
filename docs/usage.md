# Usage

## CLI (`cryptospeed/__main__.py`)

| Option | Meaning |
|---|---|
| `--selftest` | hit every live data source and print results (no token needed) |
| `--db PATH` | SQLite file for alerts (default `$ALERTS_DB` or `alerts.db`) |

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | (none, required) | bot token |
| `ALERTS_DB` | `alerts.db` | default `--db` |
| `ALERT_POLL_SECONDS` | `180` | alert check interval |
| `WALLET_POLL_SECONDS` | `75` | wallet check interval |

## Telegram commands (`build_app` in `bot.py`)

`/start`, `/help`, `/fees`, `/speed`, `/stuck`, `/price`, `/alert`, `/alerts`, `/unalert`, `/addwallet`, `/wallets`, `/removewallet`.

The bot also handles free text such as "btc fees", "price of sol and doge", "tell me when eth gas under 1", "watch my wallet <address> as savings", or a pasted BTC txid or ETH tx hash. In groups it only answers when @mentioned or replied to.
