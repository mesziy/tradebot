"""Trading settings. Edit these values to change what the bot trades."""

SYMBOL = "AAPL"          # Stock ticker to trade
TRADE_QTY = 1            # Shares per buy/sell order

# Which strategy to run, and its settings. Options (see strategies.py):
#   "ma_crossover"      {"short": 20, "long": 50}
#   "trend_monthly"     {"window": 200}
#   "momentum_monthly"  {"lookback": 252}
#   "rsi2"              {"period": 2, "entry": 10, "exit_ma": 5, "trend": 200}
#   "donchian"          {"entry": 55, "exit": 20}
# Leave a setting out to use its default.
STRATEGY = "ma_crossover"
STRATEGY_PARAMS = {"short": 20, "long": 50}

# How the bot decides to trade:
#   "crossover"  only on the day the signal flips (e.g. the MA lines actually cross).
#   "follow"     every run, buy or sell so the account matches the strategy's current
#                position. Also catches up after missed days (Mac off).
SIGNAL_MODE = "crossover"

# Trading days of history to fetch from yfinance. Must cover the longest
# indicator period (e.g. 200-day average, 252-day momentum) with room to spare.
LOOKBACK_DAYS = 400

# When True, the bot prints the order it would place but doesn't send it.
DRY_RUN = False
