"""AI provider adapters. All optional; none is enabled by default.

* ``openai_compatible`` — any endpoint speaking the OpenAI chat-completions API: Ollama,
  LM Studio, llama.cpp server, vLLM (local, free), and hosted services such as OpenAI,
  DeepSeek, Mistral, Groq, OpenRouter, xAI and Google Gemini's compatible endpoint (paid or
  free-tier-limited — see COST_AUDIT.md).
* ``anthropic`` — the Anthropic Messages API (paid; a Claude.ai subscription does NOT
  include API access).
* ``mock`` — deterministic, rule-based stand-in for tests/demos. Clearly labelled; not AI.

Cost safety: a non-local provider can only be used after the user acknowledges that it may
cost money, and calls stop when the configured daily budget is reached.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from omos.errors import NotFound, OmosError, PermissionDenied, ValidationError
from omos.security.secrets import SecretStore
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

PRESETS: dict[str, dict[str, Any]] = {
    "ollama": {"kind": "openai_compatible", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:0.5b",
               "is_local": True, "needs_key": False, "cost": "free (runs on this computer)"},
    "lmstudio": {"kind": "openai_compatible", "base_url": "http://127.0.0.1:1234/v1", "model": "local-model",
                 "is_local": True, "needs_key": False, "cost": "free (runs on this computer)"},
    "llamacpp": {"kind": "openai_compatible", "base_url": "http://127.0.0.1:8080/v1", "model": "local",
                 "is_local": True, "needs_key": False, "cost": "free (runs on this computer)"},
    "openai": {"kind": "openai_compatible", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
               "is_local": False, "needs_key": True, "cost": "PAID per token"},
    "anthropic": {"kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5",
                  "is_local": False, "needs_key": True, "cost": "PAID per token (API billing is separate from Claude.ai plans)"},
    "deepseek": {"kind": "openai_compatible", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
                 "is_local": False, "needs_key": True, "cost": "PAID per token"},
    "mistral": {"kind": "openai_compatible", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest",
                "is_local": False, "needs_key": True, "cost": "PAID (limited free tier may exist)"},
    "groq": {"kind": "openai_compatible", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.1-8b-instant",
             "is_local": False, "needs_key": True, "cost": "free tier with rate limits; paid beyond it"},
    "openrouter": {"kind": "openai_compatible", "base_url": "https://openrouter.ai/api/v1", "model": "openrouter/auto",
                   "is_local": False, "needs_key": True, "cost": "PAID (some free models)"},
    "xai": {"kind": "openai_compatible", "base_url": "https://api.x.ai/v1", "model": "grok-3-mini",
            "is_local": False, "needs_key": True, "cost": "PAID per token"},
    "gemini": {"kind": "openai_compatible", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-2.0-flash", "is_local": False, "needs_key": True,
               "cost": "free tier with limits; paid beyond it"},
    "mock": {"kind": "mock", "base_url": "local", "model": "rule-based", "is_local": True, "needs_key": False,
             "cost": "free (not AI: deterministic rules for testing)"},
}


@dataclass
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    latency_ms: float


class ProviderClient:
    def __init__(self, row: dict[str, Any], api_key: str | None, *, timeout_s: float = 120.0):
        self.row = row
        self.api_key = api_key
        self.timeout = timeout_s

    def chat(self, system: str, messages: list[dict[str, str]], *, max_tokens: int = 1200,
             temperature: float = 0.2, json_mode: bool = True) -> ChatResult:
        kind = self.row["kind"]
        t0 = time.monotonic()
        if kind == "mock":
            return MockModel().chat(system, messages)
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=10.0)) as client:
                if kind == "openai_compatible":
                    headers = {"Content-Type": "application/json"}
                    if self.api_key:
                        headers["Authorization"] = f"Bearer {self.api_key}"
                    body: dict[str, Any] = {"model": self.row["model"], "max_tokens": max_tokens, "temperature": temperature,
                                            "messages": [{"role": "system", "content": system}, *messages]}
                    if json_mode:
                        body["response_format"] = {"type": "json_object"}
                    r = client.post(self.row["base_url"].rstrip("/") + "/chat/completions", headers=headers, json=body)
                    if r.status_code == 400 and json_mode:  # some servers reject response_format
                        body.pop("response_format", None)
                        r = client.post(self.row["base_url"].rstrip("/") + "/chat/completions", headers=headers, json=body)
                    self._raise(r)
                    d = r.json()
                    text = d["choices"][0]["message"].get("content") or ""
                    u = d.get("usage") or {}
                    return ChatResult(text, int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)),
                                      d.get("model", self.row["model"]), (time.monotonic() - t0) * 1000)
                if kind == "anthropic":
                    if not self.api_key:
                        raise PermissionDenied("Anthropic needs an API key (Settings > AI)")
                    r = client.post(self.row["base_url"].rstrip("/") + "/v1/messages",
                                    headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                             "content-type": "application/json"},
                                    json={"model": self.row["model"], "max_tokens": max_tokens, "system": system,
                                          "temperature": temperature, "messages": messages})
                    self._raise(r)
                    d = r.json()
                    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
                    u = d.get("usage") or {}
                    return ChatResult(text, int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)),
                                      d.get("model", self.row["model"]), (time.monotonic() - t0) * 1000)
        except httpx.TimeoutException as exc:
            raise OmosError(f"AI provider '{self.row['name']}' timed out", code="ai_timeout") from exc
        except httpx.TransportError as exc:
            raise OmosError(f"cannot reach AI provider '{self.row['name']}' at {self.row['base_url']}: "
                            f"{type(exc).__name__}. Is it running?", code="ai_unreachable") from exc
        raise ValidationError(f"unknown provider kind '{kind}'")

    @staticmethod
    def _raise(r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise PermissionDenied("the AI provider rejected the API key")
        if r.status_code == 429:
            raise OmosError("the AI provider is rate-limiting or the quota is used up", code="ai_rate_limited")
        if r.status_code >= 400:
            raise OmosError(f"AI provider error HTTP {r.status_code}: {r.text[:300]}", code="ai_error")


class MockModel:
    """Deterministic stand-in: reads the evidence pack and applies simple trend rules. NOT AI."""

    def chat(self, system: str, messages: list[dict[str, str]]) -> ChatResult:
        content = messages[-1]["content"] if messages else ""
        evidence = {}
        start = content.find("<evidence>")
        end = content.find("</evidence>")
        if start >= 0 and end > start:
            try:
                evidence = json.loads(content[start + 10:end])
            except ValueError:
                evidence = {}
        role = "analyst"
        for r in ("bull", "bear", "risk", "supervisor"):
            if f"role: {r}" in system.lower():
                role = r
        ind = evidence.get("indicators", {})
        price = evidence.get("quote", {}).get("price")
        sma50 = ind.get("sma50")
        rsi = ind.get("rsi14")
        trend = "up" if price and sma50 and price > sma50 else "down" if price and sma50 else "unknown"
        stance = {"bull": "bullish", "bear": "bearish"}.get(role, "bullish" if trend == "up" else
                                                             "bearish" if trend == "down" else "neutral")
        obs = []
        if price is not None:
            obs.append({"claim": f"last price is {price}", "evidence": "quote.price"})
        if sma50 is not None:
            obs.append({"claim": f"SMA50 is {round(sma50, 6)}", "evidence": "indicators.sma50"})
        if rsi is not None:
            obs.append({"claim": f"RSI14 is {round(rsi, 2)}", "evidence": "indicators.rsi14"})
        out = {
            "summary": f"[MOCK rule-based output, not AI] {role}: trend {trend}.",
            "stance": stance,
            "observations": obs,
            "calculations": [],
            "interpretation": f"Price {'above' if trend == 'up' else 'below' if trend == 'down' else 'vs'} its 50-bar average.",
            "predictions": [],
            "assumptions": ["Rule-based mock: no reasoning was performed."],
            "uncertainty": "high — this is a deterministic test stub",
            "confidence": 0.1,
        }
        text = json.dumps(out)
        return ChatResult(text, len(system) // 4 + len(content) // 4, len(text) // 4, "mock/rule-based", 0.1)


class AIProviderService:
    def __init__(self, db: Database, secrets: SecretStore):
        self.db = db
        self.secrets = secrets

    def presets(self) -> dict[str, dict[str, Any]]:
        return PRESETS

    def add(self, name: str, preset: str, *, base_url: str | None = None, model: str | None = None,
            api_key: str | None = None, price_in: float = 0.0, price_out: float = 0.0) -> dict[str, Any]:
        if preset not in PRESETS:
            raise ValidationError(f"unknown preset '{preset}'")
        p = PRESETS[preset]
        base = (base_url or p["base_url"]).strip()
        if p["kind"] != "mock" and not base.startswith(("http://", "https://")):
            raise ValidationError("base URL must start with http:// or https://")
        is_local = p["is_local"] or base.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
        cur = self.db.execute(
            "INSERT INTO ai_providers(name, kind, base_url, model, enabled, price_in_per_mtok, price_out_per_mtok, is_local, "
            "options, created_at) VALUES (?,?,?,?,0,?,?,?,?,?)",
            (name.strip()[:60], p["kind"], base, (model or p["model"]).strip()[:120], float(price_in), float(price_out),
             int(is_local), dumps({"preset": preset, "cost_acknowledged": False, "cost_note": p["cost"]}), now_ms()))
        pid = int(cur.lastrowid)
        if api_key:
            self.secrets.put(f"ai.{pid}", api_key)
            self.db.execute("UPDATE ai_providers SET secret_ref = ? WHERE id = ?", (f"ai.{pid}", pid))
        return self.get(pid)

    def get(self, pid: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM ai_providers WHERE id = ?", (pid,))
        if not row:
            raise NotFound(f"AI provider {pid} not found")
        row["options"] = loads(row["options"], {})
        row["has_key"] = bool(row.pop("secret_ref"))
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self.get(r["id"]) for r in self.db.query("SELECT id FROM ai_providers ORDER BY id")]

    def set_enabled(self, pid: int, enabled: bool, *, acknowledge_cost: bool = False) -> dict[str, Any]:
        row = self.get(pid)
        opts = row["options"]
        if enabled and not row["is_local"] and not (acknowledge_cost or opts.get("cost_acknowledged")):
            raise PermissionDenied(f"'{row['name']}' is a hosted service that may charge you ({opts.get('cost_note')}). "
                                   "Confirm the cost warning to enable it.")
        if acknowledge_cost:
            opts["cost_acknowledged"] = True
        self.db.execute("UPDATE ai_providers SET enabled = ?, options = ? WHERE id = ?", (int(enabled), dumps(opts), pid))
        return self.get(pid)

    def delete(self, pid: int) -> None:
        self.get(pid)
        try:
            self.secrets.delete(f"ai.{pid}")
        except OmosError:
            pass
        self.db.execute("DELETE FROM ai_providers WHERE id = ?", (pid,))

    def spend_today(self) -> float:
        day_start = now_ms() - now_ms() % 86_400_000
        return float(self.db.scalar("SELECT COALESCE(SUM(cost), 0) FROM ai_runs WHERE ts >= ?", (day_start,)) or 0.0)

    def client(self, pid: int) -> ProviderClient:
        row = self.get(pid)
        if not row["enabled"]:
            raise PermissionDenied(f"AI provider '{row['name']}' is disabled (Settings > AI)")
        if not row["is_local"]:
            budget = float(self.db.get_setting("ai.daily_budget_usd", 1.0))
            if self.spend_today() >= budget:
                raise PermissionDenied(f"today's AI budget of ${budget:.2f} is used up (Settings > AI > Daily budget)")
        key = None
        if row["has_key"]:
            key = self.secrets.get(f"ai.{pid}")
        return ProviderClient(row, key)

    def cost(self, row: dict[str, Any], tokens_in: int, tokens_out: int) -> float:
        return tokens_in / 1e6 * float(row["price_in_per_mtok"]) + tokens_out / 1e6 * float(row["price_out_per_mtok"])
