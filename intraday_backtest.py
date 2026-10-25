"""Backtest the day-trading strategies in intraday_strategies.py on 5-minute bars.

Price data comes from Alpaca's historical data API (using the keys in .env)
and is cached in data/. Results are shown in HUF for a given budget, with the
focus on month-by-month profit.

Usage:
    python intraday_backtest.py                       # 20,000 HUF, SPY QQQ AAPL TSLA, since 2019
    python intraday_backtest.py --budget 50000 --symbols SPY --cost 0.0005
"""

import argparse
import math
import os
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from dotenv import load_dotenv

import intraday_strategies

warnings.filterwarnings("ignore", module="yfinance")

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
SESSION_START, SESSION_END = "09:30", "15:55"  # 5-minute bars labelled by start time

LABELS = {
    "orb": "Opening range 5m",
    "orb_breakout": "Range breakout 30m",
    "vwap_trend": "VWAP trend",
    "intraday_momentum": "Last half hour mom.",
    "gap_fade": "Gap-down fade",
}


def usd_huf() -> float:
    return float(yf.Ticker("HUF=X").history(period="5d")["Close"].iloc[-1])


def load_bars(symbol: str, start: datetime) -> pd.DataFrame:
    """Regular-session 5-minute bars in New York time, cached as CSV."""
    DATA_DIR.mkdir(exist_ok=True)
    cache = DATA_DIR / f"{symbol}_5min_{start:%Y%m%d}.csv"
    if cache.exists() and datetime.now().timestamp() - cache.stat().st_mtime < 86_400:
        bars = pd.read_csv(cache, index_col=0, parse_dates=True)
        bars.index = pd.to_datetime(bars.index, utc=True).tz_convert("America/New_York")
        return bars

    load_dotenv(HERE / ".env")
    client = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    print(f"Downloading {symbol} 5-minute bars since {start:%Y-%m-%d} (first run only)...", flush=True)
    bars = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=start,
        end=datetime.now() - timedelta(days=1),
        adjustment=Adjustment.ALL,
        feed=DataFeed.SIP,
    )).df.loc[symbol]
    bars.index = bars.index.tz_convert("America/New_York")
    bars = bars.between_time(SESSION_START, SESSION_END)[["open", "high", "low", "close", "volume", "vwap"]]
    bars.to_csv(cache)
    return bars


def simulate(df: pd.DataFrame, position: pd.Series, cost_pct: float) -> tuple[pd.Series, int]:
    """Return (end-of-day equity multiple, number of round trips).

    A position decided at bar i's close is held from bar i+1's open. Anything
    still held is sold at the day's last close.
    """
    held = position.groupby(df["day"]).shift(1).fillna(0)          # holding during bar j (from its open)
    held_before = held.groupby(df["day"]).shift(1).fillna(0)        # holding during the previous bar
    prev_close = df["close"].groupby(df["day"]).shift(1)
    is_last = df["bar"] == df.groupby("day")["bar"].transform("max")

    gap_ret = (df["open"] / prev_close - 1).fillna(0) * held_before  # previous close -> this open
    bar_ret = (df["close"] / df["open"] - 1) * held                  # this open -> this close
    fills = (held != held_before).astype(int) + (is_last & (held == 1)).astype(int)

    growth = (1 + gap_ret) * (1 + bar_ret) * (1 - cost_pct) ** fills
    daily = growth.groupby(df["day"]).prod()
    trips = int((held > held_before).sum())
    return daily.cumprod(), trips


def monthly_stats(equity_usd: pd.Series, start_usd: float, rate: float) -> dict:
    """Month-by-month profit in HUF from an end-of-day USD equity curve."""
    month_end = equity_usd.groupby(pd.PeriodIndex(equity_usd.index, freq="M")).last()
    pnl_huf = month_end.diff().fillna(month_end.iloc[0] - start_usd) * rate
    years = (equity_usd.index[-1] - equity_usd.index[0]).days / 365.25
    daily = equity_usd.pct_change().dropna()
    return {
        "final_huf": equity_usd.iloc[-1] * rate,
        "cagr": (equity_usd.iloc[-1] / start_usd) ** (1 / years) - 1,
        "avg_month": pnl_huf.mean(),
        "median_month": pnl_huf.median(),
        "pos_months": (pnl_huf > 0).mean(),
        "worst_month": pnl_huf.min(),
        "best_month": pnl_huf.max(),
        "max_dd": (equity_usd / equity_usd.cummax() - 1).min(),
        "sharpe": daily.mean() / daily.std() * math.sqrt(252) if daily.std() else 0.0,
        "pnl_huf": pnl_huf,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest day-trading strategies at a HUF budget.")
    p.add_argument("--budget", type=float, default=20_000, help="Starting money in HUF")
    p.add_argument("--symbols", nargs="+", default=["SPY", "QQQ", "AAPL", "TSLA"])
    p.add_argument("--start", default="2019-01-01")
    p.add_argument("--cost", type=float, default=0.0003,
                   help="Spread + slippage per fill as a fraction (default 0.0003 = 0.03%%)")
    args = p.parse_args()

    rate = usd_huf()
    start_usd = args.budget / rate
    start = datetime.strptime(args.start, "%Y-%m-%d")
    print(f"\nBudget {args.budget:,.0f} HUF = ${start_usd:,.2f} at {rate:.2f} HUF/USD "
          f"(fractional shares, converted at today's rate)")
    print(f"Trading cost {args.cost:.2%} per fill. 'No cost' = before spread and slippage.")
    print("Avg/Median/Worst month = profit per calendar month in HUF.\n")

    header = (f"{'':22}{'Yearly':>8}{'No cost':>9}{'Avg mo':>9}{'Median':>9}{'Worst':>9}"
              f"{'Up mo':>7}{'Max DD':>8}{'Trades/mo':>10}{'Final HUF':>11}")

    for symbol in args.symbols:
        df = intraday_strategies.prepare(load_bars(symbol, start))
        days = df.groupby("day")
        months = (df.index[-1] - df.index[0]).days / 30.44

        close = days["close"].last()
        hold = close / df["open"].iloc[0]
        h = monthly_stats(hold * start_usd, start_usd, rate)

        print(f"{symbol}  ({df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}, {len(close)} trading days)")
        print(header)
        print(f"{'Buy & hold (no daytr.)':22}{h['cagr']:>8.1%}{'':>9}{h['avg_month']:>9,.0f}"
              f"{h['median_month']:>9,.0f}{h['worst_month']:>9,.0f}{h['pos_months']:>7.0%}"
              f"{h['max_dd']:>8.1%}{'-':>10}{h['final_huf']:>11,.0f}")

        for name, fn in intraday_strategies.STRATEGIES.items():
            position = fn(df)
            eq, trips = simulate(df, position, args.cost)
            eq_free, _ = simulate(df, position, 0.0)
            s = monthly_stats(eq * start_usd, start_usd, rate)
            free_cagr = monthly_stats(eq_free * start_usd, start_usd, rate)["cagr"]
            print(f"{LABELS[name]:22}{s['cagr']:>8.1%}{free_cagr:>9.1%}{s['avg_month']:>9,.0f}"
                  f"{s['median_month']:>9,.0f}{s['worst_month']:>9,.0f}{s['pos_months']:>7.0%}"
                  f"{s['max_dd']:>8.1%}{trips / months:>10.1f}{s['final_huf']:>11,.0f}")
        print()

    print("Past results don't predict future ones. Costs above exclude currency conversion and taxes.")


if __name__ == "__main__":
    main()
