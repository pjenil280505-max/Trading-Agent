# Cost audit — 0.1.0

**Result: the complete application runs at zero cost.** Nothing in the default configuration
requires a payment, subscription, paid API key or cloud account. Paid options exist only where the
user deliberately adds them, and each one is labelled, disabled by default and budget-capped.

| Component | Cost | Notes |
|---|---|---|
| Open Market OS | Free | Apache-2.0 |
| Python runtime dependencies (44) | Free | Permissive open-source licenses — [inventory](DEPENDENCY_INVENTORY.md) |
| KLineChart, Preact (web UI) | Free | Apache-2.0, MIT |
| Hosting | Free | Runs on your own computer; no VPS, server or cloud needed |
| Market data: OKX, Coinbase, Kraken, Binance public endpoints | Free | No account or key |
| Market data: Yahoo Finance | Free | Unofficial; personal, non-commercial use only under Yahoo's terms |
| Economic data: FRED values, ECB rates | Free | FRED's optional API key (release schedule) is free |
| Company filings: SEC EDGAR | Free | Requires only a contact e-mail |
| News: RSS feeds | Free | |
| AI: Ollama, LM Studio, llama.cpp, mock | Free | Runs on your hardware |
| AI: Anthropic, OpenAI, DeepSeek, xAI, Mistral | **Paid per token** | Optional; disabled until you type a cost acknowledgement; daily budget (default $1) |
| AI: Gemini, Groq, OpenRouter | Free tiers with limits, paid beyond | Same safeguards as paid providers |
| MCP server and client | Free | |
| Build and test tools (Node.js, Vite, TypeScript, PyInstaller, pytest, Playwright, scanners) | Free | Only for building/testing |
| Code signing for binaries | Not used | Would cost money; binaries are unsigned (verify SHA-256 instead) |
| Hosted CI | Not used | Checks are local scripts |

## Hidden-cost checks

- **Electricity and hardware:** running bots 24/7 means a computer stays on. That is a real cost to
  you, but there is no service fee.
- **No paid fallbacks:** when a free source fails the app shows `STALE`/`UNAVAILABLE`; it never
  switches silently to a paid source.
- **AI spend visibility:** every AI run records tokens and estimated cost (based on the prices you
  enter); hosted providers stop when the daily budget is reached.
- **Trading costs:** paper trading has simulated commissions and slippage only. No live broker is
  connected in this release.
