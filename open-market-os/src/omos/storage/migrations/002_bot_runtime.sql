-- Runtime state that bots persist between ticks (stops, grid levels, last action); survives restarts.
ALTER TABLE bots ADD COLUMN runtime TEXT NOT NULL DEFAULT '{}';
