"""Replay a strategy from strategies.py on historical data.

Follows the same rules as bot.py: buy when the strategy's position switches to
"hold", sell (only if holding) when it switches to "cash". The bot runs after
the close, so each order fills at the next day's open. Results are compared
against simply buying and holding over the same period.

Usage:
    python backtest.py                                   # settings from config.py, 10 years
    python backtest.py --strategy rsi2 --symbol SPY --years 20
    python backtest.py --strategy ma_crossover --param short=50 --param long=200
    python backtest.py --compare                          # every strategy, several symbols
"""

import argparse
import math
import warnings
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

import config
import strategies

warnings.filterwarnings("ignore", module="yfinance")

START_CAPITAL = 10_000.0
WARMUP_DAYS = 600  # calendar days of lead-in so 200/252-day indicators are ready

# Strategy variants tested by --compare: (label, strategy name, params)
COMPARE_SET = [
    ("MA cross 20/50", "ma_crossover", {"short": 20, "long": 50}),
    ("MA cross 50/200", "ma_crossover", {"short": 50, "long": 200}),
    ("Trend 200d monthly", "trend_monthly", {}),
    ("Momentum 12m monthly", "momentum_monthly", {}),
    ("RSI(2) dip buying", "rsi2", {}),
    ("Donchian 55/20", "donchian", {}),
]
COMPARE_SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT"]


def fetch_history(symbol: str, years: int) -> pd.DataFrame:
    start = datetime.now() - timedelta(days=int(years * 365.25) + WARMUP_DAYS)
    df = yf.Ticker(symbol).history(start=start.strftime("%Y-%m-%d"), interval="1d")
    if df.empty:
        raise SystemExit(f"No price data returned for {symbol}")
    return df[["Open", "High", "Low", "Close"]]


def simulate(df: pd.DataFrame, position: pd.Series, years: int, cost_pct: float,
             mode: str = "crossover") -> dict:
    """Trade the position changes; return equity curves, trades and stats.

    In "follow" mode the account starts matched to the strategy (buying on day
    one if it already says hold); in "crossover" mode it waits for the first flip.
    """
    first_valid = position.first_valid_index()
    if first_valid is None:
        raise SystemExit("Not enough history for this strategy")
    test_start = max(first_valid, df.index[-1] - pd.DateOffset(years=years))
    df = df.loc[df.index >= test_start]
    change = position.loc[df.index].diff()

    cash, shares = START_CAPITAL, 0.0
    already_holding = position.shift(1).loc[df.index[0]] == 1
    pending = "buy" if mode == "follow" and already_holding else None
    trades, equity = [], []

    for date, row in df.iterrows():
        # Fill yesterday's signal at today's open.
        if pending == "buy" and shares == 0:
            shares, cash = cash / (row["Open"] * (1 + cost_pct)), 0.0
            trades.append({"entry_date": date, "entry": row["Open"]})
        elif pending == "sell" and shares > 0:
            cash, shares = shares * row["Open"] * (1 - cost_pct), 0.0
            trades[-1].update(exit_date=date, exit=row["Open"])
        pending = "buy" if change[date] > 0 else "sell" if change[date] < 0 else None
        equity.append(cash + shares * row["Close"])

    equity = pd.Series(equity, index=df.index)
    hold = START_CAPITAL * df["Close"] / df["Open"].iloc[0]
    days = (df.index[-1] - df.index[0]).days
    closed = [t for t in trades if "exit" in t]
    in_market = sum(((t.get("exit_date") or df.index[-1]) - t["entry_date"]).days for t in trades)

    return {
        "df": df, "equity": equity, "hold": hold, "trades": trades, "days": days,
        "strategy": stats(equity, days),
        "buy_hold": stats(hold, days),
        "closed": len(closed),
        "wins": sum(t["exit"] > t["entry"] for t in closed),
        "exposure": in_market / days,
    }


def stats(equity: pd.Series, days: int) -> dict:
    daily = equity.pct_change().dropna()
    return {
        "final": equity.iloc[-1],
        "total": equity.iloc[-1] / START_CAPITAL - 1,
        "cagr": (equity.iloc[-1] / START_CAPITAL) ** (365.25 / days) - 1,
        "max_dd": (equity / equity.cummax() - 1).min(),
        # Return per unit of day-to-day volatility (no risk-free rate subtracted).
        "sharpe": daily.mean() / daily.std() * math.sqrt(252) if daily.std() else 0.0,
    }


def run_single(symbol, name, params, years, cost_pct, mode) -> None:
    df = fetch_history(symbol, years)
    r = simulate(df, strategies.compute(name, df, params)["position"], years, cost_pct, mode)
    s, h, d = r["strategy"], r["buy_hold"], r["df"]

    print(f"\n{symbol}  {name} {params or '(defaults)'}  |  "
          f"{d.index[0]:%Y-%m-%d} to {d.index[-1]:%Y-%m-%d} ({r['days'] / 365.25:.1f} years)")
    print(f"Starting capital ${START_CAPITAL:,.0f}, trading cost {cost_pct:.2%} per fill, {mode} mode\n")

    print(f"{'':22}{'Strategy':>14}{'Buy & hold':>14}")
    print(f"{'Final value':22}{s['final']:>14,.0f}{h['final']:>14,.0f}")
    print(f"{'Total return':22}{s['total']:>14.1%}{h['total']:>14.1%}")
    print(f"{'Yearly return (CAGR)':22}{s['cagr']:>14.1%}{h['cagr']:>14.1%}")
    print(f"{'Biggest drop':22}{s['max_dd']:>14.1%}{h['max_dd']:>14.1%}")
    print(f"{'Sharpe ratio':22}{s['sharpe']:>14.2f}{h['sharpe']:>14.2f}")
    print(f"{'Time in market':22}{r['exposure']:>14.0%}{1:>14.0%}")

    trades = r["trades"]
    print(f"\nTrades: {r['closed']} closed"
          + (f", {r['wins']} winners ({r['wins'] / r['closed']:.0%})" if r["closed"] else "")
          + (", 1 still open" if len(trades) > r["closed"] else ""))

    print(f"\n{'Bought':>12}{'at':>10}{'Sold':>13}{'at':>10}{'Return':>9}")
    for t in trades:
        if "exit" in t:
            print(f"{t['entry_date']:%Y-%m-%d}{t['entry']:>10.2f}  {t['exit_date']:%Y-%m-%d}"
                  f"{t['exit']:>10.2f}{t['exit'] / t['entry'] - 1:>9.1%}")
        else:
            last = d["Close"].iloc[-1]
            print(f"{t['entry_date']:%Y-%m-%d}{t['entry']:>10.2f}  {'(open)':>10}"
                  f"{last:>10.2f}{last / t['entry'] - 1:>9.1%}")
    print("\nPrices are adjusted for splits and dividends. Past results don't predict future ones.")


def run_compare(symbols, years, cost_pct) -> None:
    print(f"\nAll strategies, last {years} years, trading cost {cost_pct:.2%} per fill, follow mode. "
          "CAGR = yearly return, DD = biggest drop.")
    header = f"{'':22}{'CAGR':>7}{'DD':>8}{'Sharpe':>8}{'Trades/yr':>11}{'Win%':>6}{'Invested':>10}"
    for symbol in symbols:
        df = fetch_history(symbol, years)
        rows = []
        for label, name, params in COMPARE_SET:
            # Follow mode so every strategy starts matched, instead of waiting for a first flip.
            r = simulate(df, strategies.compute(name, df, params)["position"], years, cost_pct, "follow")
            rows.append((label, r))
        h = rows[0][1]["buy_hold"]
        d = rows[0][1]["df"]
        print(f"\n{symbol}  ({d.index[0]:%Y-%m-%d} to {d.index[-1]:%Y-%m-%d})")
        print(header)
        print(f"{'Buy & hold':22}{h['cagr']:>7.1%}{h['max_dd']:>8.1%}{h['sharpe']:>8.2f}"
              f"{'-':>11}{'-':>6}{1:>10.0%}")
        for label, r in rows:
            s = r["strategy"]
            win = f"{r['wins'] / r['closed']:.0%}" if r["closed"] else "-"
            print(f"{label:22}{s['cagr']:>7.1%}{s['max_dd']:>8.1%}{s['sharpe']:>8.2f}"
                  f"{r['closed'] / (r['days'] / 365.25):>11.1f}{win:>6}{r['exposure']:>10.0%}")
    print("\nPrices are adjusted for splits and dividends. Past results don't predict future ones.")


def parse_params(pairs):
    params = {}
    for pair in pairs or []:
        key, value = pair.split("=", 1)
        params[key] = float(value) if "." in value else int(value)
    return params


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest strategies from strategies.py.")
    p.add_argument("--symbol", default=config.SYMBOL)
    p.add_argument("--strategy", default=config.STRATEGY, choices=list(strategies.STRATEGIES))
    p.add_argument("--param", action="append", metavar="KEY=VALUE",
                   help="Strategy setting, e.g. --param short=50 (repeatable)")
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--cost", type=float, default=0.0005,
                   help="Slippage per fill as a fraction (default 0.0005 = 0.05%%)")
    p.add_argument("--mode", default=config.SIGNAL_MODE, choices=["crossover", "follow"])
    p.add_argument("--compare", action="store_true", help="Compare every strategy")
    p.add_argument("--symbols", nargs="+", default=COMPARE_SYMBOLS, help="Symbols for --compare")
    args = p.parse_args()

    if args.compare:
        run_compare(args.symbols, args.years, args.cost)
    else:
        # Use config.py's params only when backtesting config.py's strategy.
        params = parse_params(args.param) if args.param or args.strategy != config.STRATEGY \
            else config.STRATEGY_PARAMS
        run_single(args.symbol, args.strategy, params, args.years, args.cost, args.mode)


if __name__ == "__main__":
    main()
