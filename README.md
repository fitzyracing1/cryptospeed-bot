# Skill — a free Telegram helper for crypto transfers

**Skill** answers plain-English questions about crypto fees, prices and stuck transactions, and watches your own wallet addresses for incoming transfers. No keys, no custody, no sign-up.

**Try it now: [@Simple1990bot on Telegram](https://t.me/Simple1990bot)** · [Launch announcement on X](https://x.com/fitzyracing1/status/2109341632500019367)

## What can you say to it?

Just type like you'd text a friend — no commands needed:

- *"btc fees"* · *"how much to send bitcoin?"* · *"is the network busy?"*
- *"price of sol and doge"* · *"whats the prise of etherium"* (typos are fine)
- *"how do I send eth faster?"* · *"send on arbitrum or base?"*
- *"my tx is stuck"* · *"transfer taking forever"*
- **Paste a transaction id** (BTC txid or ETH hash) and get its live status: confirmations, or for pending txs its fee rate vs. current fees plus unstick tips
- *"tell me when eth gas under 1"* · *"alert me if btc fees drop below 3"*
- *"watch my wallet bc1q… as savings"* · *"list my wallets"* · *"stop watching savings"*

In private chats the bot answers everything; in groups it answers when @mentioned or when you reply to it. Understanding is a rule-based parser (`cryptospeed/nlu.py`: keywords, regexes, difflib typo correction) — no LLM, no API key, nothing leaves the bot.

## Safety

- **Never holds funds.** The bot cannot receive, hold or send crypto. It only reads public data.
- **Never asks for keys.** It will never ask for a private key or seed phrase — anything shaped like one gets a safety warning, and seed-phrase/custody questions get the same warning.
- **Only watches addresses *you* add.** Wallet watching covers public addresses you explicitly register in your chat (max 10). It never scans GitHub, chats or anywhere else for wallets, and never looks up other people's addresses.
- If it can't fetch live data it says so — it never makes up numbers.

## Commands

Plain chat covers everything below, but commands work too:

| Command | What it does |
|---|---|
| `/start`, `/help` | Intro and command list |
| `/fees btc` | Live Bitcoin fees (next block, ~30 min, ~1 hr, economy, minimum) in sat/vB from mempool.space |
| `/fees eth` | Live Ethereum next-block base fee + typical/low priority fee (gwei), with the cost of a 21,000-gas transfer in ETH and USD |
| `/speed <coin>` | Tips for faster confirmation: btc, eth (incl. Arbitrum/Base/Optimism), sol, ltc, plus a generic fallback |
| `/stuck` | Speeding up a stuck tx: BTC RBF/CPFP, ETH same-nonce replacement, explorer link patterns |
| `/price <coin>` | USD price + 24h change (CoinGecko, with Coinbase spot as fallback; unknown coins resolved via CoinGecko search) |
| `/alert btc <sat_per_vb>` | Message this chat when the BTC next-block fee is ≤ the threshold |
| `/alert eth <gwei>` | Message this chat when ETH base fee + typical tip is ≤ the threshold |
| `/alerts` / `/unalert` | List / clear this chat's alerts |
| `/addwallet <address> [label]` | Watch **your own** BTC (bc1…/1…/3…) or ETH (0x…) public address for incoming transfers |
| `/wallets` | List watched addresses, labels and last known balance |
| `/removewallet <id\|label\|address>` | Stop watching an address |

### How wallet watching works

- Addresses are validated locally (base58check and bech32/bech32m checksums for BTC, 0x + 40 hex for ETH) and stored per chat in sqlite.
- Adding an address snapshots its current balance and recent txs **without alerting**, so you don't get a dump of history.
- A job runs every `WALLET_POLL_SECONDS` (default 75 s):
  - **BTC:** mempool.space address balance + recent txs. A new incoming tx is reported right away as unconfirmed, then once more when it confirms.
  - **ETH:** public RPC `eth_getBalance` plus Blockscout's free, keyless transactions API for sender and tx hash. If Blockscout is down, or the balance rises with no plain transfer (internal or contract transfer), you get a "balance increased by X ETH" alert. ERC-20 token transfers aren't tracked yet.
- Alerts go to the chat that registered the address and include the amount (≈ USD), sender, status, explorer link and new balance.

Fee alerts live in a small sqlite file (`alerts.db`), are checked every `ALERT_POLL_SECONDS` (default 180 s), fire once, then get removed. Max 10 per chat.

## Data sources (all free, no API key)

- Bitcoin fees: `https://mempool.space/api/v1/fees/recommended`
- Ethereum gas: `eth_feeHistory` (last 10 blocks, 25th/50th percentile tips) on public RPCs, tried in order: `ethereum-rpc.publicnode.com`, `eth.drpc.org`, `rpc.flashbots.net`, `rpc.mevblocker.io`
- Prices: CoinGecko `simple/price` (cached 60 s), with Coinbase `v2/prices/<SYM>-USD/spot` as fallback
- Tx lookups: mempool.space (BTC), `eth_getTransactionReceipt` + `eth_blockNumber` on public RPCs (ETH)

## Self-hosting

The hosted bot at [@Simple1990bot](https://t.me/Simple1990bot) is free to use; run your own copy like this:

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name and username, and copy the token.
   Optional: use `/setcommands` to register the command list above.
2. Install (Python 3.10+):

   ```bash
   git clone https://github.com/fitzyracing1/cryptospeed-bot.git && cd cryptospeed-bot
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. Run:

   ```bash
   export TELEGRAM_BOT_TOKEN="123456:ABC..."
   .venv/bin/python -m cryptospeed            # optional: --db /path/alerts.db
   ```

   The bot uses long polling, so no public URL or webhook is needed. httpx/telegram loggers are kept at WARNING so request URLs, which contain the token, never reach the log. Keep your token out of git — `.telegram_token` and `*.db` are already gitignored.

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
  bot.py        Telegram handlers, alert + wallet polling jobs
  fetchers.py   live API calls (mempool.space, ETH RPCs, CoinGecko, Coinbase)
  formatting.py all user-facing text (pure functions)
  nlu.py        rule-based natural-language intent parser
  addresses.py  BTC/ETH public-address validation, private-key shape detection
  wallets.py    own-wallet snapshots (mempool.space, ETH RPC, Blockscout) and incoming-tx diff
  store.py      sqlite alert store
  selftest.py   live self-test
tests/test_formatting.py, tests/test_nlu.py, tests/test_wallets.py
```

## Documentation

- In-repo docs: [docs/](docs/index.md)
- Wiki: [https://github.com/fitzyracing1/cryptospeed-bot/wiki](https://github.com/fitzyracing1/cryptospeed-bot/wiki)
