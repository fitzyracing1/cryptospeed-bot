# Changelog

## 0.2.0 (unreleased)

### Added
- **Swap quotes (read-only).** `/swap <amount> <from> <to> [chain]` and plain English
  (“swap 1 eth to usdc”, “best rate for 500 usdc to sol”, “how much usdc for 1 eth”) fetch live
  quotes in parallel from free, keyless aggregator APIs and compare them: best rate, expected
  output, price impact with a slippage warning above 1%, estimated gas in USD and net output
  after gas where available. Sources: ParaSwap, KyberSwap, CoW Protocol and Li.Fi on Ethereum /
  Base / Arbitrum; Jupiter (lite-api, with Ultra fallback) and Raydium on Solana. 0x and Odos are
  not queried — 0x requires an API key and Odos's public endpoint is unreachable. The bot never
  executes swaps, never asks for keys or wallet connections, and never invents numbers: when every
  source fails you get a friendly try-again message.
- **Gas-aware swap hint.** On Ethereum the current base fee is ranked against the last ~2 hours of
  blocks (`eth_feeHistory`) to say whether now is a relatively cheap time to swap.
- **Pair price alerts.** `/swapalert <from> <to> <target> [chain]` (or “alert me when eth/usdc hits
  4000”) stores the alert in the existing sqlite file and polls quotes every `SWAP_POLL_SECONDS`
  (default 300); fires once when 1 FROM quotes ≥ target TO, then is removed. Listed by `/alerts`,
  cleared by `/unalert`.
- **Terminal mode.** `python -m cryptospeed --cli` opens an interactive REPL that accepts the same
  plain-English messages and slash commands as the Telegram bot and prints plain-text replies
  (HTML stripped). Fee, swap and wallet alerts print in-session. No Telegram token needed.
- Tests: swap parser phrasings, pair resolution, quote comparison against mocked APIs, swap-alert
  store + polling job, CLI dispatch and REPL loop. The live `--selftest` now also checks each swap
  source individually plus a compared ETH→USDC and SOL→USDC quote and the base-fee history.

### Unchanged
- All v0.1.0 features (fees, prices, speed tips, stuck-tx help, fee alerts, own-wallet watching)
  and the no-custody policy: the bot never holds funds and never asks for private keys or seed
  phrases.

## 0.1.0

- Initial release: live BTC/ETH fees, prices with fallback source, speed tips, stuck-tx guide,
  fee-drop alerts, own-wallet watching, rule-based natural-language chat, live `--selftest`.
