"""Order, fill and position value types plus the order state machine."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from omos.errors import InvalidState, ValidationError


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    NEW = "new"                      # validated + risk-approved, not yet at the broker
    ACCEPTED = "accepted"            # resting at the broker
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)


TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.NEW: {OrderStatus.ACCEPTED, OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.REJECTED,
                      OrderStatus.CANCELLED},
    OrderStatus.ACCEPTED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED,
                           OrderStatus.REJECTED},
    OrderStatus.PARTIALLY_FILLED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED,
                                   OrderStatus.EXPIRED},
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
}


def check_transition(old: OrderStatus, new: OrderStatus) -> None:
    if new not in TRANSITIONS[old]:
        raise InvalidState(f"order cannot go from {old.value} to {new.value}")


ORIGINS = ("ui", "bot", "ai", "mcp", "workflow", "api", "system", "emergency")


@dataclass
class OrderRequest:
    account_id: int
    instrument_id: str
    side: Side
    qty: float
    type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    tif: str = "gtc"
    client_order_id: str | None = None
    origin: str = "ui"
    bot_id: int | None = None
    reason: str = ""
    stop_loss: float | None = None     # protective stop price, used for risk sizing checks
    take_profit: float | None = None
    reduce_only: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrderRequest:
        try:
            return cls(
                account_id=int(d["account_id"]), instrument_id=str(d["instrument_id"]), side=Side(d["side"]),
                qty=float(d["qty"]), type=OrderType(d.get("type", "market")),
                limit_price=_opt_float(d.get("limit_price")), stop_price=_opt_float(d.get("stop_price")),
                tif=str(d.get("tif", "gtc")), client_order_id=d.get("client_order_id"),
                origin=str(d.get("origin", "ui")), bot_id=d.get("bot_id"), reason=str(d.get("reason", ""))[:500],
                stop_loss=_opt_float(d.get("stop_loss")), take_profit=_opt_float(d.get("take_profit")),
                reduce_only=bool(d.get("reduce_only", False)), meta=dict(d.get("meta") or {}),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ValidationError(f"invalid order: {exc}") from exc

    def validate(self) -> None:
        if not math.isfinite(self.qty) or self.qty <= 0:
            raise ValidationError("quantity must be a positive number")
        if self.origin.split(":")[0] not in ORIGINS:
            raise ValidationError(f"unknown order origin '{self.origin}'")
        if self.type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            if self.limit_price is None or not math.isfinite(self.limit_price) or self.limit_price <= 0:
                raise ValidationError("limit orders need a positive limit price")
        if self.type in (OrderType.STOP, OrderType.STOP_LIMIT):
            if self.stop_price is None or not math.isfinite(self.stop_price) or self.stop_price <= 0:
                raise ValidationError("stop orders need a positive stop price")
        if self.tif not in ("gtc", "day", "ioc"):
            raise ValidationError("time-in-force must be gtc, day or ioc")
        if self.client_order_id is not None and (len(self.client_order_id) > 96 or not self.client_order_id.strip()):
            raise ValidationError("client order id must be 1–96 characters")
        for name in ("stop_loss", "take_profit"):
            v = getattr(self, name)
            if v is not None and (not math.isfinite(v) or v <= 0):
                raise ValidationError(f"{name} must be a positive price")

    def to_dict(self) -> dict[str, Any]:
        return {"account_id": self.account_id, "instrument_id": self.instrument_id, "side": self.side.value,
                "qty": self.qty, "type": self.type.value, "limit_price": self.limit_price,
                "stop_price": self.stop_price, "tif": self.tif, "client_order_id": self.client_order_id,
                "origin": self.origin, "bot_id": self.bot_id, "reason": self.reason, "stop_loss": self.stop_loss,
                "take_profit": self.take_profit, "reduce_only": self.reduce_only, "meta": self.meta}


def _opt_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)
