"""Offline tests for the signal logic, strategies and backtest simulators."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest  # noqa: E402
import bot  # noqa: E402
import intraday_backtest  # noqa: E402
import intraday_strategies  # noqa: E402
import strategies  # noqa: E402


def daily(closes, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 0.5, "Low": c - 0.5, "Close": c})


# --- bot.detect_signal --------------------------------------------------------

def test_buy_on_real_crossover():
    prices = daily([100 - i * 0.5 for i in range(59)] + [400])
    signal, _ = bot.detect_signal(prices, "ma_crossover", {"short": 20, "long": 50})
    assert signal == "buy"


def test_sell_on_real_crossover():
    prices = daily([100 + i * 0.5 for i in range(59)] + [-200])
    signal, _ = bot.detect_signal(prices, "ma_crossover", {"short": 20, "long": 50})
    assert signal == "sell"


def test_no_signal_while_trend_continues():
    prices = daily([100 + i * 0.5 for i in range(60)])
    signal, result = bot.detect_signal(prices, "ma_crossover", {"short": 20, "long": 50})
    assert signal is None
    assert result["position"].iloc[-1] == 1


def test_not_enough_history_raises():
    with pytest.raises(RuntimeError, match="LOOKBACK_DAYS"):
        bot.detect_signal(daily(range(30)), "ma_crossover", {"short": 20, "long": 50})


# --- strategies -----------------------------------------------------------------

@pytest.mark.parametrize("name", list(strategies.STRATEGIES))
def test_positions_are_zero_one_or_warmup(name):
    rng = np.random.default_rng(0)
    prices = daily(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600))))
    pos = strategies.compute(name, prices)["position"]
    assert set(pos.dropna().unique()) <= {0.0, 1.0}
    assert pos.notna().iloc[-1]


def test_monthly_strategies_only_change_on_first_day_of_month():
    rng = np.random.default_rng(1)
    prices = daily(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 800))))
    pos = strategies.compute("trend_monthly", prices)["position"]
    changed = pos.diff().fillna(0) != 0
    month_start = pd.Series(pos.index.month, index=pos.index).diff().fillna(0) != 0
    assert (changed <= month_start).all()


def test_rsi2_buys_dip_in_uptrend_and_sells_bounce():
    closes = [100 + i * 0.2 for i in range(250)] + [140, 135, 131, 150]
    pos = strategies.compute("rsi2", daily(closes))["position"]
    assert pos.iloc[-2] == 1   # bought after the sharp dip
    assert pos.iloc[-1] == 0   # sold when price closed above the 5-day average


def test_unknown_strategy():
    with pytest.raises(ValueError):
        strategies.compute("nope", daily(range(10)))


# --- backtest.simulate ------------------------------------------------------------

def test_orders_fill_at_next_open():
    prices = daily([10, 10, 10, 10, 10])
    prices["Open"] = [10, 11, 12, 13, 14]
    position = pd.Series([0, 1, 1, 0, 0], index=prices.index, dtype=float)
    r = backtest.simulate(prices, position, years=10, cost_pct=0.0)
    trade = r["trades"][0]
    assert trade["entry"] == 12 and trade["exit"] == 14


def test_follow_mode_enters_on_day_one():
    # Strategy already says "hold" before the 1-year test window starts.
    prices = daily(np.linspace(10, 20, 400))
    position = pd.Series(1.0, index=prices.index)
    assert backtest.simulate(prices, position, 1, 0.0, "crossover")["trades"] == []
    assert len(backtest.simulate(prices, position, 1, 0.0, "follow")["trades"]) == 1


def test_idle_cash_earns_interest():
    prices = daily(np.full(400, 10.0))
    position = pd.Series(0.0, index=prices.index)  # always in cash
    rates = backtest.cash_rates(prices.index, "0.05")
    r = backtest.simulate(prices, position, 1, 0.0, cash_rate=rates)
    years = (r["equity"].index[-1] - r["equity"].index[0]).days / 365
    assert r["equity"].iloc[-1] == pytest.approx(backtest.START_CAPITAL * 1.05 ** years)
    assert backtest.simulate(prices, position, 1, 0.0)["equity"].iloc[-1] == backtest.START_CAPITAL


# --- intraday ----------------------------------------------------------------------

def intraday_bars():
    days = ["2026-01-05", "2026-01-06"]
    idx = pd.DatetimeIndex([]).tz_localize("America/New_York")
    for d in days:
        idx = idx.append(pd.date_range(f"{d} 09:30", f"{d} 15:55", freq="5min", tz="America/New_York"))
    c = np.r_[np.linspace(100, 101, 78), np.linspace(99, 102, 78)]
    bars = pd.DataFrame({"open": c, "high": c + 0.05, "low": c - 0.05, "close": c,
                         "volume": 1000, "vwap": c}, index=idx)
    return intraday_strategies.prepare(bars)


def test_intraday_positions_close_every_day():
    df = intraday_bars()
    for fn in intraday_strategies.STRATEGIES.values():
        eq, trips = intraday_backtest.simulate(df, fn(df), 0.0)
        assert len(eq) == 2 and trips <= 2


def test_gap_fade_trades_only_gap_down_day():
    df = intraday_bars()
    pos = intraday_strategies.gap_fade(df)
    assert pos[df["day"] == df["day"].iloc[0]].sum() == 0
    assert pos[df["day"] == df["day"].iloc[-1]].sum() > 0


def test_costs_reduce_returns():
    df = intraday_bars()
    pos = intraday_strategies.vwap_trend(df)
    free, _ = intraday_backtest.simulate(df, pos, 0.0)
    costly, _ = intraday_backtest.simulate(df, pos, 0.001)
    assert costly.iloc[-1] < free.iloc[-1]
