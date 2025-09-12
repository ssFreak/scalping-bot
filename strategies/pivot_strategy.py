import pandas as pd
import traceback

from strategies.base_strategy import BaseStrategy
from core.utils import calculate_atr, calculate_daily_pivots_from_rates
from core.indicators import calculate_rsi_wilder, calculate_macd


class PivotStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, mt5_connector):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, mt5_connector)
        self.timeframe = self.mt5.get_timeframe(config.get("timeframe", "M1"))
        self.atr_period = config.get("atr_period", 14)
        self.atr_multiplier = config.get("atr_multiplier", 2.5)
        self.min_atr_points = config.get("min_atr_points", 25)
        self.rsi_period = config.get("rsi_period", 14)
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)
        self.ts_atr_multiplier = config.get("ts_atr_multiplier", 1.0)

    def run_once(self, symbol=None):
        sym = symbol or self.symbol
        try:
            # intraday data
            rates = self.mt5.get_rates(sym, self.timeframe, 200)
            if not rates or len(rates) < 50:
                return
            df = pd.DataFrame(rates)

            # daily pivots from previous D1 bar
            d1 = self.mt5.get_rates(sym, self.mt5.get_timeframe("D1"), 20)
            if not d1 or len(d1) < 2:
                return
            d1df = pd.DataFrame(d1)
            pivots = calculate_daily_pivots_from_rates(d1df)
            if not pivots:
                return

            # indicators
            df = calculate_atr(df, self.atr_period)
            df = calculate_rsi_wilder(df, self.rsi_period)
            df = calculate_macd(df, self.macd_fast, self.macd_slow, self.macd_signal)

            # filters
            symbol_info = self.mt5.get_symbol_info(sym)
            if not symbol_info:
                return
            point = symbol_info.point
            atr = float(df["atr"].iloc[-1])
            atr_points = atr / point
            if atr_points < self.min_atr_points:
                return

            rsi = float(df["rsi"].iloc[-1])
            macd = float(df["macd"].iloc[-1])
            macd_sig = float(df["macd_signal"].iloc[-1])

            price = float(df["close"].iloc[-1])
            s1, r1, pp = pivots["S1"], pivots["R1"], pivots["PP"]

            signal, tp = None, None
            if price <= s1:
                signal, tp = "BUY", pp
            elif price >= r1:
                signal, tp = "SELL", pp
            if not signal:
                return

            # RSI filter
            if 40 <= rsi <= 60:
                return
            if signal == "BUY" and rsi <= 60:
                return
            if signal == "SELL" and rsi >= 40:
                return

            # MACD filter
            if signal == "BUY" and not (macd > macd_sig):
                return
            if signal == "SELL" and not (macd < macd_sig):
                return

            # risk & order
            entry = price
            if signal == "BUY":
                sl = entry - self.atr_multiplier * atr
            else:
                sl = entry + self.atr_multiplier * atr

            lot = self.risk_manager.get_lot_size(sym, entry, sl)
            if lot <= 0 or not self.risk_manager.check_free_margin(lot, sym):
                return

            ok = self.trade_manager.open_trade(sym, signal, lot, entry, sl, tp)
            if ok:
                ts_distance = self.ts_atr_multiplier * atr  # price units
                self.trade_manager.manage_trailing_stop(sym, ts_atr=ts_distance)

        except Exception as e:
            self.logger.log(f"❌ Error in PivotStrategy {sym}: {e}")
            self.logger.log(f"🔍 {traceback.format_exc()}")
