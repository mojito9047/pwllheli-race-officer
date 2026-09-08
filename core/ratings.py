"""Pure IRC/YTC rating-band parsing and classification.

Extracted verbatim from app.py. Pure functions of their arguments -- no Flask,
database or module-global dependencies.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional


def rating_type_from_class_name(name: str, fallback: str = "IRC") -> str:
    """Infer IRC/YTC from a class name prefix."""
    n = (name or "").strip().upper()
    if n.startswith("YTC"):
        return "YTC"
    if n.startswith("IRC"):
        return "IRC"
    fb = (fallback or "IRC").strip().upper()
    return "YTC" if fb.startswith("YTC") else "IRC"


def parse_band_expression(expr: str) -> Dict[str, Any]:
    """Parse human-readable rating bands such as '1.000 < rating < 1.099'."""
    text = (expr or "").strip()
    cleaned = text.lower().replace("≤", "<=").replace("≥", ">=").replace("rating", " rating ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    num = r"(-?\d+(?:\.\d+)?)"
    op = r"(<=|>=|<|>)"
    band: Dict[str, Any] = {
        "expr": text,
        "min": None,
        "max": None,
        "min_inclusive": False,
        "max_inclusive": False,
    }

    # a < rating < b, or a <= rating <= b
    m = re.fullmatch(rf"{num}\s*{op}\s*rating\s*{op}\s*{num}", cleaned)
    if m:
        left, op1, op2, right = float(m.group(1)), m.group(2), m.group(3), float(m.group(4))
        if op1 in ("<", "<=") and op2 in ("<", "<="):
            band.update({"min": left, "max": right, "min_inclusive": op1 == "<=", "max_inclusive": op2 == "<="})
            return band
        if op1 in (">", ">=") and op2 in (">", ">="):
            band.update({"min": right, "max": left, "min_inclusive": op2 == ">=", "max_inclusive": op1 == ">="})
            return band

    # rating < b, rating <= b, rating > a, rating >= a
    m = re.fullmatch(rf"rating\s*{op}\s*{num}", cleaned)
    if m:
        oper, value = m.group(1), float(m.group(2))
        if oper in ("<", "<="):
            band.update({"max": value, "max_inclusive": oper == "<="})
        else:
            band.update({"min": value, "min_inclusive": oper == ">="})
        return band

    # a < rating, a <= rating, a > rating, a >= rating
    m = re.fullmatch(rf"{num}\s*{op}\s*rating", cleaned)
    if m:
        value, oper = float(m.group(1)), m.group(2)
        if oper in ("<", "<="):
            band.update({"min": value, "min_inclusive": oper == "<="})
        else:
            band.update({"max": value, "max_inclusive": oper == ">="})
        return band

    # Bare min,max fallback for admin convenience.
    parts = [p.strip() for p in re.split(r"[;,]", text) if p.strip()]
    if len(parts) >= 1:
        try:
            band["min"] = float(parts[0]) if parts[0] not in ("-", "*") else None
            if len(parts) >= 2:
                band["max"] = float(parts[1]) if parts[1] not in ("-", "*") else None
        except Exception:
            pass
    return band


def rating_band_display(rule: Dict[str, Any]) -> str:
    """Return a readable display string for a class rating band."""
    expr = str(rule.get("expr") or "").strip()
    if expr:
        return expr
    parts = []
    if rule.get("min") is not None:
        parts.append(f"rating {'≥' if rule.get('min_inclusive') else '>'} {rule.get('min'):g}")
    if rule.get("max") is not None:
        parts.append(f"rating {'≤' if rule.get('max_inclusive') else '<'} {rule.get('max'):g}")
    return " and ".join(parts) if parts else "all ratings"


def rating_in_class_band(rating: Optional[float], rule: Dict[str, Any]) -> bool:
    """Return True if rating fits the configured class band."""
    if rating is None:
        return False
    r = float(rating)
    mn = rule.get("min")
    mx = rule.get("max")
    if mn is not None:
        mn = float(mn)
        if rule.get("min_inclusive"):
            if r < mn:
                return False
        elif r <= mn:
            return False
    if mx is not None:
        mx = float(mx)
        if rule.get("max_inclusive"):
            if r > mx:
                return False
        elif r >= mx:
            return False
    return True
