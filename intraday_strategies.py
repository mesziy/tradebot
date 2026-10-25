"""Day-trading strategies on 5-minute bars, shared by intraday_backtest.py.

Each strategy takes a DataFrame of regular-session 5-minute bars (09:30-16:00
New York time) with columns open/high/low/close/volume/vwap plus helper
columns added by prepare(): day, bar (0 = 09:30 bar), time, prev_close.
It returns a 0/1 "position" Series: decided at each bar's close, filled at the
next bar's open. Every position is closed at the end of the day, so nothing is
held overnight. All strategies are long-only (small accounts can't short).
"""

from datetime import time

import pandas as pd


def prepare(bars: pd.DataFrame) -> pd.DataFrame:
    """Add day / bar / time / prev_close columns to New York-time 5-minute bars."""
    df = bars.copy()
    df["day"] = df.index.date
    df["time"] = df.index.time
    df["bar"] = df.groupby("day").cumcount()
    day_close = df.groupby("day")["close"].last()
    df["prev_close"] = df["day"].map(day_close.shift(1))
    return df


def _by_day(mask: pd.Series, df: pd.DataFrame) -> pd.Series:
    """True from the first bar where `mask` is True until the end of that day."""
    return mask.astype(int).groupby(df["day"]).cummax().astype(bool)


def orb(df: pd.DataFrame, minutes: int = 5) -> pd.Series:
    """Opening range breakout, long side (Zarattini & Aziz, 2023).

    If the first `minutes` of the day close up, buy at the next bar and hold to
    the close, exiting early if a bar closes below the opening range low.
    """
    n = minutes // 5
    first_open = df.groupby("day")["open"].transform("first")
    or_close = df["day"].map(df[df["bar"] == n - 1].set_index("day")["close"])
    or_low = df["day"].map(df[df["bar"] < n].groupby("day")["low"].min())
    bullish = or_close > first_open
    started = df["bar"] >= n - 1
    stopped = _by_day(started & (df["close"] < or_low), df)
    return (bullish & started & ~stopped).astype(float)


def orb_breakout(df: pd.DataFrame, minutes: int = 30) -> pd.Series:
    """Classic opening range breakout: after the first `minutes`, buy when a bar
    closes above the range high; sell if one closes below the range low."""
    n = minutes // 5
    in_range = df["bar"] < n
    or_high = df["day"].map(df[in_range].groupby("day")["high"].max())
    or_low = df["day"].map(df[in_range].groupby("day")["low"].min())
    entered = _by_day(~in_range & (df["close"] > or_high), df)
    exited = _by_day(entered & (df["close"] < or_low), df)
    return (entered & ~exited).astype(float)


def vwap_trend(df: pd.DataFrame) -> pd.Series:
    """Hold while price is above the day's VWAP (Zarattini & Aziz, 2023)."""
    pv = (df["vwap"] * df["volume"]).groupby(df["day"]).cumsum()
    session_vwap = pv / df["volume"].groupby(df["day"]).cumsum()
    return (df["close"] > session_vwap).astype(float)


def intraday_momentum(df: pd.DataFrame) -> pd.Series:
    """Market intraday momentum (Gao, Han, Li & Zhou, 2018).

    If the return from yesterday's close to 10:00 is positive, hold for the
    last half hour (buy 15:30, sell at the close).
    """
    at_10 = df[df["time"] == time(9, 55)].set_index("day")["close"]  # the 9:55 bar closes at 10:00
    first_half_hour = df["day"].map(at_10) / df["prev_close"] - 1
    return ((first_half_hour > 0) & (df["time"] >= time(15, 25))).astype(float)


def gap_fade(df: pd.DataFrame, min_gap: float = 0.005) -> pd.Series:
    """Buy a gap down of at least `min_gap` at the open and sell when the price
    gets back to yesterday's close (or at the end of the day)."""
    first_open = df.groupby("day")["open"].transform("first")
    gapped_down = first_open / df["prev_close"] - 1 <= -min_gap
    filled = _by_day(df["close"] >= df["prev_close"], df)
    return (gapped_down & ~filled).astype(float)


STRATEGIES = {
    "orb": orb,
    "orb_breakout": orb_breakout,
    "vwap_trend": vwap_trend,
    "intraday_momentum": intraday_momentum,
    "gap_fade": gap_fade,
}
