"""The person's role in a paper (first author, contributor… last author), from their
position among the authors: an ordered list of rules (the first that matches wins, else the
catch-all role), set in Settings → Contribution roles.

A rule's condition is a formula on ``n`` (the number of authors), ``p`` (the person's
position, from 1) and ``phd`` (one of their PhD students is an author), e.g.
``n>=12 and p>=25% and p<=75%``. Compared with ``p``, a negative number counts from the end
(``p=-1``: the last author, ``p>=-3``: one of the last three) and a percentage is the place
in the list (0%: first, 100%: last). Operators: ``= != < <= > >=``, ``and``, ``or``, ``not``
and parentheses.
"""

from __future__ import annotations

import operator
import secrets
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from lark import Lark, Transformer
from lark.exceptions import (
    UnexpectedCharacters,
    UnexpectedEOF,
    UnexpectedToken,
    VisitError,
)

from .db.models import AppSetting
from .db.session import session_scope
from .i18n import _

if TYPE_CHECKING:
    from .pubview import PubStat

KEY = "contribution"  # the roles and rules (an AppSetting)


@dataclass
class Role:
    key: str
    label: str
    colour: str


@dataclass
class Rule:
    role: str  # a Role's key
    condition: str


@dataclass
class Config:
    roles: list[Role]  # in their order on the chart
    rules: list[Rule]  # the first that matches wins
    fallback: str  # the catch-all role (when no rule matches)

    def label(self, key: str) -> str:
        return next((r.label for r in self.roles if r.key == key), key)


def default_config() -> Config:
    return Config(
        roles=[
            Role("sole", "Sole", "#0b4f2a"),
            Role("first", "First", "#1a7f37"),
            Role("contributor", "Contributor", "#8cc99a"),
            Role("involved", "Involved", "#c4c4c4"),
            Role("supervisor", "Supervisor", "#8fb3e8"),
            Role("last", "Last", "#1f5fbf"),
        ],
        rules=[
            Rule("sole", "n=1"),
            Rule("involved", "n>=12 and p>=25% and p<=75%"),
            Rule("first", "p=1"),
            Rule("last", "p=-1"),
            Rule("supervisor", "p>=-3"),  # (one of the last three, but the last)
            Rule("supervisor", "phd and p>1"),
            Rule("contributor", "p=2 or p=3"),
        ],
        fallback="involved",
    )


def new_role_key(cfg: Config) -> str:
    keys = {r.key for r in cfg.roles}
    while (key := f"r{secrets.token_hex(3)}") in keys:
        pass
    return key


# ---- conditions --------------------------------------------------------------------------

_OPS = {
    "=": operator.eq,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}

_GRAMMAR = r"""
?start: or_
?or_: and_ | or_ "or"i and_ -> or_
?and_: not_ | and_ "and"i not_ -> and_
?not_: "not"i not_ -> not_ | atom
?atom: "(" or_ ")" | "phd"i -> phd | operand OP operand -> cmp
operand: VAR -> var | PERCENT -> percent | SIGNED_INT -> num
VAR: "n"i | "p"i
OP: "<=" | ">=" | "!=" | "==" | "=" | "<" | ">"
PERCENT.2: /\d+%/
%import common.SIGNED_INT
%import common.WS
%ignore WS
"""


class ConditionError(ValueError):
    pass


class _Tree(Transformer):
    """The parse tree → nested tuples, evaluated by _eval."""

    def or_(self, items):
        return ("or", *items)

    def and_(self, items):
        return ("and", *items)

    def not_(self, items):
        return ("not", *items)

    def phd(self, _items):
        return ("phd",)

    def var(self, items):
        return ("var", str(items[0]).lower())

    def percent(self, items):
        return ("%", int(str(items[0])[:-1]))

    def num(self, items):
        return ("num", int(items[0]))

    def cmp(self, items):
        left, op, right = items
        if ("%" in (left[0], right[0])) and ("var", "p") not in (left, right):
            raise ConditionError(_("A percentage is compared with p"))
        return ("cmp", str(op), left, right)


_parser = Lark(_GRAMMAR, parser="lalr", transformer=_Tree())


@lru_cache(maxsize=256)
def parse_condition(text: str) -> Any:
    """The condition's tree (an empty condition always holds); ConditionError if invalid."""
    if not (text or "").strip():
        return ("true",)
    try:
        return _parser.parse(text)
    except UnexpectedEOF:
        raise ConditionError(_("Incomplete condition")) from None
    except UnexpectedToken as e:
        if e.token.type == "$END":
            raise ConditionError(_("Incomplete condition")) from None
        raise ConditionError(
            _("Unexpected {token} at {column}").format(token=repr(str(e.token)), column=e.column)
        ) from None
    except UnexpectedCharacters as e:
        raise ConditionError(
            _("Unexpected {token} at {column}").format(token=repr(e.char), column=e.column)
        ) from None
    except VisitError as e:
        if isinstance(e.orig_exc, ConditionError):
            raise e.orig_exc from None
        raise


def check_condition(text: str) -> str | None:
    """The error in a condition, if any."""
    try:
        parse_condition(text)
    except ConditionError as e:
        return str(e)
    return None


def _value(operand, other, p: int, n: int | None) -> float | None:
    """An operand's value (None: unknown, the number of authors being unknown)."""
    kind, v = operand
    against_p = other == ("var", "p")
    if kind == "var":
        if v == "p" and other[0] == "%":
            return None if n is None else (100 * (p - 1) / (n - 1) if n > 1 else 0)
        return p if v == "p" else n
    if kind == "%":
        return v
    if v < 0 and against_p:  # (from the end)
        return None if n is None else n + 1 + v
    return v


def _eval(tree, p: int, n: int | None, phd: bool) -> bool | None:
    """Three-valued (None: unknown), so that an unknown number of authors matches nothing
    that depends on it."""
    kind = tree[0]
    if kind == "true":
        return True
    if kind == "phd":
        return phd
    if kind == "not":
        v = _eval(tree[1], p, n, phd)
        return None if v is None else not v
    if kind in ("and", "or"):
        a, b = _eval(tree[1], p, n, phd), _eval(tree[2], p, n, phd)
        if kind == "and":
            return False if False in (a, b) else None if None in (a, b) else True
        return True if True in (a, b) else None if None in (a, b) else False
    _kind, op, left, right = tree
    a, b = _value(left, right, p, n), _value(right, left, p, n)
    return None if a is None or b is None else _OPS[op](a, b)


def matches(condition: str, p: int, n: int | None, phd: bool) -> bool:
    return _eval(parse_condition(condition), p, n, phd) is True


def role(s: PubStat, cfg: Config) -> str | None:
    """The person's role in a paper (a Role's key): the first rule that matches, else the
    catch-all (also when the order does not matter, position 0); None if their position is
    unknown."""
    if s.author_pos is None:
        return None
    if s.author_pos == 0:
        return cfg.fallback
    phd = "student" in s.author_marks
    for r in cfg.rules:
        try:
            if matches(r.condition, s.author_pos, s.num_authors, phd):
                return r.role
        except ConditionError:
            continue
    return cfg.fallback


# ---- settings ----------------------------------------------------------------------------


def load_config() -> Config:
    with session_scope() as s:
        row = s.get(AppSetting, KEY)
        saved = dict(row.value or {}) if row else {}
    if not saved.get("roles"):
        return default_config()
    return Config(
        roles=[Role(**r) for r in saved["roles"]],
        rules=[Rule(r["role"], r.get("condition", "")) for r in saved.get("rules", [])],
        fallback=saved.get("fallback") or saved["roles"][0]["key"],
    )


def validate(cfg: Config) -> None:
    """ValueError when the configuration cannot be used."""
    if not cfg.roles:
        raise ValueError(_("At least one role is needed"))
    labels = [r.label.strip() for r in cfg.roles]
    if not all(labels):
        raise ValueError(_("A role has no name"))
    if len(set(labels)) < len(labels):
        raise ValueError(_("Two roles have the same name"))
    keys = {r.key for r in cfg.roles}
    if cfg.fallback not in keys:
        raise ValueError(_("The catch-all role is unknown"))
    for i, r in enumerate(cfg.rules, 1):
        if r.role not in keys:
            raise ValueError(_("Rule {n}: unknown role").format(n=i))
        if error := check_condition(r.condition):
            raise ValueError(_("Rule {n}: {error}").format(n=i, error=error))


def save_config(cfg: Config) -> None:
    validate(cfg)
    with session_scope() as s:
        s.merge(AppSetting(key=KEY, value=asdict(cfg)))


def copy(cfg: Config) -> Config:
    return Config(
        roles=[Role(**asdict(r)) for r in cfg.roles],
        rules=[Rule(**asdict(r)) for r in cfg.rules],
        fallback=cfg.fallback,
    )
