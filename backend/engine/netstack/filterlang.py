"""Display-filter mini-language for the capture inspector (NG-CAP-02).

A tiny, safe, Wireshark-flavoured expression language evaluated against
:class:`CaptureRecord` layer dicts — no eval(), no attribute access, just a
recursive-descent parser over a fixed field table.

    icmp && ip.addr == 10.0.0.1
    (tcp.port == 179 || ospf) && !stp
    ipv6.src == 2001:db8::1 && icmpv6.type == 128
    frame.size > 100 && vlan == 20

Grammar:
    expr  := term (('&&' | 'or' | '||' | 'and') term)*
    term  := ['!' | 'not'] atom
    atom  := '(' expr ')' | proto | field op value
    op    := '==' | '!=' | '>' | '<' | '>=' | '<='
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

Predicate = Callable[[dict], bool]

# Protocol atoms -> layer key present in CaptureRecord.layers.
_PROTOS = {
    "eth": "eth", "arp": "arp", "stp": "stp", "ip": "ipv4", "ipv4": "ipv4",
    "ipv6": "ipv6", "icmp": "icmp", "icmpv6": "icmpv6", "tcp": "tcp",
    "udp": "udp", "dns": "dns", "dhcp": "dhcp", "ospf": None, "bgp": None,
    "vrrp": None,
}

# field name -> (layer, [keys]) — multiple keys = any-of (ip.addr, tcp.port).
_FIELDS: dict[str, tuple[str, list[str]]] = {
    "eth.src": ("eth", ["src"]),
    "eth.dst": ("eth", ["dst"]),
    "eth.addr": ("eth", ["src", "dst"]),
    "vlan": ("eth", ["vlan"]),
    "ip.src": ("ipv4", ["src"]),
    "ip.dst": ("ipv4", ["dst"]),
    "ip.addr": ("ipv4", ["src", "dst"]),
    "ip.ttl": ("ipv4", ["ttl"]),
    "ip.proto": ("ipv4", ["proto"]),
    "ipv6.src": ("ipv6", ["src"]),
    "ipv6.dst": ("ipv6", ["dst"]),
    "ipv6.addr": ("ipv6", ["src", "dst"]),
    "icmp.type": ("icmp", ["type"]),
    "icmp.seq": ("icmp", ["seq"]),
    "icmpv6.type": ("icmpv6", ["type"]),
    "tcp.port": ("tcp", ["src_port", "dst_port"]),
    "tcp.src_port": ("tcp", ["src_port"]),
    "tcp.dst_port": ("tcp", ["dst_port"]),
    "udp.port": ("udp", ["src_port", "dst_port"]),
    "udp.src_port": ("udp", ["src_port"]),
    "udp.dst_port": ("udp", ["dst_port"]),
    "dns.qname": ("dns", ["qname"]),
    "frame.size": ("", ["size"]),        # record-level fields
    "frame.iface": ("", ["iface"]),
    "frame.dir": ("", ["dir"]),
}

_TOKEN = re.compile(
    r"\s*(?:(?P<op>&&|\|\||==|!=|>=|<=|[!()><])|(?P<word>[A-Za-z0-9_.:/-]+))"
)


def _tokenize(text: str) -> list[str]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None or m.end() == pos:
            if text[pos:].strip():
                raise ValueError(f"bad character at: {text[pos:][:12]!r}")
            break
        out.append(m.group("op") or m.group("word"))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.toks = tokens
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> str:
        tok = self.peek()
        if tok is None:
            raise ValueError("unexpected end of filter")
        self.i += 1
        return tok

    # expr := term (bool-op term)*
    def expr(self) -> Predicate:
        left = self.term()
        while self.peek() in ("&&", "||", "and", "or"):
            op = self.next()
            right = self.term()
            if op in ("&&", "and"):
                left = lambda r, a=left, b=right: a(r) and b(r)
            else:
                left = lambda r, a=left, b=right: a(r) or b(r)
        return left

    def term(self) -> Predicate:
        if self.peek() in ("!", "not"):
            self.next()
            inner = self.term()
            return lambda r: not inner(r)
        return self.atom()

    def atom(self) -> Predicate:
        tok = self.next()
        if tok == "(":
            inner = self.expr()
            if self.next() != ")":
                raise ValueError("missing ')'")
            return inner
        if tok in _FIELDS and self.peek() in ("==", "!=", ">", "<", ">=", "<="):
            op = self.next()
            value = self.next()
            return _field_predicate(tok, op, value)
        if tok in _PROTOS:
            return _proto_predicate(tok)
        raise ValueError(f"unknown token {tok!r}")


def _proto_predicate(name: str) -> Predicate:
    layer = _PROTOS[name]
    if layer is not None:
        return lambda r: layer in r.get("layers", {})
    # ospf/bgp/vrrp ride inside other layers — match on the summary line.
    needle = name.upper()
    return lambda r: needle in r.get("info", "").upper()


def _field_predicate(field: str, op: str, raw: str) -> Predicate:
    layer, keys = _FIELDS[field]
    want_num = _as_num(raw)

    def compare(actual: Any) -> bool:
        if actual is None:
            return False
        a_num = _as_num(actual)
        if want_num is not None and a_num is not None:
            a, b = a_num, want_num
        else:
            a, b = _canon(actual), _canon(raw)
            if op in (">", "<", ">=", "<="):
                return False
        return {
            "==": a == b, "!=": a != b, ">": a > b, "<": a < b,
            ">=": a >= b, "<=": a <= b,
        }[op]

    def pred(r: dict) -> bool:
        source = r.get("layers", {}).get(layer) if layer else r
        if not isinstance(source, dict):
            return False
        hits = [compare(source.get(k)) for k in keys]
        # ip.addr==X means "either end matches"; != means "neither end is X".
        return all(hits) if op == "!=" else any(hits)

    return pred


def _as_num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def _canon(v: Any) -> str:
    """Canonical string compare — normalizes IPv6 spellings when possible."""
    s = str(v).lower()
    try:
        from ipaddress import ip_address

        return str(ip_address(s))
    except ValueError:
        return s


def compile_filter(text: str) -> Predicate:
    """Compile filter text to a predicate over ``CaptureRecord.as_dict()``.

    Raises ``ValueError`` with a human-readable message on syntax errors.
    """
    tokens = _tokenize(text)
    if not tokens:
        return lambda _r: True
    parser = _Parser(tokens)
    pred = parser.expr()
    if parser.peek() is not None:
        raise ValueError(f"unexpected trailing token {parser.peek()!r}")
    return pred
