from __future__ import annotations

import re

_PIGEON_TAIL_REPEAT = re.compile(r"^(.*?)(:\d+::\d+:\d+:pigeon)(?:\2)+$")


def normalize_pigeon_conversation_id(conv_id: str = "") -> str:
    """Collapse duplicated pigeon conversation tail segments."""
    value = str(conv_id or "").strip()
    if not value:
        return value
    matched = _PIGEON_TAIL_REPEAT.match(value)
    if matched:
        return f"{matched.group(1)}{matched.group(2)}"
    return value


def normalize_route_key(key: str = "") -> str:
    """Merge n-prefix and plain security conversation ids for the same buyer."""
    value = str(key or "").strip()
    if value.startswith("n") and len(value) > 24 and value[1:2].isalpha():
        value = value[1:]
    return normalize_pigeon_conversation_id(value)


def conversation_ids_match(expected: str = "", current: str = "") -> bool:
    """True when two Feige conversation ids refer to the same buyer session."""
    left = str(expected or "").strip()
    right = str(current or "").strip()
    if not left or not right:
        return False
    left_variants = set(conv_id_variants(left))
    right_variants = set(conv_id_variants(right))
    if left_variants.intersection(right_variants):
        return True
    left_norm = normalize_route_key(left)
    right_norm = normalize_route_key(right)
    if left_norm and right_norm and left_norm == right_norm:
        return True
    # Probe/UI ids can differ slightly from WS security ids at the tail.
    for a in (left, left_norm):
        for b in (right, right_norm):
            if not a or not b:
                continue
            if a.startswith(b) or b.startswith(a):
                if min(len(a), len(b)) >= 40:
                    return True
    return False


def conv_id_variants(conv_id: str = "") -> list[str]:
    value = str(conv_id or "").strip()
    if not value:
        return []
    variants: list[str] = []
    for candidate in (value, normalize_route_key(value)):
        if candidate and candidate not in variants:
            variants.append(candidate)
    if not value.startswith("n") and value.startswith("AQ"):
        prefixed = f"n{value}"
        if prefixed not in variants:
            variants.append(prefixed)
    return variants
