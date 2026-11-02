"""Minimal cron matcher — 5 fields: minute hour day-of-month month day-of-week.

Supports *, lists, ranges, and step values (e.g. */15, 1-5/2).
No vixie-cron day-of-week alias names; numeric 0-6 (0=Sunday).
"""
from __future__ import annotations

from datetime import datetime

MAX_RANGE = 100000  # guard against absurd step expansion


def _parse_field(field: str, lo: int, hi: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty field")
        step = 1
        if part == "*":
            base = (lo, hi)
        elif "/" in part:
            base_raw, step_raw = part.split("/", 1)
            base = (lo, hi) if base_raw == "*" else _range(base_raw, lo, hi)
            step = int(step_raw)
            if step < 1:
                raise ValueError("step must be >= 1")
        else:
            base = _range(part, lo, hi)
        if len(base) > MAX_RANGE:
            raise ValueError("field range too large")
        values.update(range(base[0], base[1] + 1, step))
        if len(values) > MAX_RANGE:
            raise ValueError("field expands too large")
    return values


def _range(spec: str, lo: int, hi: int) -> tuple[int, int]:
    if "-" in spec:
        a, b = spec.split("-", 1)
        a, b = int(a), int(b)
    else:
        a = b = int(spec)
    if a < lo or b > hi or a > b:
        raise ValueError(f"value out of range {lo}-{hi}: {spec}")
    return a, b


def parse_cron(spec: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    parts = spec.split()
    if len(parts) != 5:
        raise ValueError("cron must have exactly 5 fields: minute hour dom month dow")
    return (
        _parse_field(parts[0], 0, 59),
        _parse_field(parts[1], 0, 23),
        _parse_field(parts[2], 1, 31),
        _parse_field(parts[3], 1, 12),
        _parse_field(parts[4], 0, 6),
    )


def cron_matches(spec: str, dt: datetime) -> bool:
    minutes, hours, dom, months, dow = parse_cron(spec)
    return (
        dt.minute in minutes
        and dt.hour in hours
        and dt.day in dom
        and dt.month in months
        and dt.weekday() in {(d - 1) % 7 for d in dow}  # cron: Sun=0; py: Mon=0
    )


def cron_is_valid(spec: str) -> str:
    """Return '' if valid, else an error message."""
    if not spec.strip():
        return ""
    try:
        parse_cron(spec.strip())
        return ""
    except (ValueError, TypeError) as exc:
        return f"invalid cron '{spec}': {exc}"
