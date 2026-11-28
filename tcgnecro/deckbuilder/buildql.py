"""BuildQL: a small query language for the deck builder's card list.

Modelled on arkham.build's BuildQL
(https://github.com/arkham-build/arkham.build, frontend/src/store/lib/buildql/buildql.md),
so queries written for that site mostly work here too:

    trait = "spell" & level >= 2
    (cost = 0 | cost = 1) & slot ? ["hand", "arcane"]
    health + sanity < 14
    name = /^the/

A query runs after the filter panel, over the cards that already pass it.
Games say which field names exist (and their aliases) in deckbuilder.toml's
[query] table; any key of a card's attrs or raw data also works by name.
docs/BUILDQL.md is the user-facing reference.

Comparisons, for each operator its negation in brackets:

    =   (!=)    fuzzy: every word of the right side appears in the left
                (case-insensitive); numbers and booleans compare exactly
    ==  (!==)   exact: whole-value match, or for text fields an exact
                substring (punctuation and [tokens] count)
    ?   (!?)    = against any item of a [list]
    ??  (!??)   == against any item of a [list]
    > < >= <=   numbers only

A multi-value field (a list, or a string the game splits, like Arkham traits)
matches if any of its values does; a negated operator matches only if none
does. `field = null` tests for a missing or empty value. & binds tighter than
|; parentheses group; + - * / % do arithmetic on numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tcgnecro.db import normalize_title


class QueryError(ValueError):
    """A query that can't be parsed, with the character position it failed at."""

    def __init__(self, message: str, pos: int | None = None):
        super().__init__(message)
        self.pos = pos


# ----- lexer ---------------------------------------------------------------------

COMPARE_OPS = ("!==", "!??", "==", "!=", "!?", "??", ">=", "<=", "=", "?", ">", "<")
PUNCT = ("&", "|", "(", ")", "[", "]", ",", "+", "-", "*", "/", "%")
# After these a "/" starts a regex rather than meaning division.
_REGEX_AFTER = set(COMPARE_OPS) | {"&", "|", "(", "[", ",", None}


@dataclass
class Token:
    kind: str      # "num", "str", "regex", "ident", "op", "end"
    value: Any
    pos: int


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        prev = out[-1].value if out and out[-1].kind == "op" else (None if not out else "value")
        if ch in "\"'":
            j, buf = i + 1, []
            while j < n and text[j] != ch:
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(text[j])
                j += 1
            if j >= n:
                raise QueryError("Unclosed quote", i)
            out.append(Token("str", "".join(buf), i))
            i = j + 1
        elif ch == "/" and prev in _REGEX_AFTER:
            j, buf = i + 1, []
            while j < n and text[j] != "/":
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j])
                    j += 1
                buf.append(text[j])
                j += 1
            if j >= n:
                raise QueryError("Unclosed /regex/", i)
            j += 1
            flags = re.IGNORECASE
            while j < n and text[j].isalpha():
                if text[j] == "s":
                    flags |= re.DOTALL
                elif text[j] == "m":
                    flags |= re.MULTILINE
                j += 1
            try:
                out.append(Token("regex", re.compile("".join(buf), flags), i))
            except re.error as e:
                raise QueryError(f"Bad regular expression: {e}", i) from None
            i = j
        elif ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            m = re.match(r"\d*\.?\d+|\d+", text[i:])
            s = m.group(0)
            out.append(Token("num", float(s) if "." in s else int(s), i))
            i += len(s)
        elif ch.isalpha() or ch == "_":
            m = re.match(r"[A-Za-z_][\w.:]*", text[i:])
            s = m.group(0).rstrip(".")
            out.append(Token("ident", s, i))
            i += len(s)
        else:
            op = next((o for o in COMPARE_OPS + PUNCT if text.startswith(o, i)), None)
            if op is None:
                raise QueryError(f"Unexpected character {ch!r}", i)
            out.append(Token("op", op, i))
            i += len(op)
    out.append(Token("end", None, n))
    return out


# ----- syntax tree ---------------------------------------------------------------

@dataclass
class Literal:
    value: Any          # None, bool, int/float, str, re.Pattern


@dataclass
class ListLit:
    items: list


@dataclass
class Field:
    name: str
    pos: int


@dataclass
class Arith:
    op: str
    left: Any
    right: Any


@dataclass
class Neg:
    operand: Any


@dataclass
class Compare:
    op: str
    left: Any
    right: Any


@dataclass
class Truthy:
    operand: Any        # a bare value used as a condition: `unique`


@dataclass
class BoolOp:
    op: str             # "&" or "|"
    left: Any
    right: Any


# ----- parser --------------------------------------------------------------------

class _Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0
        self.furthest: QueryError | None = None

    def peek(self) -> Token:
        return self.toks[self.i]

    def take(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def is_op(self, *ops) -> bool:
        t = self.peek()
        return t.kind == "op" and t.value in ops

    def expect(self, op: str) -> None:
        if not self.is_op(op):
            self.fail(f"Expected {op!r}")
        self.take()

    def fail(self, message: str):
        t = self.peek()
        found = "end of query" if t.kind == "end" else repr(t.value.pattern if t.kind == "regex" else t.value)
        raise QueryError(f"{message}, found {found}", t.pos)

    def parse(self):
        node = self.parse_or()
        if self.peek().kind != "end":
            self.fail("Expected & or | between conditions")
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.is_op("|"):
            self.take()
            node = BoolOp("|", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_condition()
        while self.is_op("&"):
            self.take()
            node = BoolOp("&", node, self.parse_condition())
        return node

    def parse_condition(self):
        # "(" is ambiguous: a group of conditions, or arithmetic like
        # "(health + sanity) > 12". Try a comparison first; if that fails,
        # back up and try a group, and report whichever got further.
        start = self.i
        try:
            return self.parse_comparison()
        except QueryError as first:
            if not self.toks[start].kind == "op" or self.toks[start].value != "(":
                raise
            self.i = start
            try:
                self.take()
                node = self.parse_or()
                self.expect(")")
                return node
            except QueryError as second:
                raise max((first, second), key=lambda e: e.pos or 0) from None

    def parse_comparison(self):
        left = self.parse_arith()
        if self.peek().kind == "op" and self.peek().value in COMPARE_OPS:
            op = self.take().value
            right = self.parse_list() if self.is_op("[") else self.parse_arith()
            return Compare(op, left, right)
        return Truthy(left)

    def parse_list(self):
        self.expect("[")
        items = []
        if not self.is_op("]"):
            items.append(self.parse_arith())
            while self.is_op(","):
                self.take()
                items.append(self.parse_arith())
        self.expect("]")
        return ListLit(items)

    def parse_arith(self):
        node = self.parse_term()
        while self.is_op("+", "-"):
            node = Arith(self.take().value, node, self.parse_term())
        return node

    def parse_term(self):
        node = self.parse_unary()
        while self.is_op("*", "/", "%"):
            node = Arith(self.take().value, node, self.parse_unary())
        return node

    def parse_unary(self):
        if self.is_op("-"):
            self.take()
            return Neg(self.parse_unary())
        return self.parse_factor()

    def parse_factor(self):
        t = self.peek()
        if t.kind in ("num", "str", "regex"):
            self.take()
            return Literal(t.value)
        if t.kind == "ident":
            self.take()
            word = t.value.lower()
            if word in ("true", "false"):
                return Literal(word == "true")
            if word == "null":
                return Literal(None)
            return Field(word, t.pos)
        if self.is_op("("):
            self.take()
            node = self.parse_arith()
            self.expect(")")
            return node
        if self.is_op("["):
            self.fail("A [list] only goes on the right of ? or ??")
        self.fail("Expected a field, number, \"text\" or /regex/")


def _fields_in(node) -> list[Field]:
    if isinstance(node, Field):
        return [node]
    out = []
    for attr in ("left", "right", "operand"):
        if hasattr(node, attr):
            out += _fields_in(getattr(node, attr))
    if isinstance(node, ListLit):
        for item in node.items:
            out += _fields_in(item)
    return out


# ----- fields --------------------------------------------------------------------

# Fields every game has; the deck builder fills them in per card.
CORE_FIELDS = {
    "id": "The card's id (code)",
    "code": "Same as id",
    "pack": "Pack code or pack name",
    "in_deck": "Copies in this deck (all sections)",
    "owned": "In a pack you own (only once your collection is set up)",
}


@dataclass
class QuerySchema:
    """How field names resolve for one game: built from deckbuilder.toml's
    [query] table (QueryConfig) plus the keys the game's cards actually have."""

    fields: dict[str, str | list[str]] = field(default_factory=dict)  # name -> path(s)
    split: dict[str, str] = field(default_factory=dict)               # data key -> separator
    text_fields: set[str] = field(default_factory=set)                # data keys compared as text
    known: set[str] = field(default_factory=set)                      # every valid name

    @classmethod
    def build(cls, config, cards) -> "QuerySchema":
        known = set(CORE_FIELDS) | set(config.fields)
        for card in cards:
            known.update(k.lower() for k in card.attrs)
            known.update(k.lower() for k in card.raw)
        return cls(dict(config.fields), dict(config.split), set(config.text_fields), known)


@dataclass
class Context:
    """What a query needs to know beyond the card itself."""

    schema: QuerySchema
    counts: dict[str, int] = field(default_factory=dict)      # card id -> copies in the deck
    owned: set[str] | None = None
    pack_names: dict[str, str] = field(default_factory=dict)  # pack code -> name


def _lookup(card, path: str):
    if path.startswith("raw."):
        return path[4:], card.raw.get(path[4:])
    if path.startswith("attrs."):
        return path[6:], card.attrs.get(path[6:])
    if path in card.attrs:
        return path, card.attrs[path]
    return path, card.raw.get(path)


def resolve(card, name: str, ctx: Context) -> tuple[Any, bool]:
    """(value, is_text) of a field for one card. Lists are multi-value."""
    target = ctx.schema.fields.get(name)
    if isinstance(target, str) and target in CORE_FIELDS and target != name:
        name = target   # an alias of a core field, like "pa" for pack
    if name in ("id", "code") and name not in ctx.schema.fields:
        return card.id, False
    if name == "in_deck":
        return ctx.counts.get(card.id, 0), False
    if name == "owned":
        return None if ctx.owned is None else card.id in ctx.owned, False
    if name == "pack" and "pack" not in ctx.schema.fields:
        code = card.attrs.get("pack") or card.raw.get("pack_code")
        names = [code, ctx.pack_names.get(code) or card.raw.get("pack_name")]
        return [v for v in names if v], False
    paths = ctx.schema.fields.get(name, name)
    values, is_text = [], False
    for path in paths if isinstance(paths, list) else [paths]:
        key, value = _lookup(card, path)
        key = key.lower()
        is_text = is_text or key in ctx.schema.text_fields
        sep = ctx.schema.split.get(key)
        if sep and isinstance(value, str):
            value = [part.strip() for part in value.split(sep) if part.strip()]
        if not isinstance(paths, list):
            return value, is_text
        values += value if isinstance(value, list) else ([] if value is None else [value])
    return values, is_text


# ----- evaluation ----------------------------------------------------------------

# arkham.build's number placeholders, for fields like cost: "x" is X, etc.
NUMBER_WORDS = {"x": -2, "*": -3, "?": -4, "-": None}


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_num(v):
    if _is_num(v):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in NUMBER_WORDS:
            return NUMBER_WORDS[s]
        try:
            return float(s) if "." in s else int(s)
        except ValueError:
            return None
    return None


def _is_null(v) -> bool:
    return v is None or v == "" or v == []


def _words(s: str) -> list[str]:
    return [w for w in normalize_title(s).split("_") if w]


def _match(x, y, op: str, is_text: bool) -> bool:
    """One value against another for a positive operator (= == ? ?? > < >= <=)."""
    if isinstance(y, re.Pattern) or isinstance(x, re.Pattern):
        pattern, other = (y, x) if isinstance(y, re.Pattern) else (x, y)
        return other is not None and op in ("=", "==", "?", "??") and bool(pattern.search(str(other)))
    if isinstance(x, bool) or isinstance(y, bool):
        return op in ("=", "==", "?", "??") and bool(x) == bool(y)
    if _is_num(x) or _is_num(y):
        a, b = _as_num(x), _as_num(y)
        if a is None or b is None:
            return False
        return {"=": a == b, "==": a == b, "?": a == b, "??": a == b,
                ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
    if x is None or y is None or op in (">", "<", ">=", "<="):
        return False
    x, y = str(x), str(y)
    if op in ("=", "?"):
        hay = normalize_title(x)
        needle = _words(y)
        return all(w in hay for w in needle) if needle else x.strip() == y.strip()
    if is_text:
        return y.casefold() in x.casefold()
    return x.casefold() == y.casefold()


def _items(v) -> list:
    return v if isinstance(v, list) else [v]


class Query:
    """A parsed query; `matches(card, ctx)` tests one card."""

    def __init__(self, text: str, tree):
        self.text = text
        self.tree = tree

    def check_fields(self, schema: QuerySchema) -> None:
        """Raise QueryError for any field name the game doesn't have."""
        for f in _fields_in(self.tree):
            bare = f.name.split(".", 1)[1] if f.name.startswith(("raw.", "attrs.")) else f.name
            if bare not in schema.known:
                hint = "; put text in quotes" if f.name.isalpha() else ""
                raise QueryError(f"Unknown field {f.name!r}{hint}", f.pos)

    def matches(self, card, ctx: Context) -> bool:
        return bool(self._cond(self.tree, card, ctx))

    def _cond(self, node, card, ctx) -> bool:
        if isinstance(node, BoolOp):
            if node.op == "&":
                return self._cond(node.left, card, ctx) and self._cond(node.right, card, ctx)
            return self._cond(node.left, card, ctx) or self._cond(node.right, card, ctx)
        if isinstance(node, Truthy):
            v, _ = self._value(node.operand, card, ctx)
            return not _is_null(v) and v is not False and v != 0
        return self._compare(node, card, ctx)

    def _compare(self, node: Compare, card, ctx) -> bool:
        negate = node.op.startswith("!")
        op = node.op[1:] if negate else node.op   # != -> =, !== -> ==, !? -> ?, !?? -> ??
        left, left_text = self._value(node.left, card, ctx)
        right, right_text = self._value(node.right, card, ctx)
        if isinstance(node.right, Literal) and node.right.value is None:
            result = _is_null(left)
        elif isinstance(node.left, Literal) and node.left.value is None:
            result = _is_null(right)
        else:
            is_text = left_text or right_text
            # A boolean literal coerces a missing value to false.
            if isinstance(right, bool) and left is None:
                left = False
            if isinstance(left, bool) and right is None:
                right = False
            result = any(_match(x, y, op, is_text) for x in _items(left) for y in _items(right))
        return not result if negate else result

    def _value(self, node, card, ctx) -> tuple[Any, bool]:
        if isinstance(node, Literal):
            return node.value, False
        if isinstance(node, ListLit):
            out = []
            for item in node.items:
                v, _ = self._value(item, card, ctx)
                out += _items(v)
            return out, False
        if isinstance(node, Field):
            return resolve(card, node.name, ctx)
        if isinstance(node, Neg):
            v = _as_num(self._value(node.operand, card, ctx)[0])
            return (None if v is None else -v), False
        if isinstance(node, Arith):
            a = _as_num(self._value(node.left, card, ctx)[0])
            b = _as_num(self._value(node.right, card, ctx)[0])
            if a is None or b is None:
                return None, False
            if node.op in ("/", "%") and b == 0:
                return None, False
            r = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                 "/": lambda: a / b, "%": lambda: a % b}[node.op]()
            return (int(r) if isinstance(r, float) and r.is_integer() else r), False
        if isinstance(node, (Compare, BoolOp, Truthy)):
            return self._cond(node, card, ctx), False
        raise QueryError("Can't evaluate this")


def parse(text: str) -> Query | None:
    """Parse a query. Blank text gives None (no query)."""
    if not text or not text.strip():
        return None
    return Query(text, _Parser(tokenize(text)).parse())
