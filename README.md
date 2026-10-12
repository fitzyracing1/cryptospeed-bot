# Skill — a free Telegram helper for crypto transfers

**Skill** answers plain-English questions about crypto fees, prices and stuck transactions, watches your own wallet addresses for incoming transfers, and compares live swap quotes across free DEX aggregators (read-only). No keys, no custody, no sign-up. It also runs as a local terminal app without Telegram (`--cli`).

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
- *"swap 1 eth to usdc"* · *"best rate for 500 usdc to sol"* · *"how much usdc for 1 eth"* · *"alert me when eth/usdc hits 4000"*

In private chats the bot answers everything; in groups it answers when @mentioned or when you reply to it. Understanding is a rule-based parser (`cryptospeed/nlu.py`: keywords, regexes, difflib typo correction) — no LLM, no API key, nothing leaves the bot.

## Safety

- **Never holds funds.** The bot cannot receive, hold or send crypto. It only reads public data.
- **Never asks for keys.** It will never ask for a private key or seed phrase — anything shaped like one gets a safety warning, and seed-phrase/custody questions get the same warning.
- **Only watches addresses *you* add.** Wallet watching covers public addresses you explicitly register in your chat (max 10). It never scans GitHub, chats or anywhere else for wallets, and never looks up other people's addresses.
- **Swap quotes are read-only.** It never executes swaps, never builds or signs transactions, and never asks for keys or wallet connections — it only compares public quote data so you can swap from your own wallet.
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
| `/swap <amount> <from> <to> [chain]` | Compare live swap quotes across keyless aggregators (read-only), e.g. `/swap 1 eth usdc`, `/swap 500 usdc sol` |
| `/swapalert <from> <to> <target> [chain]` | Message this chat when 1 FROM quotes ≥ target TO, e.g. `/swapalert eth usdc 4000` |
| `/alerts` / `/unalert` | List / clear this chat's alerts (fee alerts and swap-rate alerts) |
| `/addwallet <address> [label]` | Watch **your own** BTC (bc1…/1…/3…) or ETH (0x…) public address for incoming transfers |
| `/wallets` | List watched addresses, labels and last known balance |
| `/removewallet <id\|label\|address>` | Stop watching an address |

## Swap quotes (read-only, v0.2.0)

`/swap 1 eth usdc` (or just *"swap 1 eth to usdc"*) fetches live quotes **in parallel** from every free, keyless aggregator for that chain, compares them, and shows:

- 🏆 the best rate and expected output, with the other quotes listed below it,
- price impact per quote (computed from the aggregator's own USD valuations, or reported directly), with a ⚠️ slippage warning when impact exceeds 1%,
- estimated gas cost in USD and **net output after gas** where the API reports it,
- on Ethereum, a ⛽ gas-aware hint: the current base fee is ranked against the last ~2 hours of blocks (`eth_feeHistory`, 600 blocks) to say honestly whether now is a cheap time to swap,
- a clear disclaimer: quotes are live estimates, not predictions or financial advice — the bot can't predict prices.

Supported pairs — Ethereum: ETH, WETH, WBTC, USDC, USDT, DAI, LINK, UNI; Base: ETH, WETH, USDC; Arbitrum: ETH, WETH, USDC, USDT, ARB; Solana: SOL, USDC, USDT, JUP, BONK, WIF. The chain is inferred from the pair (e.g. SOL pairs go to Solana) or given explicitly (`/swap 1 eth usdc base`, *"on arbitrum"*). "btc" on an EVM chain means wrapped BTC (WBTC), and the bot says so.

**Quote sources (all free, no API key — verified):**

- EVM: ParaSwap `/prices`, KyberSwap aggregator `/routes`, CoW Protocol `/quote` (native ETH sells are quoted via WETH; CoW's fee is already included in its quote), Li.Fi `/quote`.
- Solana: Jupiter lite-api (`swap/v1/quote`, falling back to `ultra/v1/order` when the quote service is down), Raydium trade API (`compute/swap-base-in`).
- Not usable keyless, therefore not queried: **0x** (requires an API key) and **Odos** (public endpoint unreachable).

If a source fails it's listed as "no answer from: …"; if **all** sources fail you get a friendly try-again message. The bot never invents numbers.

**Pair price alerts:** `/swapalert eth usdc 4000` (or *"alert me when eth/usdc hits 4000"*) stores the alert in the same sqlite file and polls the aggregators every `SWAP_POLL_SECONDS` (default 300). When the best quote for 1 FROM reaches the target, the chat is notified once and the alert is removed. Max 10 per chat; `/alerts` lists them, `/unalert` clears them.

## Terminal mode (no Telegram, v0.2.0)

```bash
.venv/bin/python -m cryptospeed --cli          # optional: --db /path/alerts.db
```

Opens an interactive REPL that accepts exactly the same plain-English messages and slash commands as the Telegram bot and prints replies as plain text (HTML stripped). Fee alerts, swap-rate alerts and wallet watches poll in the background and print into the session while it's open. `exit`, `quit` or Ctrl-D quits. No token or network setup is needed beyond internet access to the public APIs.

## How wallet watching works

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
- Swap quotes: ParaSwap, KyberSwap, CoW Protocol and Li.Fi (EVM); Jupiter and Raydium (Solana) — see the swap section above for endpoints and the keyless-access notes

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

   No Telegram account? `python -m cryptospeed --cli` runs the same bot in your terminal (see Terminal mode above) — no token needed.

## Testing

```bash
# Live check of every data fetcher against the real APIs (no Telegram token needed):
.venv/bin/python -m cryptospeed --selftest

# Offline unit tests (formatting, parser, store, fetcher fallbacks, handlers, alert jobs,
# swap-quote comparison with mocked APIs, CLI dispatch + REPL loop):
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The selftest also queries each swap source individually (ParaSwap, KyberSwap, CoW, Li.Fi, Jupiter, Raydium) and prints a compared live ETH→USDC and SOL→USDC quote.

## Layout

```
cryptospeed/
  __main__.py   entry point (run bot / --selftest / --cli)
  bot.py        Telegram handlers, alert + swap-alert + wallet polling jobs, shared flows
  cli.py        terminal mode: slash-command dispatch + plain-English REPL (HTML stripped)
  fetchers.py   live API calls (mempool.space, ETH RPCs, CoinGecko, Coinbase)
  swaps.py      read-only swap quotes from keyless aggregators (never executes swaps)
  formatting.py all user-facing text (pure functions)
  nlu.py        rule-based natural-language intent parser
  addresses.py  BTC/ETH public-address validation, private-key shape detection
  wallets.py    own-wallet snapshots (mempool.space, ETH RPC, Blockscout) and incoming-tx diff
  store.py      sqlite store (fee alerts, swap-rate alerts, watched wallets)
  selftest.py   live self-test
tests/test_formatting.py, tests/test_nlu.py, tests/test_wallets.py, tests/test_swaps.py, tests/test_cli.py
```

## Documentation

- In-repo docs: [docs/](docs/index.md)
- Wiki: [https://github.com/fitzyracing1/cryptospeed-bot/wiki](https://github.com/fitzyracing1/cryptospeed-bot/wiki)
