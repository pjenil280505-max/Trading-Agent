"""OMS Script — a small, sandboxed, vectorised language for indicators, strategies,
screener formulas and alert conditions.

Safety properties (enforced here, tested in tests/unit/test_script.py):

* Hand-written lexer/parser -> AST -> evaluator. No ``eval``/``exec``, no attribute
  access, no imports, no loops, no user-defined functions: every program terminates.
* Only whitelisted functions; only numeric/boolean series and scalars exist at runtime.
* ``x[n]`` lags by ``n`` bars (n must be a non-negative constant) — there is no way to read
  a future bar. ``tf("1h", expr)`` only exposes *completed* higher-timeframe bars.
* Hard limits on source size, statements, AST nodes, nesting depth, window lengths and
  evaluation wall-clock time.

See docs/reference/SCRIPT_LANGUAGE.md for the user guide.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from omos.errors import ScriptError
from omos.indicators import core as ta
from omos.indicators import structure as st
from omos.marketdata.models import Candles
from omos.marketdata.timeframes import aggregate, parse as parse_tf

MAX_SOURCE = 20_000
MAX_STATEMENTS = 300
MAX_NODES = 5_000
MAX_DEPTH = 48          # nesting of parentheses / calls
MAX_AST_DEPTH = 160     # depth of the parsed tree (bounds evaluator recursion)
MAX_WINDOW = 5_000
DEFAULT_TIMEOUT_S = 5.0

SERIES_NAMES = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "time", "bar_index"}
STRATEGY_OUTPUTS = {"long_entry", "long_exit", "short_entry", "short_exit", "stop_loss", "take_profit",
                    "trailing_stop", "stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "position_size_pct"}
CONSTANTS = {"true": True, "false": False, "na": math.nan, "pi": math.pi}
KEYWORDS = {"and", "or", "not", "param", "plot"} | set(CONSTANTS)

# ---------------------------------------------------------------------------------------- lexer
_TOKEN = re.compile(r"""
    (?P<ws>[ \t\r]+)
  | (?P<comment>\#[^\n]*)
  | (?P<nl>\n)
  | (?P<num>\d+\.\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|\d+(?:[eE][-+]?\d+)?)
  | (?P<str>"[^"\n]{0,60}"|'[^'\n]{0,60}')
  | (?P<id>[A-Za-z_][A-Za-z0-9_]{0,40})
  | (?P<op><=|>=|==|!=|[-+*/%<>()\[\],=?:^])
""", re.VERBOSE)


@dataclass
class Tok:
    kind: str
    value: str
    line: int
    col: int


def tokenize(src: str) -> list[Tok]:
    if len(src) > MAX_SOURCE:
        raise ScriptError(f"script is longer than {MAX_SOURCE} characters")
    toks: list[Tok] = []
    pos, line, line_start = 0, 1, 0
    depth = 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise ScriptError(f"line {line}: unexpected character {src[pos]!r}")
        kind = m.lastgroup
        text = m.group()
        col = pos - line_start + 1
        if kind == "nl":
            if depth == 0:
                toks.append(Tok("nl", "\n", line, col))
            line += 1
            line_start = m.end()
        elif kind in ("ws", "comment"):
            pass
        else:
            if kind == "op" and text in "([":
                depth += 1
            elif kind == "op" and text in ")]":
                depth = max(0, depth - 1)
            toks.append(Tok(kind, text, line, col))
        pos = m.end()
    toks.append(Tok("nl", "\n", line, 0))
    toks.append(Tok("eof", "", line, 0))
    return toks


# ---------------------------------------------------------------------------------------- AST
@dataclass
class Node:
    kind: str                 # num bool str name call index unary binop ternary
    value: Any = None
    args: list[Node] = field(default_factory=list)
    line: int = 0
    depth: int = 1

    def key(self) -> str:
        if self.kind in ("num", "bool", "str", "name"):
            return f"{self.kind}:{self.value}"
        return f"{self.kind}:{self.value}(" + ",".join(a.key() for a in self.args) + ")"


@dataclass
class Statement:
    kind: str                 # assign | param | plot
    name: str
    expr: Node | None = None
    line: int = 0
    default: float | None = None
    bounds: tuple[float, float, float] | None = None
    label: str = ""


@dataclass
class Program:
    statements: list[Statement]
    params: dict[str, Statement]
    source: str

    @property
    def assigned(self) -> set[str]:
        return {s.name for s in self.statements if s.kind == "assign"}

    @property
    def is_strategy(self) -> bool:
        return bool(self.assigned & {"long_entry", "short_entry"})

    def describe_params(self) -> list[dict[str, Any]]:
        out = []
        for p in self.params.values():
            lo, hi, step = p.bounds if p.bounds else (None, None, None)
            out.append({"name": p.name, "default": p.default, "min": lo, "max": hi, "step": step})
        return out


class Parser:
    def __init__(self, toks: list[Tok]):
        self.t = toks
        self.i = 0
        self.nodes = 0

    # helpers
    def peek(self, k: int = 0) -> Tok:
        return self.t[min(self.i + k, len(self.t) - 1)]

    def next(self) -> Tok:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def accept(self, value: str) -> bool:
        if self.peek().value == value and self.peek().kind in ("op", "id"):
            self.i += 1
            return True
        return False

    def expect(self, value: str) -> Tok:
        tok = self.peek()
        if tok.value != value:
            raise ScriptError(f"line {tok.line}: expected '{value}' but found '{tok.value or 'end of line'}'")
        return self.next()

    def node(self, kind: str, value: Any = None, args: list[Node] | None = None, line: int = 0) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ScriptError(f"script is too complex (more than {MAX_NODES} expression nodes)")
        n = Node(kind, value, args or [], line)
        n.depth = 1 + max((a.depth for a in n.args), default=0)
        if n.depth > MAX_AST_DEPTH:
            raise ScriptError(f"line {line}: expression is too deep (more than {MAX_AST_DEPTH} levels); split it into variables")
        return n

    # grammar
    def program(self, source: str) -> Program:
        stmts: list[Statement] = []
        params: dict[str, Statement] = {}
        while self.peek().kind != "eof":
            if self.peek().kind == "nl":
                self.next()
                continue
            s = self.statement()
            if s.kind == "param":
                if s.name in params:
                    raise ScriptError(f"line {s.line}: parameter '{s.name}' declared twice")
                params[s.name] = s
            stmts.append(s)
            if len(stmts) > MAX_STATEMENTS:
                raise ScriptError(f"script has more than {MAX_STATEMENTS} statements")
            tok = self.peek()
            if tok.kind not in ("nl", "eof"):
                raise ScriptError(f"line {tok.line}: unexpected '{tok.value}' after statement")
        return Program(stmts, params, source)

    def statement(self) -> Statement:
        tok = self.peek()
        if tok.kind == "id" and tok.value == "param":
            self.next()
            name = self.ident("parameter name")
            self.expect("=")
            default = self.signed_number()
            bounds = None
            if self.accept("("):
                lo = self.signed_number()
                self.expect(",")
                hi = self.signed_number()
                step = 1.0
                if self.accept(","):
                    step = self.signed_number()
                self.expect(")")
                if not lo <= default <= hi or step <= 0:
                    raise ScriptError(f"line {tok.line}: parameter '{name}' needs min <= default <= max and step > 0")
                bounds = (lo, hi, step)
            return Statement("param", name, None, tok.line, default=default, bounds=bounds)
        if tok.kind == "id" and tok.value == "plot":
            self.next()
            self.expect("(")
            expr = self.expr(0)
            label = ""
            if self.accept(","):
                s = self.next()
                if s.kind != "str":
                    raise ScriptError(f"line {s.line}: plot label must be a quoted string")
                label = s.value[1:-1]
            self.expect(")")
            return Statement("plot", label or f"plot{tok.line}", expr, tok.line, label=label)
        if tok.kind == "id" and self.peek(1).value == "=":
            name = self.ident("variable name")
            self.expect("=")
            if name in SERIES_NAMES or name in FUNCTIONS or name in KEYWORDS:
                raise ScriptError(f"line {tok.line}: '{name}' is built in and cannot be assigned")
            return Statement("assign", name, self.expr(0), tok.line)
        raise ScriptError(f"line {tok.line}: a statement must be 'name = expression', 'param ...' or 'plot(...)'")

    def ident(self, what: str) -> str:
        tok = self.next()
        if tok.kind != "id" or tok.value in KEYWORDS:
            raise ScriptError(f"line {tok.line}: expected a {what}, found '{tok.value}'")
        return tok.value

    def signed_number(self) -> float:
        neg = self.accept("-")
        tok = self.next()
        if tok.kind != "num":
            raise ScriptError(f"line {tok.line}: expected a number, found '{tok.value}'")
        v = float(tok.value)
        return -v if neg else v

    def expr(self, depth: int) -> Node:
        if depth > MAX_DEPTH:
            raise ScriptError(f"expression nested more than {MAX_DEPTH} levels deep")
        cond = self.or_expr(depth)
        if self.accept("?"):
            a = self.expr(depth + 1)
            self.expect(":")
            b = self.expr(depth + 1)
            return self.node("ternary", None, [cond, a, b], cond.line)
        return cond

    def or_expr(self, d: int) -> Node:
        left = self.and_expr(d)
        while self.peek().kind == "id" and self.peek().value == "or":
            self.next()
            left = self.node("binop", "or", [left, self.and_expr(d)], left.line)
        return left

    def and_expr(self, d: int) -> Node:
        left = self.not_expr(d)
        while self.peek().kind == "id" and self.peek().value == "and":
            self.next()
            left = self.node("binop", "and", [left, self.not_expr(d)], left.line)
        return left

    def not_expr(self, d: int) -> Node:
        if d > MAX_DEPTH:
            raise ScriptError(f"expression nested more than {MAX_DEPTH} levels deep")
        if self.peek().kind == "id" and self.peek().value == "not":
            tok = self.next()
            return self.node("unary", "not", [self.not_expr(d + 1)], tok.line)
        return self.comparison(d)

    def comparison(self, d: int) -> Node:
        left = self.additive(d)
        if self.peek().kind == "op" and self.peek().value in ("<", "<=", ">", ">=", "==", "!="):
            op = self.next().value
            right = self.additive(d)
            return self.node("binop", op, [left, right], left.line)
        return left

    def additive(self, d: int) -> Node:
        left = self.term(d)
        while self.peek().kind == "op" and self.peek().value in ("+", "-"):
            op = self.next().value
            left = self.node("binop", op, [left, self.term(d)], left.line)
        return left

    def term(self, d: int) -> Node:
        left = self.unary(d)
        while self.peek().kind == "op" and self.peek().value in ("*", "/", "%"):
            op = self.next().value
            left = self.node("binop", op, [left, self.unary(d)], left.line)
        return left

    def unary(self, d: int) -> Node:
        if d > MAX_DEPTH:
            raise ScriptError(f"expression nested more than {MAX_DEPTH} levels deep")
        if self.peek().kind == "op" and self.peek().value in ("-", "+"):
            tok = self.next()
            operand = self.unary(d + 1)
            return operand if tok.value == "+" else self.node("unary", "-", [operand], tok.line)
        return self.power(d)

    def power(self, d: int) -> Node:
        base = self.postfix(d)
        if self.accept("^"):
            return self.node("binop", "^", [base, self.unary(d + 1)], base.line)
        return base

    def postfix(self, d: int) -> Node:
        node = self.primary(d)
        while self.peek().value == "[" and self.peek().kind == "op":
            tok = self.next()
            idx = self.expr(d + 1)
            self.expect("]")
            node = self.node("index", None, [node, idx], tok.line)
        return node

    def primary(self, d: int) -> Node:
        tok = self.next()
        if tok.kind == "num":
            return self.node("num", float(tok.value), line=tok.line)
        if tok.kind == "str":
            return self.node("str", tok.value[1:-1], line=tok.line)
        if tok.kind == "op" and tok.value == "(":
            inner = self.expr(d + 1)
            self.expect(")")
            return inner
        if tok.kind == "id":
            if tok.value in CONSTANTS:
                v = CONSTANTS[tok.value]
                return self.node("bool" if isinstance(v, bool) else "num", v, line=tok.line)
            if tok.value in ("and", "or", "not", "param", "plot"):
                raise ScriptError(f"line {tok.line}: unexpected keyword '{tok.value}'")
            if self.peek().value == "(" and self.peek().kind == "op":
                self.next()
                args: list[Node] = []
                if not (self.peek().value == ")" and self.peek().kind == "op"):
                    args.append(self.expr(d + 1))
                    while self.accept(","):
                        args.append(self.expr(d + 1))
                self.expect(")")
                if tok.value not in FUNCTIONS:
                    raise ScriptError(f"line {tok.line}: unknown function '{tok.value}'")
                return self.node("call", tok.value, args, tok.line)
            return self.node("name", tok.value, line=tok.line)
        raise ScriptError(f"line {tok.line}: unexpected '{tok.value or 'end of line'}'")


def parse(source: str) -> Program:
    if not isinstance(source, str):
        raise ScriptError("script must be text")
    try:
        prog = Parser(tokenize(source)).program(source)
    except RecursionError as exc:  # defence in depth: limits above should trigger first
        raise ScriptError("script is nested too deeply") from exc
    _check_names(prog)
    return prog


def _check_names(prog: Program) -> None:
    defined = set(SERIES_NAMES) | set(prog.params)
    for s in prog.statements:
        if s.expr is not None:
            for name, line in _names(s.expr):
                if name not in defined:
                    raise ScriptError(f"line {line}: '{name}' is used before it is defined")
        if s.kind == "assign":
            defined.add(s.name)


def _names(n: Node):
    if n.kind == "name":
        yield n.value, n.line
    for a in n.args:
        yield from _names(a)


# ---------------------------------------------------------------------------------------- runtime
Value = Any  # np.ndarray (float or bool) or float/bool scalar


class _Ctx:
    def __init__(self, candles: Candles, params: dict[str, float], timeout_s: float, mtf_depth: int = 0):
        self.c = candles
        self.n = len(candles)
        self.params = params
        self.vars: dict[str, Value] = {}
        self.cache: dict[str, Value] = {}
        self.deadline = time.monotonic() + timeout_s
        self.timeout_s = timeout_s
        self.mtf_depth = mtf_depth

    def check_time(self) -> None:
        if time.monotonic() > self.deadline:
            raise ScriptError(f"script took longer than {self.timeout_s:.1f}s and was stopped")

    def series(self, name: str) -> np.ndarray:
        c = self.c
        if name == "open":
            return c.open
        if name == "high":
            return c.high
        if name == "low":
            return c.low
        if name == "close":
            return c.close
        if name == "volume":
            return c.volume.astype(float)
        if name == "hl2":
            return (c.high + c.low) / 2
        if name == "hlc3":
            return (c.high + c.low + c.close) / 3
        if name == "ohlc4":
            return (c.open + c.high + c.low + c.close) / 4
        if name == "time":
            return c.ts.astype(float)
        if name == "bar_index":
            return np.arange(self.n, dtype=float)
        raise ScriptError(f"unknown series '{name}'")


def _as_series(v: Value, n: int) -> np.ndarray:
    if isinstance(v, np.ndarray):
        return v.astype(float) if v.dtype == bool else v
    if isinstance(v, bool):
        return np.full(n, 1.0 if v else 0.0)
    return np.full(n, float(v))


def _as_bool(v: Value, n: int) -> np.ndarray:
    if isinstance(v, np.ndarray):
        if v.dtype == bool:
            return v
        with np.errstate(invalid="ignore"):
            return np.nan_to_num(v, nan=0.0) != 0
    return np.full(n, bool(v) and not (isinstance(v, float) and math.isnan(v)))


def _const_int(v: Value, what: str, lo: int = 1, hi: int = MAX_WINDOW) -> int:
    if isinstance(v, np.ndarray):
        raise ScriptError(f"{what} must be a constant number (not a series)")
    if isinstance(v, bool) or v != v or v != int(v):
        raise ScriptError(f"{what} must be a whole number")
    iv = int(v)
    if not lo <= iv <= hi:
        raise ScriptError(f"{what} must be between {lo} and {hi}")
    return iv


def _const_float(v: Value, what: str) -> float:
    if isinstance(v, np.ndarray):
        raise ScriptError(f"{what} must be a constant number (not a series)")
    return float(v)


def _eval(node: Node, ctx: _Ctx) -> Value:
    ctx.check_time()
    k = node.kind
    if k == "num":
        return float(node.value)
    if k == "bool":
        return bool(node.value)
    if k == "str":
        raise ScriptError(f"line {node.line}: text is only allowed as the first argument of tf() and in plot labels")
    if k == "name":
        name = node.value
        if name in ctx.vars:
            return ctx.vars[name]
        if name in ctx.params:
            return float(ctx.params[name])
        return ctx.series(name)
    key = node.key()
    if key in ctx.cache:
        return ctx.cache[key]
    if k == "unary":
        v = _eval(node.args[0], ctx)
        out = ~_as_bool(v, ctx.n) if node.value == "not" else -_as_series(v, ctx.n) if isinstance(v, np.ndarray) else -float(v)
    elif k == "binop":
        out = _binop(node, ctx)
    elif k == "ternary":
        cond = _as_bool(_eval(node.args[0], ctx), ctx.n)
        a = _as_series(_eval(node.args[1], ctx), ctx.n)
        b = _as_series(_eval(node.args[2], ctx), ctx.n)
        out = np.where(cond, a, b)
    elif k == "index":
        base = _eval(node.args[0], ctx)
        lag = _const_int(_eval(node.args[1], ctx), f"line {node.line}: the [] offset", lo=0)
        out = ta.shift(_as_series(base, ctx.n), lag)
        if isinstance(base, np.ndarray) and base.dtype == bool:
            out = np.nan_to_num(out, nan=0.0) != 0
    elif k == "call":
        out = _call(node, ctx)
    else:  # pragma: no cover - parser never produces other kinds
        raise ScriptError(f"internal: unknown node {k}")
    ctx.cache[key] = out
    return out


def _binop(node: Node, ctx: _Ctx) -> Value:
    op = node.value
    a, b = _eval(node.args[0], ctx), _eval(node.args[1], ctx)
    n = ctx.n
    if op in ("and", "or"):
        x, y = _as_bool(a, n), _as_bool(b, n)
        return (x & y) if op == "and" else (x | y)
    scalar = not isinstance(a, np.ndarray) and not isinstance(b, np.ndarray)
    x = float(a) if scalar else _as_series(a, n)
    y = float(b) if scalar else _as_series(b, n)
    with np.errstate(all="ignore"):
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            if scalar:
                return x / y if y != 0 else math.nan
            return np.where(y != 0, np.divide(x, np.where(y == 0, 1, y)), np.nan)
        if op == "%":
            if scalar:
                return math.fmod(x, y) if y != 0 else math.nan
            return np.where(y != 0, np.fmod(x, np.where(y == 0, 1, y)), np.nan)
        if op == "^":
            r = np.power(x, y)
            return r
        cmp = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal,
               "==": np.equal, "!=": np.not_equal}[op]
        res = cmp(x, y)
        return bool(res) if scalar else res


# ---------------------------------------------------------------------------------------- functions
class Fn:
    def __init__(self, impl, arity: tuple[int, int], doc: str):
        self.impl = impl
        self.arity = arity
        self.doc = doc


def _ser(ctx: _Ctx, v: Value) -> np.ndarray:
    return _as_series(v, ctx.n)


def _win(v: Value, name: str = "length") -> int:
    return _const_int(v, name)


def _barssince(cond: np.ndarray) -> np.ndarray:
    out = np.full(cond.shape, np.nan)
    last = -1
    for i, c in enumerate(cond):
        if c:
            last = i
        if last >= 0:
            out[i] = i - last
    return out


def _valuewhen(cond: np.ndarray, x: np.ndarray) -> np.ndarray:
    out = np.full(cond.shape, np.nan)
    cur = np.nan
    for i in range(len(cond)):
        if cond[i]:
            cur = x[i]
        out[i] = cur
    return out


def _ffill(x: np.ndarray) -> np.ndarray:
    out = x.copy()
    idx = np.where(~np.isnan(out), np.arange(len(out)), 0)
    np.maximum.accumulate(idx, out=idx)
    filled = out[idx]
    filled[np.isnan(out) & (np.cumsum(~np.isnan(out)) == 0)] = np.nan
    return filled


def _rising(x: np.ndarray, n: int) -> np.ndarray:
    up = (x > ta.shift(x, 1)).astype(float)
    return ta.sma(up, n) == 1.0


def _falling(x: np.ndarray, n: int) -> np.ndarray:
    dn = (x < ta.shift(x, 1)).astype(float)
    return ta.sma(dn, n) == 1.0


def _mtf(node: Node, ctx: _Ctx) -> np.ndarray:
    """tf("4h", expr): evaluate expr on higher-timeframe bars aggregated from the base series,
    then expose each higher bar's value only from the first base bar AFTER that higher bar closed."""
    if len(node.args) != 2 or node.args[0].kind != "str":
        raise ScriptError(f"line {node.line}: use tf(\"4h\", expression)")
    if ctx.mtf_depth >= 1:
        raise ScriptError(f"line {node.line}: tf() cannot be nested")
    target = node.args[0].value
    tf_hi = parse_tf(target)
    base_tf = ctx.c.timeframe or "1m"
    tf_lo = parse_tf(base_tf)
    if tf_hi.ms <= tf_lo.ms:
        raise ScriptError(f"line {node.line}: tf() needs a higher timeframe than the chart's {base_tf}")
    hi = aggregate(ctx.c, target)
    sub = _Ctx(hi, ctx.params, max(0.1, ctx.deadline - time.monotonic()), ctx.mtf_depth + 1)
    sub.vars = {}  # variables computed on the base timeframe do not exist on the higher one
    vals = _as_series(_eval(node.args[1], sub), len(hi))
    # higher bar j closes at next_open(ts_j); base bar i closes at ts_i + base_ms
    hi_close = np.array([tf_hi.next_open(int(t)) for t in hi.ts], dtype=np.int64)
    base_close = ctx.c.ts + tf_lo.ms
    j = np.searchsorted(hi_close, base_close, side="right") - 1
    out = np.full(ctx.n, np.nan)
    ok = j >= 0
    out[ok] = vals[j[ok]]
    return out


def _call(node: Node, ctx: _Ctx) -> Value:
    name = node.value
    fn = FUNCTIONS[name]
    lo, hi = fn.arity
    if not lo <= len(node.args) <= hi:
        expect = f"{lo}" if lo == hi else f"{lo}–{hi}"
        raise ScriptError(f"line {node.line}: {name}() takes {expect} argument(s), got {len(node.args)}")
    if name == "tf":
        return _mtf(node, ctx)
    args = [_eval(a, ctx) for a in node.args]
    try:
        with np.errstate(all="ignore"):
            return fn.impl(ctx, *args)
    except ScriptError as exc:
        raise ScriptError(f"line {node.line}: {name}(): {exc.message}") from exc
    except ValueError as exc:
        raise ScriptError(f"line {node.line}: {name}(): {exc}") from exc


C = Candles


def _hlc(ctx: _Ctx):
    return ctx.c.high, ctx.c.low, ctx.c.close


FUNCTIONS: dict[str, Fn] = {
    # math
    "abs": Fn(lambda ctx, x: np.abs(x) if isinstance(x, np.ndarray) else abs(float(x)), (1, 1), "absolute value"),
    "min": Fn(lambda ctx, a, b: np.fmin(_ser(ctx, a), _ser(ctx, b)), (2, 2), "element-wise minimum"),
    "max": Fn(lambda ctx, a, b: np.fmax(_ser(ctx, a), _ser(ctx, b)), (2, 2), "element-wise maximum"),
    "sqrt": Fn(lambda ctx, x: np.sqrt(_ser(ctx, x)), (1, 1), "square root"),
    "log": Fn(lambda ctx, x: np.log(_ser(ctx, x)), (1, 1), "natural log"),
    "exp": Fn(lambda ctx, x: np.exp(np.clip(_ser(ctx, x), -700, 700)), (1, 1), "e^x"),
    "round": Fn(lambda ctx, x: np.round(_ser(ctx, x)), (1, 1), "round"),
    "floor": Fn(lambda ctx, x: np.floor(_ser(ctx, x)), (1, 1), "floor"),
    "ceil": Fn(lambda ctx, x: np.ceil(_ser(ctx, x)), (1, 1), "ceiling"),
    "sign": Fn(lambda ctx, x: np.sign(_ser(ctx, x)), (1, 1), "sign"),
    "clamp": Fn(lambda ctx, x, a, b: np.clip(_ser(ctx, x), _const_float(a, "min"), _const_float(b, "max")), (3, 3), "clamp"),
    # logic / helpers
    "iff": Fn(lambda ctx, c, a, b: np.where(_as_bool(c, ctx.n), _ser(ctx, a), _ser(ctx, b)), (3, 3), "if-then-else"),
    "nz": Fn(lambda ctx, x, v=0.0: np.nan_to_num(_ser(ctx, x), nan=_const_float(v, "replacement")), (1, 2), "replace NaN"),
    "na": Fn(lambda ctx, x: np.isnan(_ser(ctx, x)), (1, 1), "is NaN"),
    "cum": Fn(lambda ctx, x: np.nancumsum(_ser(ctx, x)), (1, 1), "cumulative sum"),
    "barssince": Fn(lambda ctx, c: _barssince(_as_bool(c, ctx.n)), (1, 1), "bars since condition was last true"),
    "valuewhen": Fn(lambda ctx, c, x: _valuewhen(_as_bool(c, ctx.n), _ser(ctx, x)), (2, 2),
                    "value of x when condition was last true"),
    "rising": Fn(lambda ctx, x, n: _rising(_ser(ctx, x), _win(n)), (2, 2), "x rose on each of the last n bars"),
    "falling": Fn(lambda ctx, x, n: _falling(_ser(ctx, x), _win(n)), (2, 2), "x fell on each of the last n bars"),
    "crossover": Fn(lambda ctx, a, b: ta.crossover(_ser(ctx, a), _ser(ctx, b)), (2, 2), "a crosses above b"),
    "crossunder": Fn(lambda ctx, a, b: ta.crossunder(_ser(ctx, a), _ser(ctx, b)), (2, 2), "a crosses below b"),
    "cross": Fn(lambda ctx, a, b: ta.crossover(_ser(ctx, a), _ser(ctx, b)) | ta.crossunder(_ser(ctx, a), _ser(ctx, b)),
                (2, 2), "a crosses b either way"),
    "tf": Fn(None, (2, 2), "evaluate an expression on a higher timeframe (completed bars only)"),
    # moving averages / stats
    "sma": Fn(lambda ctx, x, n: ta.sma(_ser(ctx, x), _win(n)), (2, 2), "simple moving average"),
    "ema": Fn(lambda ctx, x, n: ta.ema(_ser(ctx, x), _win(n)), (2, 2), "exponential moving average"),
    "rma": Fn(lambda ctx, x, n: ta.rma(_ser(ctx, x), _win(n)), (2, 2), "Wilder moving average"),
    "wma": Fn(lambda ctx, x, n: ta.wma(_ser(ctx, x), _win(n)), (2, 2), "weighted moving average"),
    "hma": Fn(lambda ctx, x, n: ta.hma(_ser(ctx, x), _const_int(n, "length", 2)), (2, 2), "Hull moving average"),
    "dema": Fn(lambda ctx, x, n: ta.dema(_ser(ctx, x), _win(n)), (2, 2), "double EMA"),
    "tema": Fn(lambda ctx, x, n: ta.tema(_ser(ctx, x), _win(n)), (2, 2), "triple EMA"),
    "vwma": Fn(lambda ctx, n: ta.vwma(ctx.c.close, ctx.c.volume, _win(n)), (1, 1), "volume-weighted MA of close"),
    "highest": Fn(lambda ctx, x, n: ta.highest(_ser(ctx, x), _win(n)), (2, 2), "highest value over n bars"),
    "lowest": Fn(lambda ctx, x, n: ta.lowest(_ser(ctx, x), _win(n)), (2, 2), "lowest value over n bars"),
    "stdev": Fn(lambda ctx, x, n: ta.stdev(_ser(ctx, x), _win(n)), (2, 2), "population standard deviation"),
    "change": Fn(lambda ctx, x, n=1.0: ta.change(_ser(ctx, x), _const_int(n, "lag", 0)), (1, 2), "x - x[n]"),
    "roc": Fn(lambda ctx, x, n: ta.roc(_ser(ctx, x), _win(n)), (2, 2), "rate of change %"),
    "mom": Fn(lambda ctx, x, n: ta.momentum(_ser(ctx, x), _win(n)), (2, 2), "momentum"),
    # oscillators
    "rsi": Fn(lambda ctx, x, n: ta.rsi(_ser(ctx, x), _win(n)), (2, 2), "RSI"),
    "cci": Fn(lambda ctx, n: ta.cci(*_hlc(ctx), _win(n)), (1, 1), "CCI"),
    "willr": Fn(lambda ctx, n: ta.williams_r(*_hlc(ctx), _win(n)), (1, 1), "Williams %R"),
    "mfi": Fn(lambda ctx, n: ta.mfi(*_hlc(ctx), ctx.c.volume, _win(n)), (1, 1), "money flow index"),
    "macd": Fn(lambda ctx, x, f, s, g: ta.macd(_ser(ctx, x), _win(f), _win(s), _win(g))["macd"], (4, 4), "MACD line"),
    "macd_signal": Fn(lambda ctx, x, f, s, g: ta.macd(_ser(ctx, x), _win(f), _win(s), _win(g))["signal"], (4, 4),
                      "MACD signal line"),
    "macd_hist": Fn(lambda ctx, x, f, s, g: ta.macd(_ser(ctx, x), _win(f), _win(s), _win(g))["hist"], (4, 4),
                    "MACD histogram"),
    "stoch_k": Fn(lambda ctx, k, d, sm: ta.stoch(*_hlc(ctx), _win(k), _win(d), _win(sm))["k"], (3, 3), "stochastic %K"),
    "stoch_d": Fn(lambda ctx, k, d, sm: ta.stoch(*_hlc(ctx), _win(k), _win(d), _win(sm))["d"], (3, 3), "stochastic %D"),
    "adx": Fn(lambda ctx, n: ta.adx(*_hlc(ctx), _win(n))["adx"], (1, 1), "ADX"),
    "plus_di": Fn(lambda ctx, n: ta.adx(*_hlc(ctx), _win(n))["plus_di"], (1, 1), "+DI"),
    "minus_di": Fn(lambda ctx, n: ta.adx(*_hlc(ctx), _win(n))["minus_di"], (1, 1), "-DI"),
    # volatility
    "atr": Fn(lambda ctx, n: ta.atr(*_hlc(ctx), _win(n)), (1, 1), "average true range"),
    "tr": Fn(lambda ctx: ta.true_range(*_hlc(ctx)), (0, 0), "true range"),
    "bb_upper": Fn(lambda ctx, x, n, k: ta.bbands(_ser(ctx, x), _win(n), _const_float(k, "stddev"))["upper"], (3, 3),
                   "Bollinger upper band"),
    "bb_lower": Fn(lambda ctx, x, n, k: ta.bbands(_ser(ctx, x), _win(n), _const_float(k, "stddev"))["lower"], (3, 3),
                   "Bollinger lower band"),
    "bb_mid": Fn(lambda ctx, x, n: ta.sma(_ser(ctx, x), _win(n)), (2, 2), "Bollinger middle band"),
    "bb_width": Fn(lambda ctx, x, n, k: ta.bbands(_ser(ctx, x), _win(n), _const_float(k, "stddev"))["width"], (3, 3),
                   "Bollinger band width"),
    "kc_upper": Fn(lambda ctx, n, m, a: ta.keltner(*_hlc(ctx), _win(n), _const_float(m, "multiplier"), _win(a))["upper"],
                   (3, 3), "Keltner upper"),
    "kc_lower": Fn(lambda ctx, n, m, a: ta.keltner(*_hlc(ctx), _win(n), _const_float(m, "multiplier"), _win(a))["lower"],
                   (3, 3), "Keltner lower"),
    "dc_upper": Fn(lambda ctx, n: ta.highest(ctx.c.high, _win(n)), (1, 1), "Donchian upper"),
    "dc_lower": Fn(lambda ctx, n: ta.lowest(ctx.c.low, _win(n)), (1, 1), "Donchian lower"),
    "hv": Fn(lambda ctx, n, ppy=365.0: ta.hv(ctx.c.close, _win(n), _const_float(ppy, "periods per year")), (1, 2),
             "historical volatility %"),
    # trend / volume
    "supertrend": Fn(lambda ctx, n, m: ta.supertrend(*_hlc(ctx), _win(n), _const_float(m, "multiplier"))["line"],
                     (2, 2), "Supertrend line"),
    "supertrend_dir": Fn(lambda ctx, n, m: ta.supertrend(*_hlc(ctx), _win(n), _const_float(m, "multiplier"))["direction"],
                         (2, 2), "Supertrend direction (+1 up, -1 down)"),
    "psar": Fn(lambda ctx, s=0.02, m=0.2: ta.psar(ctx.c.high, ctx.c.low, _const_float(s, "step"),
                                                  _const_float(m, "max step")), (0, 2), "Parabolic SAR"),
    "obv": Fn(lambda ctx: ta.obv(ctx.c.close, ctx.c.volume), (0, 0), "on-balance volume"),
    "vwap": Fn(lambda ctx: ta.vwap(ctx.c.ts, ctx.c.high, ctx.c.low, ctx.c.close, ctx.c.volume, "D"), (0, 0),
               "VWAP reset each UTC day"),
    # structure (confirmed, non-repainting)
    "swing_high": Fn(lambda ctx, l, r: _ffill(st.swing_points(ctx.c.high, ctx.c.low, _win(l), _win(r))["swing_high"]),
                     (2, 2), "last confirmed swing high price"),
    "swing_low": Fn(lambda ctx, l, r: _ffill(st.swing_points(ctx.c.high, ctx.c.low, _win(l), _win(r))["swing_low"]),
                    (2, 2), "last confirmed swing low price"),
    "fvg_bull": Fn(lambda ctx: st.fair_value_gaps(ctx.c.high, ctx.c.low)["bullish"] > 0, (0, 0), "bullish fair-value gap"),
    "fvg_bear": Fn(lambda ctx: st.fair_value_gaps(ctx.c.high, ctx.c.low)["bearish"] > 0, (0, 0), "bearish fair-value gap"),
}


@dataclass
class ScriptResult:
    variables: dict[str, Value]
    plots: dict[str, np.ndarray]
    params: dict[str, float]
    n: int

    def series(self, name: str, *, as_bool: bool = False) -> np.ndarray | None:
        if name not in self.variables:
            return None
        v = self.variables[name]
        return _as_bool(v, self.n) if as_bool else _as_series(v, self.n)


def resolve_params(prog: Program, overrides: dict[str, Any] | None = None) -> dict[str, float]:
    params = {name: float(s.default) for name, s in prog.params.items()}
    for k, v in (overrides or {}).items():
        if k not in prog.params:
            raise ScriptError(f"unknown parameter '{k}'")
        try:
            fv = float(v)
        except (TypeError, ValueError) as exc:
            raise ScriptError(f"parameter '{k}' must be a number") from exc
        b = prog.params[k].bounds
        if b and not b[0] <= fv <= b[1]:
            raise ScriptError(f"parameter '{k}' must be between {b[0]} and {b[1]}")
        params[k] = fv
    return params


def run(prog: Program | str, candles: Candles, params: dict[str, Any] | None = None, *,
        timeout_s: float = DEFAULT_TIMEOUT_S) -> ScriptResult:
    if isinstance(prog, str):
        prog = parse(prog)
    p = resolve_params(prog, params)
    ctx = _Ctx(candles, p, timeout_s)
    plots: dict[str, np.ndarray] = {}
    for s in prog.statements:
        if s.kind == "param":
            continue
        assert s.expr is not None
        try:
            val = _eval(s.expr, ctx)
        except RecursionError as exc:
            raise ScriptError(f"line {s.line}: expression is nested too deeply") from exc
        if s.kind == "assign":
            ctx.vars[s.name] = val
            ctx.cache = {k: v for k, v in ctx.cache.items() if f"name:{s.name}" not in k}
        elif s.kind == "plot":
            plots[s.name] = _as_series(val, ctx.n)
    return ScriptResult(ctx.vars, plots, p, ctx.n)


def evaluate_expression(expr: str, candles: Candles, *, timeout_s: float = 2.0) -> np.ndarray:
    """Evaluate a single expression (screener/alert formula) and return it as a series."""
    if "\n" in expr.strip() or "=" in expr.replace("==", "").replace("<=", "").replace(">=", "").replace("!=", ""):
        raise ScriptError("a formula must be a single expression (no assignments)")
    res = run(f"__value = {expr}\n", candles, timeout_s=timeout_s)
    return res.series("__value")  # type: ignore[return-value]


def function_catalog() -> list[dict[str, Any]]:
    return [{"name": k, "min_args": f.arity[0], "max_args": f.arity[1], "doc": f.doc} for k, f in sorted(FUNCTIONS.items())]
