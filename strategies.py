"""Trading strategies shared by bot.py (live) and backtest.py (history).

Each strategy takes a daily price DataFrame (Open/High/Low/Close) and returns a
DataFrame with a "position" column: 1 = hold the stock, 0 = stay in cash,
NaN = not enough history yet. Other columns are indicators shown in the log.
Positions are decided at the close; the bot acts when the position changes.
All strategies are long-only.
"""

import pandas as pd


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def _rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def _monthly(signal: pd.Series) -> pd.Series:
    """Only let a 0/1 signal change once a month.

    On the first trading day of each month, take the signal from the last close
    of the previous month and hold it all month. This works live too, because
    it never needs to know whether today is the last day of the month.
    """
    new_month = pd.Series(signal.index.month, index=signal.index).diff().fillna(0) != 0
    return signal.shift(1).where(new_month).ffill()


def ma_crossover(df: pd.DataFrame, short: int = 20, long: int = 50) -> pd.DataFrame:
    """Hold while the short moving average is above the long one."""
    out = pd.DataFrame(index=df.index)
    out[f"{short}d MA"] = _sma(df["Close"], short)
    out[f"{long}d MA"] = _sma(df["Close"], long)
    diff = out[f"{short}d MA"] - out[f"{long}d MA"]
    out["position"] = (diff > 0).astype(float).where(diff.notna())
    return out


def trend_monthly(df: pd.DataFrame, window: int = 200) -> pd.DataFrame:
    """Hold while the price is above its long average, checked monthly (Faber, 2007)."""
    out = pd.DataFrame(index=df.index)
    out["close"] = df["Close"]
    out[f"{window}d MA"] = ma = _sma(df["Close"], window)
    above = (df["Close"] > ma).astype(float).where(ma.notna())
    out["position"] = _monthly(above)
    return out


def momentum_monthly(df: pd.DataFrame, lookback: int = 252) -> pd.DataFrame:
    """Hold while the past ~12-month return is positive, checked monthly
    (time-series momentum, Moskowitz/Ooi/Pedersen, 2012)."""
    out = pd.DataFrame(index=df.index)
    out["12m return"] = ret = df["Close"] / df["Close"].shift(lookback) - 1
    out["position"] = _monthly((ret > 0).astype(float).where(ret.notna()))
    return out


def rsi2(df: pd.DataFrame, period: int = 2, entry: float = 10, exit_ma: int = 5,
         trend: int = 200) -> pd.DataFrame:
    """Buy short, sharp dips in an uptrend; sell on the bounce (Connors, 2008).

    Buy when RSI(2) is below `entry` and the price is above its 200-day average.
    Sell when the price closes above its 5-day average.
    """
    close = df["Close"]
    out = pd.DataFrame(index=df.index)
    out["RSI"] = r = _rsi(close, period)
    out[f"{exit_ma}d MA"] = exit_line = _sma(close, exit_ma)
    out[f"{trend}d MA"] = trend_line = _sma(close, trend)

    positions, pos = [], 0.0
    for c, rv, ex, tr in zip(close, r, exit_line, trend_line):
        if pd.isna(tr):
            positions.append(float("nan"))
            continue
        if pos == 0 and c > tr and rv < entry:
            pos = 1.0
        elif pos == 1 and c > ex:
            pos = 0.0
        positions.append(pos)
    out["position"] = positions
    return out


def donchian(df: pd.DataFrame, entry: int = 55, exit: int = 20) -> pd.DataFrame:
    """Buy a close above the 55-day high; sell a close below the 20-day low
    (the Turtle traders' breakout system)."""
    close = df["Close"]
    out = pd.DataFrame(index=df.index)
    out[f"{entry}d high"] = upper = df["High"].rolling(entry).max().shift(1)
    out[f"{exit}d low"] = lower = df["Low"].rolling(exit).min().shift(1)

    positions, pos = [], 0.0
    for c, up, lo in zip(close, upper, lower):
        if pd.isna(up) or pd.isna(lo):
            positions.append(float("nan"))
            continue
        if pos == 0 and c > up:
            pos = 1.0
        elif pos == 1 and c < lo:
            pos = 0.0
        positions.append(pos)
    out["position"] = positions
    return out


STRATEGIES = {
    "ma_crossover": ma_crossover,
    "trend_monthly": trend_monthly,
    "momentum_monthly": momentum_monthly,
    "rsi2": rsi2,
    "donchian": donchian,
}


def compute(name: str, df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy {name!r}. Options: {', '.join(STRATEGIES)}")
    return STRATEGIES[name](df, **(params or {}))
