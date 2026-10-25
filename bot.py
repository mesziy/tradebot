"""Strategy bot for an Alpaca paper trading account.

Run once per day (e.g. after market close): it fetches recent prices, runs the
strategy chosen in config.py, and places a market order either when the
strategy's position flips between the last two trading days ("crossover" mode)
or whenever the account doesn't match the strategy's position ("follow" mode).
"""

import json
import os
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from dotenv import load_dotenv

import config
import strategies

# yfinance triggers pandas deprecation warnings internally; they don't affect results.
warnings.filterwarnings("ignore", module="yfinance")

STATE_FILE = Path(__file__).with_name("last_trade.json")


def fetch_prices(symbol: str, days: int) -> pd.DataFrame:
    """Return the last `days` trading days of daily prices."""
    # Request extra calendar days so weekends/holidays still leave `days` trading days.
    start = datetime.now() - timedelta(days=int(days * 1.6) + 10)
    history = yf.Ticker(symbol).history(start=start.strftime("%Y-%m-%d"), interval="1d")
    if history.empty:
        raise RuntimeError(f"No price data returned for {symbol}")
    return history[["Open", "High", "Low", "Close"]].tail(days)


def detect_signal(prices: pd.DataFrame, name: str, params: dict):
    """Return ("buy" | "sell" | None, indicators) based on the last two days."""
    result = strategies.compute(name, prices, params)
    prev, curr = result["position"].iloc[-2], result["position"].iloc[-1]
    if pd.isna(prev) or pd.isna(curr):
        raise RuntimeError(f"Not enough history for {name}: increase LOOKBACK_DAYS in config.py")

    if curr > prev:
        return "buy", result
    if curr < prev:
        return "sell", result
    return None, result


def get_client() -> TradingClient:
    load_dotenv()
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret or "your_" in key or "your_" in secret:
        sys.exit("Missing Alpaca credentials: set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env")
    return TradingClient(key, secret, paper=True)


def held_qty(client: TradingClient, symbol: str) -> float:
    try:
        return float(client.get_open_position(symbol).qty)
    except APIError:
        return 0.0  # Alpaca returns 404 when there is no position


def already_traded(signal_date: str) -> bool:
    """Prevent placing the same order twice if the bot runs more than once a day."""
    if not STATE_FILE.exists():
        return False
    return json.loads(STATE_FILE.read_text()).get("signal_date") == signal_date


def record_trade(signal_date: str, side: str, order_id: str) -> None:
    STATE_FILE.write_text(json.dumps({"signal_date": signal_date, "side": side, "order_id": order_id}))


def main() -> None:
    symbol = config.SYMBOL
    prices = fetch_prices(symbol, config.LOOKBACK_DAYS)
    signal, result = detect_signal(prices, config.STRATEGY, config.STRATEGY_PARAMS)
    date = prices.index[-1].strftime("%Y-%m-%d")

    print(f"Run at {datetime.now():%Y-%m-%d %H:%M}  |  strategy: {config.STRATEGY} {config.STRATEGY_PARAMS}")
    print(f"{symbol} on {date}: close ${prices['Close'].iloc[-1]:.2f}")
    for col in result.columns.drop("position"):
        print(f"  {col}: {result[col].iloc[-2]:.2f} -> {result[col].iloc[-1]:.2f}")
    target = result["position"].iloc[-1]
    print(f"  position: {'hold' if target else 'cash'}  |  mode: {config.SIGNAL_MODE}")

    client = None
    qty = config.TRADE_QTY
    if config.SIGNAL_MODE == "follow":
        # Match the account to the strategy's current position.
        client = get_client()
        held = held_qty(client, symbol)
        if target == 1 and held < config.TRADE_QTY:
            signal, qty = "buy", config.TRADE_QTY - held
        elif target == 0 and held > 0:
            signal, qty = "sell", min(held, config.TRADE_QTY)
        else:
            signal = None

    if signal is None:
        print("No signal today. No order placed.\n")
        return

    print(f"Signal detected: {signal.upper()}.")

    if already_traded(date):
        print(f"Already placed an order for the {date} signal. Skipping.\n")
        return

    if config.DRY_RUN:
        print(f"[DRY RUN] Would {signal} {qty:g} share(s) of {symbol}.\n")
        return

    client = client or get_client()

    if signal == "sell" and held_qty(client, symbol) < qty:
        print(f"Sell signal, but you don't hold {qty:g} share(s) of {symbol}. Skipping.\n")
        return

    order = client.submit_order(
        MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if signal == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
    )
    record_trade(date, signal, str(order.id))
    print(f"Submitted {signal} order for {qty:g} {symbol}: id={order.id}, status={order.status}\n")


if __name__ == "__main__":
    main()
