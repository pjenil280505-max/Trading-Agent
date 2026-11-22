# AI guide

AI is **optional** and **off by default**. Everything else in Open Market OS works without it.

## What the AI can and cannot do

- It reasons over an **evidence pack** built by code from real data: quote and recent returns,
  indicators (SMA, EMA, RSI, ATR, MACD, ADX…), market structure, data-quality labels and, when
  available, headlines and reported fundamentals. It never sees your API keys, accounts or positions.
- Its answer must follow a fixed structure that separates **observations** (each pointing at the
  evidence), **calculations**, **interpretation**, **predictions** (with horizon and confidence),
  **assumptions** and **uncertainty**. Every number it quotes is checked against the evidence;
  unverified claims are listed under the answer.
- It **cannot** place, change or cancel orders, change settings, read secrets or control live bots.
  With the `propose` level it can suggest an order, which waits in **Portfolio → Proposals** until a
  person approves it — and then still passes the risk engine.

## Connecting a provider (Settings → AI)

| Preset | Runs where | Cost | Needs |
|---|---|---|---|
| `ollama` | Your computer | Free | [Ollama](https://ollama.com) installed and a model pulled |
| `lmstudio`, `llamacpp` | Your computer | Free | LM Studio or llama.cpp server running (OpenAI-compatible) |
| `anthropic` (Claude) | Anthropic | Paid per token; API billing is separate from Claude.ai plans | API key from console.anthropic.com |
| `openai`, `deepseek`, `xai` (Grok), `mistral` | The provider | Paid per token | API key |
| `gemini`, `groq`, `openrouter` | The provider | Free tiers with limits, paid beyond | API key |
| custom | Any OpenAI-compatible endpoint | — | Base URL (+ key) |
| `mock` | Your computer | Free | Nothing — rule-based, clearly labelled **not AI**, for testing |

Steps: choose a preset → name → (optional) base URL / model → API key if needed → **Add**. Providers
are added **disabled**. Local ones enable with a click; hosted ones require typing
`I ACCEPT THE COST`. Enter the provider's real prices per million tokens so the daily budget works.

Only Ollama (`qwen2.5:0.5b`) and the mock were exercised in testing for this release; the hosted
presets use the same tested clients but were not run (no keys, and they cost money).

## Local models and hardware

```bash
ollama pull qwen2.5:0.5b     # ~400 MB, runs on almost anything, makes more mistakes
ollama pull qwen2.5:3b       # better; ~2 GB, wants 8 GB+ RAM
ollama pull qwen2.5:7b       # better still; wants 16 GB RAM or a GPU
```

System status shows a suggestion for your computer's tier. Small models often break the required
answer format ("AI output could not be parsed"); a bigger model fixes that.

## Agent roles

Market analyst, bull analyst, bear analyst, quant researcher, risk analyst, news analyst,
fundamental analyst, strategy developer, and a supervisor that combines views.

- **Single analyst:** one role, your task text.
- **Bull vs bear debate:** bull, bear and risk analysts on the same evidence, then the supervisor
  reports agreement and disagreement (4 model calls).
- **Question with tools:** the model may call read-level tools (quote, analysis, instrument search),
  `run_backtest` (analyze level) and `propose_order` (propose level), one per step, up to 4 steps.

## Permissions (Settings → AI → What AI agents may do)

`read` market data · `analyze` (run backtests) · `propose` orders for your approval. Untick any of
them. No setting lets AI place orders directly.

## Cost risks

Hosted providers charge per token; a debate is four calls. Protections: providers disabled until
you accept the cost; a **daily budget** (default $1.00) blocks further calls once today's estimated
spend reaches it; every run is logged with tokens and estimated cost (**AI workspace → Recent AI
runs**). Estimates use the prices you entered — check your provider's own billing.

## MCP

To use Open Market OS *from* an AI assistant such as Claude Desktop or Claude Code, see the
[MCP guide](MCP_GUIDE.md). That path is controlled by the MCP permission matrix, not by these AI
settings.

## Privacy

Local models keep everything on your computer. With hosted providers, the evidence pack and your
question are sent to that company and handled under its terms. Saved AI runs stay in your local
database.
