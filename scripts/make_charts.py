"""Generate the README charts in docs/.

Usage:
    python scripts/make_charts.py

Daily data comes from yfinance; the day-trading chart reuses the 5-minute bars
cached in data/ by intraday_backtest.py (downloaded from Alpaca if missing).
"""

import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backtest  # noqa: E402
import intraday_backtest  # noqa: E402
import intraday_strategies  # noqa: E402
import strategies  # noqa: E402

DOCS = ROOT / "docs"

# Reference palette: categorical slots in fixed order, plus chart chrome.
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
NEUTRAL = "#a3a29c"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "text.color": INK,
    "xtick.color": INK_2, "ytick.color": INK_2, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 11, "axes.titlesize": 14, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "legend.frameon": False,
})


def signals_chart() -> None:
    """AAPL price with the 20/50-day averages and the crossover trades."""
    df = backtest.fetch_history("AAPL", 2)
    ma = strategies.compute("ma_crossover", df, {"short": 20, "long": 50})
    df, ma = df.loc[df.index[-504]:], ma.loc[df.index[-504]:]  # last ~2 trading years
    change = ma["position"].diff()

    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.plot(df.index, df["Close"], color=NEUTRAL, linewidth=1.2, label="AAPL close")
    ax.plot(ma.index, ma["20d MA"], color=SERIES[0], linewidth=2, label="20-day average")
    ax.plot(ma.index, ma["50d MA"], color=SERIES[1], linewidth=2, label="50-day average")
    buys, sells = change[change > 0].index, change[change < 0].index
    ax.scatter(buys, ma.loc[buys, "20d MA"], marker="^", s=110, color=SERIES[2],
               edgecolor=INK, linewidth=0.8, zorder=5, label="Buy (crosses above)")
    ax.scatter(sells, ma.loc[sells, "20d MA"], marker="v", s=90, color=SERIES[3],
               edgecolor=INK, linewidth=0.8, zorder=5, label="Sell (crosses below)")
    ax.set_title("Moving average crossover signals on AAPL, last 2 years")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(loc="upper left", ncols=3, fontsize=10)
    fig.tight_layout()
    fig.savefig(DOCS / "signals.png", dpi=150)
    plt.close(fig)


def equity_chart() -> None:
    """Growth of $10,000: 20/50 crossover vs buy and hold on AAPL, 10 years."""
    df = backtest.fetch_history("AAPL", 10)
    r = backtest.simulate(df, strategies.compute("ma_crossover", df, {"short": 20, "long": 50})["position"],
                          10, 0.0005, "crossover")

    fig, ax = plt.subplots(figsize=(11, 5.2))
    for series, color, label in [(r["hold"], SERIES[0], "Buy & hold"),
                                 (r["equity"], SERIES[1], "20/50 crossover bot")]:
        ax.plot(series.index, series, color=color, linewidth=2,
                label=f"{label}: ${series.iloc[-1]:,.0f}")
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Growth of $10,000 on AAPL: crossover bot vs buy & hold (log scale)")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(DOCS / "equity.png", dpi=150)
    plt.close(fig)


def daytrading_chart() -> None:
    """What 20,000 HUF became with each day-trading strategy on QQQ since 2019."""
    rate = intraday_backtest.usd_huf()
    budget = 20_000
    df = intraday_strategies.prepare(intraday_backtest.load_bars("QQQ", datetime(2019, 1, 1)))
    hold = df.groupby("day")["close"].last() / df["open"].iloc[0]
    rows = [("Buy & hold (no day trading)", hold.iloc[-1] * budget)]
    for name, fn in intraday_strategies.STRATEGIES.items():
        eq, _ = intraday_backtest.simulate(df, fn(df), 0.0003)
        rows.append((intraday_backtest.LABELS[name], eq.iloc[-1] * budget))
    rows.sort(key=lambda r: r[1])

    fig, ax = plt.subplots(figsize=(11, 4.8))
    labels, values = zip(*rows)
    colors = [SERIES[0] if l.startswith("Buy") else NEUTRAL for l in labels]
    ax.barh(labels, values, color=colors, height=0.6)
    ax.axvline(budget, color=INK, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
    ax.text(budget, 0, " start: 20,000 HUF", va="center", fontsize=10, color=INK_2)
    for i, v in enumerate(values):
        ax.annotate(f"{v:,.0f} HUF", (v, i), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=10, color=INK, zorder=3,
                    bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1})
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, max(values) * 1.15)
    ax.set_title(f"20,000 HUF (${budget / rate:,.0f}) day-trading QQQ since 2019, after costs")
    fig.tight_layout()
    fig.savefig(DOCS / "daytrading.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    signals_chart()
    equity_chart()
    daytrading_chart()
    print(f"Charts written to {DOCS}")
