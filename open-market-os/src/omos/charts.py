"""Chart workspaces, drawings and templates (persisted per instrument).

Drawings are stored with time/price anchors ``[{"t": epoch_ms, "v": price}]``, never pixels,
so they stay aligned when the timeframe, zoom, scale or layout changes.
"""

from __future__ import annotations

import re
from typing import Any

from omos.errors import NotFound, ValidationError
from omos.storage.db import Database
from omos.util import dumps, loads, new_id, now_ms

DRAWING_KINDS = {
    # chart-library built-ins
    "horizontalRayLine", "horizontalSegment", "horizontalStraightLine", "verticalRayLine", "verticalSegment",
    "verticalStraightLine", "rayLine", "segment", "straightLine", "priceLine", "priceChannelLine",
    "parallelStraightLine", "fibonacciLine", "brush", "simpleAnnotation", "simpleTag",
    # ours (registered in web/src/chart/overlays.ts)
    "rect", "circle", "triangle", "fibExtension", "regressionChannel", "longPosition", "shortPosition", "measure",
    "arrow", "textNote", "pitchfork",
}
_NAME = re.compile(r"^[\w .:-]{1,80}$")


class ChartService:
    def __init__(self, db: Database):
        self.db = db

    # workspaces (layout JSON produced by the UI)
    def workspaces(self) -> list[dict[str, Any]]:
        return [{"name": r["name"], "updated_at": r["updated_at"]} for r in self.db.query("SELECT name, updated_at FROM workspaces ORDER BY name")]

    def get_workspace(self, name: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM workspaces WHERE name = ?", (name,))
        if not row:
            raise NotFound(f"workspace '{name}' not found")
        return {"name": row["name"], "layout": loads(row["layout"], {}), "updated_at": row["updated_at"]}

    def save_workspace(self, name: str, layout: dict[str, Any]) -> dict[str, Any]:
        if not _NAME.match(name):
            raise ValidationError("workspace name must be 1–80 letters, digits, spaces, '.', ':' or '-'")
        text = dumps(layout)
        if len(text) > 500_000:
            raise ValidationError("workspace layout is too large")
        self.db.execute("INSERT INTO workspaces(name, layout, updated_at) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                        "layout = excluded.layout, updated_at = excluded.updated_at", (name, text, now_ms()))
        return self.get_workspace(name)

    def delete_workspace(self, name: str) -> None:
        self.db.execute("DELETE FROM workspaces WHERE name = ?", (name,))

    # drawings
    def drawings(self, instrument_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM drawings WHERE instrument_id = ? ORDER BY created_at", (instrument_id,))
        for r in rows:
            r["points"] = loads(r["points"], [])
            r["styles"] = loads(r["styles"], {})
            r["extend"] = loads(r["extend"], {})
            r["locked"] = bool(r["locked"])
            r["visible"] = bool(r["visible"])
        return rows

    def save_drawing(self, d: dict[str, Any]) -> dict[str, Any]:
        kind = d.get("kind")
        if kind not in DRAWING_KINDS:
            raise ValidationError(f"unknown drawing type '{kind}'")
        iid = str(d.get("instrument_id", ""))
        if ":" not in iid or len(iid) > 80:
            raise ValidationError("drawing needs an instrument_id like PROVIDER:SYMBOL")
        pts = d.get("points") or []
        if not isinstance(pts, list) or not 1 <= len(pts) <= 2000:
            raise ValidationError("drawing needs 1–2000 anchor points")
        clean = []
        for p in pts:
            try:
                clean.append({"t": int(p["t"]), "v": float(p["v"])})
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError("each point needs t (epoch ms) and v (price)") from exc
        for field in ("styles", "extend"):
            if len(dumps(d.get(field) or {})) > 20_000:
                raise ValidationError(f"drawing {field} too large")
        did = str(d.get("id") or new_id("dr_"))[:64]
        ts = now_ms()
        self.db.execute(
            "INSERT INTO drawings(id, instrument_id, kind, points, styles, extend, locked, visible, group_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET points = excluded.points, styles = excluded.styles, "
            "extend = excluded.extend, locked = excluded.locked, visible = excluded.visible, group_id = excluded.group_id, "
            "updated_at = excluded.updated_at",
            (did, iid, kind, dumps(clean), dumps(d.get("styles") or {}), dumps(d.get("extend") or {}), int(bool(d.get("locked"))),
             int(d.get("visible", True)), (d.get("group_id") or None), ts, ts))
        return next(x for x in self.drawings(iid) if x["id"] == did)

    def delete_drawing(self, did: str) -> None:
        self.db.execute("DELETE FROM drawings WHERE id = ?", (did,))

    def clear_drawings(self, instrument_id: str) -> int:
        return self.db.execute("DELETE FROM drawings WHERE instrument_id = ?", (instrument_id,)).rowcount

    # templates (indicator sets + style)
    def templates(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM chart_templates ORDER BY name")
        for r in rows:
            r["config"] = loads(r["config"], {})
        return rows

    def save_template(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        if not _NAME.match(name):
            raise ValidationError("invalid template name")
        if len(dumps(config)) > 100_000:
            raise ValidationError("template too large")
        self.db.execute("INSERT INTO chart_templates(name, config, updated_at) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                        "config = excluded.config, updated_at = excluded.updated_at", (name, dumps(config), now_ms()))
        return next(t for t in self.templates() if t["name"] == name)

    def delete_template(self, name: str) -> None:
        self.db.execute("DELETE FROM chart_templates WHERE name = ?", (name,))
