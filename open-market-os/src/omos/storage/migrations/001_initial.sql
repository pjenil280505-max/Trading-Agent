-- Open Market OS schema, version 1.
-- Timestamps are UTC epoch milliseconds unless the column name says otherwise.

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE instruments (
  id TEXT PRIMARY KEY,                -- provider-qualified, e.g. OKX:BTC-USDT
  provider TEXT NOT NULL,
  provider_symbol TEXT NOT NULL,
  asset_class TEXT NOT NULL,
  exchange TEXT,
  base TEXT,
  quote TEXT,
  currency TEXT,
  name TEXT,
  timezone TEXT,
  session TEXT,
  tick_size REAL,
  meta TEXT,
  updated_at INTEGER
);

CREATE TABLE candles (
  instrument_id TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  ts INTEGER NOT NULL,                -- bar open time
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,               -- REALTIME | DELAYED | HISTORICAL | SIMULATED | ESTIMATED
  is_closed INTEGER NOT NULL DEFAULT 1,
  fetched_at INTEGER NOT NULL,
  PRIMARY KEY (instrument_id, timeframe, ts)
) WITHOUT ROWID;

CREATE TABLE provider_health (
  provider TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  last_ok INTEGER,
  last_error TEXT,
  last_error_at INTEGER,
  latency_ms REAL,
  requests INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  rate_limited INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER
);

CREATE TABLE watchlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE watchlist_items (
  watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
  instrument_id TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (watchlist_id, instrument_id)
);

CREATE TABLE workspaces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  layout TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE drawings (
  id TEXT PRIMARY KEY,
  instrument_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  points TEXT NOT NULL,               -- JSON [{"t": ms, "v": price}]
  styles TEXT,
  extend TEXT,
  locked INTEGER NOT NULL DEFAULT 0,
  visible INTEGER NOT NULL DEFAULT 1,
  group_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX idx_drawings_instrument ON drawings(instrument_id);

CREATE TABLE chart_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  config TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE strategies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'script',
  current_version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE strategy_versions (
  strategy_id INTEGER NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  source TEXT NOT NULL,
  note TEXT,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (strategy_id, version)
);

CREATE TABLE backtests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id INTEGER,
  strategy_version INTEGER,
  instrument_id TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  config TEXT NOT NULL,
  metrics TEXT NOT NULL,
  trades TEXT NOT NULL,
  equity TEXT NOT NULL,
  data_status TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('paper', 'live')),
  broker TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  initial_cash REAL NOT NULL,
  cash REAL NOT NULL,
  created_at INTEGER NOT NULL,
  meta TEXT
);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_order_id TEXT NOT NULL UNIQUE,  -- idempotency key
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  instrument_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  type TEXT NOT NULL CHECK (type IN ('market', 'limit', 'stop', 'stop_limit')),
  qty REAL NOT NULL CHECK (qty > 0),
  limit_price REAL,
  stop_price REAL,
  tif TEXT NOT NULL DEFAULT 'gtc',
  status TEXT NOT NULL,
  filled_qty REAL NOT NULL DEFAULT 0,
  avg_fill_price REAL,
  origin TEXT NOT NULL,
  bot_id INTEGER,
  reason TEXT,
  risk_decision_id TEXT,
  broker_order_id TEXT,
  meta TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_account ON orders(account_id, created_at);

CREATE TABLE fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  account_id INTEGER NOT NULL,
  instrument_id TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  price REAL NOT NULL,
  fee REAL NOT NULL,
  realized_pnl REAL NOT NULL DEFAULT 0,
  ts INTEGER NOT NULL
);
CREATE INDEX idx_fills_account ON fills(account_id, ts);

CREATE TABLE positions (
  account_id INTEGER NOT NULL,
  instrument_id TEXT NOT NULL,
  qty REAL NOT NULL,
  avg_price REAL NOT NULL,
  realized_pnl REAL NOT NULL DEFAULT 0,
  fees REAL NOT NULL DEFAULT 0,
  layers INTEGER NOT NULL DEFAULT 0,
  opened_at INTEGER,
  updated_at INTEGER,
  PRIMARY KEY (account_id, instrument_id)
);

CREATE TABLE equity_snapshots (
  account_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  equity REAL NOT NULL,
  cash REAL NOT NULL,
  exposure REAL NOT NULL,
  PRIMARY KEY (account_id, ts)
);

CREATE TABLE order_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  payload TEXT NOT NULL,
  origin TEXT NOT NULL,
  rationale TEXT,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected | expired
  result TEXT,
  created_at INTEGER NOT NULL,
  decided_at INTEGER,
  expires_at INTEGER
);

CREATE TABLE risk_state (
  account_id INTEGER PRIMARY KEY,
  trading_halted INTEGER NOT NULL DEFAULT 0,
  halt_reason TEXT,
  halted_at INTEGER,
  day TEXT,
  day_start_equity REAL,
  week TEXT,
  week_start_equity REAL,
  peak_equity REAL,
  updated_at INTEGER
);

CREATE TABLE risk_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  account_id INTEGER,
  decision TEXT NOT NULL,              -- approved | rejected | halt | resume | breach | kill
  origin TEXT,
  rule TEXT,
  message TEXT NOT NULL,
  order_payload TEXT,
  decision_id TEXT
);
CREATE INDEX idx_risk_events_ts ON risk_events(ts);

CREATE TABLE bots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,                  -- rule | dca | grid
  state TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'paper' CHECK (mode IN ('paper', 'live')),
  account_id INTEGER,
  instrument_id TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  strategy_id INTEGER,
  strategy_version INTEGER,
  config TEXT NOT NULL,
  flags TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1,
  last_heartbeat INTEGER,
  last_bar_ts INTEGER,
  error_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE bot_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  data TEXT
);
CREATE INDEX idx_bot_logs ON bot_logs(bot_id, ts);

CREATE TABLE bot_state_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  reason TEXT
);

CREATE TABLE alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  instrument_id TEXT,
  timeframe TEXT,
  kind TEXT NOT NULL,
  params TEXT NOT NULL,
  state TEXT NOT NULL,                 -- active | triggered | snoozed | expired | disabled
  frequency TEXT NOT NULL DEFAULT 'once',
  expires_at INTEGER,
  snoozed_until INTEGER,
  last_triggered_at INTEGER,
  trigger_count INTEGER NOT NULL DEFAULT 0,
  last_value REAL,
  last_bar_ts INTEGER,
  notify TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE alert_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  message TEXT NOT NULL,
  value REAL,
  acknowledged INTEGER NOT NULL DEFAULT 0,
  data TEXT
);

CREATE TABLE notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  level TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT,
  read INTEGER NOT NULL DEFAULT 0,
  data TEXT
);

CREATE TABLE screeners (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  definition TEXT NOT NULL,
  schedule_minutes INTEGER,
  last_run_at INTEGER,
  updated_at INTEGER NOT NULL
);

CREATE TABLE screener_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  screener_id INTEGER,
  ts INTEGER NOT NULL,
  results TEXT NOT NULL
);

CREATE TABLE journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  account_id INTEGER,
  instrument_id TEXT,
  title TEXT NOT NULL,
  body TEXT,
  tags TEXT NOT NULL DEFAULT '[]',
  trade_ref TEXT
);

CREATE TABLE workflows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 0,
  current_version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE workflow_versions (
  workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  definition TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (workflow_id, version)
);

CREATE TABLE workflow_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id INTEGER NOT NULL,
  version INTEGER NOT NULL,
  started_at INTEGER NOT NULL,
  finished_at INTEGER,
  status TEXT NOT NULL,
  dry_run INTEGER NOT NULL DEFAULT 0,
  trigger_data TEXT,
  log TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE ai_providers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,                  -- openai_compatible | anthropic | mock
  base_url TEXT NOT NULL,
  model TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  secret_ref TEXT,
  price_in_per_mtok REAL NOT NULL DEFAULT 0,
  price_out_per_mtok REAL NOT NULL DEFAULT 0,
  is_local INTEGER NOT NULL DEFAULT 0,
  options TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL
);

CREATE TABLE ai_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  provider_id INTEGER,
  workflow TEXT,
  role TEXT,
  request_summary TEXT,
  output TEXT,
  tool_calls TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL
);

CREATE TABLE ai_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  content TEXT NOT NULL
);

CREATE TABLE mcp_servers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  transport TEXT NOT NULL CHECK (transport IN ('stdio', 'http')),
  command TEXT,
  args TEXT NOT NULL DEFAULT '[]',
  url TEXT,
  enabled INTEGER NOT NULL DEFAULT 0,
  timeout_s REAL NOT NULL DEFAULT 30,
  created_at INTEGER NOT NULL
);

CREATE TABLE mcp_tool_permissions (
  server_id INTEGER NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
  tool_name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  description TEXT,
  PRIMARY KEY (server_id, tool_name)
);

CREATE TABLE plugins (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  kind TEXT NOT NULL,
  manifest TEXT NOT NULL,
  granted_permissions TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL,
  installed_at INTEGER NOT NULL
);

CREATE TABLE auth_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                  -- browser | device | mcp | cli
  scopes TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER,
  last_used_at INTEGER,
  revoked_at INTEGER
);

CREATE TABLE auth_codes (
  code_hash TEXT PRIMARY KEY,
  purpose TEXT NOT NULL,               -- bootstrap | pairing
  scopes TEXT NOT NULL,
  label TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER
);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  detail TEXT,
  ip TEXT
);
CREATE INDEX idx_audit_ts ON audit_log(ts);

CREATE TABLE news_items (
  id TEXT PRIMARY KEY,                 -- hash of normalised title
  ts INTEGER NOT NULL,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT,
  summary TEXT,
  symbols TEXT NOT NULL DEFAULT '[]',
  topics TEXT NOT NULL DEFAULT '[]',
  sentiment REAL,
  sentiment_method TEXT,
  fetched_at INTEGER NOT NULL
);
CREATE INDEX idx_news_ts ON news_items(ts);

CREATE TABLE heartbeats (
  component TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  info TEXT
);

CREATE TABLE kv_cache (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
