# strategies/momentum_strategy.py
import pandas as pd
import MetaTrader5 as mt5
import time
import traceback

from strategies.base_strategy import BaseStrategy
from core.utils import calculate_atr  # putem păstra pentru SL/TP din ATR

class MomentumStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, bot_manager):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, bot_manager)

        # Parametrii din YAML
        self.rsi_period = config.get("rsi_period", 14)
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)
        self.atr_period = config.get("atr_period", 14)
        self.tp_atr_multiplier = config.get("tp_atr_multiplier", 1.5)
        self.sl_atr_multiplier = config.get("sl_atr_multiplier", 2.0)
        self.timeframe = getattr(mt5, f"TIMEFRAME_{config.get('timeframe', 'M1')}")

    def _calculate_rsi(self, df):
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).ewm(span=self.rsi_period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(span=self.rsi_period, adjust=False).mean()
        rs = gain / loss.replace(0, 1)  # evităm divizare la zero
        df["rsi"] = 100 - (100 / (1 + rs))
        return df

    def _calculate_macd(self, df):
        df["ema_fast"] = df["close"].ewm(span=self.macd_fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.macd_slow, adjust=False).mean()
        df["macd"] = df["ema_fast"] - df["ema_slow"]
        df["macd_signal"] = df["macd"].ewm(span=self.macd_signal, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]
        return df

    def generate_signal(self, df):
        """Returnează BUY/SELL/None pe baza RSI și MACD crossover."""
        df = self._calculate_rsi(df)
        df = self._calculate_macd(df)

        if df.isnull().any().any():
            return None

        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]

        # Semnal de cumpărare
        if last_row["rsi"] > 40 and last_row["macd_hist"] > 0 and prev_row["macd_hist"] <= 0:
            return "BUY"

        # Semnal de vânzare
        elif last_row["rsi"] < 60 and last_row["macd_hist"] < 0 and prev_row["macd_hist"] >= 0:
            return "SELL"

        return None

    def run(self, symbol=None):
        # Use the provided symbol parameter or fall back to self.symbol for backward compatibility
        active_symbol = symbol if symbol is not None else self.symbol
        self.logger.log(f"▶️ Starting MomentumStrategy for {active_symbol}")
        while True and self.risk_manager.can_trade():
            try:
                rates = mt5.copy_rates_from_pos(
                    active_symbol, self.timeframe, 0, max(self.macd_slow, self.rsi_period, self.atr_period) + 5
                )
                if rates is None or len(rates) < max(self.macd_slow, self.rsi_period):
                    time.sleep(5)
                    continue

                df = pd.DataFrame(rates)
                df = calculate_atr(df, self.atr_period)
                atr = float(df["atr"].iloc[-1])

                if atr == 0.0 or df.isnull().any().any():
                    time.sleep(5)
                    continue

                signal = self.generate_signal(df)
                if signal and self.risk_manager.check_max_daily_loss():
                    entry_price = float(df["close"].iloc[-1])

                    if signal == "BUY":
                        sl = entry_price - self.sl_atr_multiplier * atr
                        tp = entry_price + self.tp_atr_multiplier * atr
                    else:  # SELL
                        sl = entry_price + self.sl_atr_multiplier * atr
                        tp = entry_price - self.tp_atr_multiplier * atr

                    lot = self.risk_manager.calculate_lot_size(active_symbol, signal, entry_price, sl)
                    if lot > 0 and self.risk_manager.check_free_margin():
                        self.trade_manager.open_trade(active_symbol, signal, lot, entry_price, sl, tp)

                self.trade_manager.manage_trailing_stop(active_symbol)
                time.sleep(10)

            except Exception as e:
                trace = traceback.format_exc()
                self.logger.log(f"❌ Error in MomentumStrategy {active_symbol}: {e} - {trace}")
                time.sleep(30)
