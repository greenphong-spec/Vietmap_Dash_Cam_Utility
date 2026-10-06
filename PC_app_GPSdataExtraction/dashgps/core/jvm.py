"""
Small helpers that reproduce JVM number/text behaviour, so the Python port makes exactly
the same decisions and writes exactly the same text as the Android (Kotlin) app:

- Java Math.round rounds half up; Python round() rounds half to even.
- Java String.format("%.Nf") rounds the shortest decimal form half up; Python formats the
  exact binary value half-even ("%.1f" of 0.25: Java "0.3", Python "0.2").
- Kotlin sumOf adds left to right; Python 3.12+ sum() of floats is compensated, which can
  differ in the last bit and flip a threshold decision.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

EPOCH = datetime(1970, 1, 1)


def jround(x: float) -> int:
    """java.lang.Math.round(double)."""
    return math.floor(x + 0.5)


def jfmt(x: float, decimals: int) -> str:
    """String.format(Locale.US, "%.<decimals>f", x)."""
    if math.isnan(x) or math.isinf(x):
        return "NaN" if math.isnan(x) else ("Infinity" if x > 0 else "-Infinity")
    q = Decimal(1).scaleb(-decimals)
    return str(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


def ksum(values) -> float:
    """Kotlin sumOf over doubles: plain left-to-right addition."""
    s = 0.0
    for v in values:
        s += v
    return s


def epoch_seconds(t: datetime) -> float:
    """LocalDateTime.toEpochSecond(ZoneOffset.UTC) as a double."""
    d = t - EPOCH
    return float(d.days * 86400 + d.seconds)


def from_epoch_seconds(s: int) -> datetime:
    return EPOCH + timedelta(seconds=s)


def seconds_between(a: datetime, b: datetime) -> int:
    """java.time.Duration.between(a, b).getSeconds() (floors)."""
    d = b - a
    return d.days * 86400 + d.seconds


def ldt_to_string(t: datetime) -> str:
    """LocalDateTime.toString(): seconds omitted when they and the fraction are zero."""
    s = t.strftime("%Y-%m-%dT%H:%M")
    if t.second or t.microsecond:
        s += f":{t.second:02d}"
        if t.microsecond:
            ms, us = divmod(t.microsecond, 1000)
            s += f".{ms:03d}" if us == 0 else f".{t.microsecond:06d}"
    return s
