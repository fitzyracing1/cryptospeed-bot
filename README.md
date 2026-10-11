# CryptoSpeed Telegram bot

Honest, non-custodial help for getting crypto transfers confirmed faster: live fee data, plain-language tips, stuck-tx guides and fee-drop alerts.

**No custody.** The bot never holds, receives or sends funds and never asks for private keys or seed phrases. It only reads public data.

## Commands
| Command | What it does |
|---|---|
| `/start`, `/help` | Intro and command list |
| `/fees btc` | Live Bitcoin fees (next block, ~30 min, ~1 hr, economy, minimum) in sat/vB from mempool.space |
| `/fees eth` | Live Ethereum next-block base fee + typical/low priority fee (gwei), with the cost of a 21,000-gas transfer in ETH and USD |
| `/speed <coin>` | Tips for faster confirmation: btc, eth (incl. Arbitrum/Base/Optimism), sol, ltc, plus a generic fallback |
| `/stuck` | Speeding up a stuck tx: BTC RBF/CPFP, ETH same-nonce replacement, explorer link patterns |
| `/price <coin>` | USD price + 24h change (CoinGecko, with Coinbase spot price as fallback) |
| `/alert btc <sat_per_vb>` | Message this chat when the BTC next-block fee is ≤ the threshold |
| `/alert eth <gwei>` | Message this chat when ETH base fee + typical tip is ≤ the threshold |
| `/alerts` / `/unalert` | List / clear this chat's alerts |
| `/addwallet <address> [label]` | Watch **your own** BTC (bc1…/1…/3…) or ETH (0x…) public address for incoming transfers. Max 10 per chat |
| `/wallets` | List watched addresses, labels and last known balance |
| `/removewallet <id\|label\|address>` | Stop watching an address |

### Just chat (no commands needed)
You can send plain messages too. In private chats the bot answers everything. In groups it only answers when it's @mentioned or when you reply to one of its messages. A rule-based parser (`cryptospeed/nlu.py`: keywords, regexes and difflib typo correction; no LLM or API key) understands things like:
- “btc fees”, “how much to send bitcoin?”, “bitcon fess?”, “gas price”, “is the network busy”
- “price of sol and doge”, “whats the prise of etherium”, “price of near and bonk” (unknown coins are resolved through CoinGecko search, cached for 24 h)
- “how do I send eth faster?”, “how long does a litecoin transfer take”, “send on arbitrum or base?”
- “my tx is stuck”, “speed up my btc transaction”, “transfer taking forever”
- a pasted BTC txid (64 hex) or ETH tx hash (0x + 64 hex): live status via mempool.space or ETH RPC (`eth_getTransactionReceipt` + `eth_blockNumber`), giving confirmations, or for pending txs the fee rate vs. current fees plus unstick tips
- “tell me when eth gas under 1”, “alert me if btc fees drop below 3”, “show my alerts”, “clear my alerts”
- hi / thanks / help; seed-phrase or custody questions get a safety warning; anything else gets a list of example questions

### Own-wallet watching (public addresses only)
The bot watches only public addresses that **you** add, either with `/addwallet` or by saying “watch my wallet <address> as savings”, “track this address <address>”, “list my wallets” or “stop watching savings”. It never asks for, stores or uses private keys or seed phrases; anything shaped like a WIF key or xprv gets a safety warning instead. It can't move funds and never looks up other people's wallets.

- Addresses are validated locally (base58check and bech32/bech32m checksums for BTC, 0x + 40 hex for ETH) and stored per chat in sqlite (`wallets` table).
- When you add an address, the bot snapshots its current balance and recent txs **without alerting**, so you don't get a dump of history.
- A job runs every `WALLET_POLL_SECONDS` (default 75 s):
  - **BTC:** `https://mempool.space/api/address/<a>` (balance) and `/api/address/<a>/txs` (recent txs, mempool first). A new incoming tx is reported right away as unconfirmed, then once more when it confirms.
  - **ETH:** public RPC `eth_getBalance` plus Blockscout's free, keyless `https://eth.blockscout.com/api/v2/addresses/<a>/transactions?filter=to` for sender and tx hash. Etherscan v2 needs an API key, so it isn't used. If Blockscout is down, or the balance rises with no plain transfer (internal or contract transfer), you get a "balance increased by X ETH" alert. ERC-20 token transfers aren't tracked yet.
- Alerts go to the chat that registered the address and include the amount (≈ USD), sender, status, explorer link and new balance.

Alerts are stored in a small sqlite file (`alerts.db`). They're checked every `ALERT_POLL_SECONDS` (default 180), fire once, then get removed. Max 10 per chat.

## Data sources (all free, no API key)
- Bitcoin fees: `https://mempool.space/api/v1/fees/recommended`
- Ethereum gas: `eth_feeHistory` (last 10 blocks, 25th/50th percentile tips) on public RPCs, tried in this order: `ethereum-rpc.publicnode.com`, `eth.drpc.org`, `rpc.flashbots.net`, `rpc.mevblocker.io`. `eth_maxPriorityFeePerGas` isn't used because several public RPCs return 0 for it.
- Prices: CoinGecko `simple/price` (cached for 60 s because the free tier rate-limits), with Coinbase `v2/prices/<SYM>-USD/spot` as the fallback.

If a source fails, the user gets a friendly "try again" message. The bot never makes up numbers.

## Setup
1. In Telegram, open **@BotFather**, send `/newbot`, pick a name and username, and copy the token.
   Optional: use `/setcommands` to register the command list above.
2. Install (Python 3.10+):
   ```bash
   cd cryptospeed-bot
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Run:
   ```bash
   export TELEGRAM_BOT_TOKEN="123456:ABC..."
   .venv/bin/python -m cryptospeed            # optional: --db /path/alerts.db
   ```
   The bot uses long polling, so you don't need a public URL or webhook. httpx/telegram loggers are kept at WARNING so request URLs, which contain the token, never reach the log.

## Testing
```bash
# Live check of every data fetcher against the real APIs (no Telegram token needed):
.venv/bin/python -m cryptospeed --selftest

# Offline unit tests (formatting, alert parsing/store, fetcher fallbacks, handlers, alert job):
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## Layout
```
cryptospeed/
  __main__.py   entry point (run bot / --selftest)
  bot.py        Telegram handlers, alert polling job
  fetchers.py   live API calls (mempool.space, ETH RPCs, CoinGecko, Coinbase)
  formatting.py all user-facing text (pure functions)
  nlu.py        rule-based natural-language intent parser
  addresses.py  BTC/ETH public-address validation, private-key shape detection
  wallets.py    own-wallet snapshots (mempool.space, ETH RPC, Blockscout) and incoming-tx diff
  store.py      sqlite alert store
  selftest.py   live self-test
tests/test_formatting.py, tests/test_nlu.py, tests/test_wallets.py
```
