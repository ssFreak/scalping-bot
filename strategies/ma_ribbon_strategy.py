import pandas as pd
import traceback

from strategies.base_strategy import BaseStrategy
from core.utils import calculate_atr
from core.indicators import calculate_rsi_wilder, calculate_macd


class MARibbonStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, mt5_connector):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, mt5_connector)
        self.sma_periods = config.get("sma_periods", [5, 8, 13])
        self.atr_period = config.get("atr_period", 14)
        self.tp_atr_multiplier = config.get("tp_atr_multiplier", 1.5)
        self.sl_atr_multiplier = config.get("sl_atr_multiplier", 2.5)
        self.timeframe = self.mt5.get_timeframe(config.get("timeframe", "M5"))
        self.min_atr_points = config.get("min_atr_points", 60)
        self.rsi_period = config.get("rsi_period", 14)
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)
        self.ts_atr_multiplier = config.get("ts_atr_multiplier", 1.0)

    def _calculate_sma(self, df):
        for p in self.sma_periods:
            df[f"SMA_{p}"] = df["close"].rolling(window=p, min_periods=1).mean()
        return df

    def _ribbon_signal(self, df):
        sma_5, sma_8, sma_13 = float(df["SMA_5"].iloc[-1]), float(df["SMA_8"].iloc[-1]), float(df["SMA_13"].iloc[-1])
        point_val = self.mt5.get_symbol_info(self.symbol).point
        if sma_5 > sma_8 > sma_13 and (sma_5 - sma_8 > 0.5 * point_val) and (sma_8 - sma_13 > 0.5 * point_val):
            return "BUY"
        if sma_5 < sma_8 < sma_13 and (sma_8 - sma_5 > 0.5 * point_val) and (sma_13 - sma_8 > 0.5 * point_val):
            return "SELL"
        return None

    def run_once(self, symbol=None):
        sym = symbol or self.symbol
        try:
            count = max(self.sma_periods) + self.atr_period + 10
            rates = self.mt5.get_rates(sym, self.timeframe, count)
            if not rates or len(rates) < count - 5:
                return
            df = pd.DataFrame(rates)

            df = calculate_atr(df, self.atr_period)
            df = calculate_rsi_wilder(df, self.rsi_period)
            df = calculate_macd(df, self.macd_fast, self.macd_slow, self.macd_signal)
            df = self._calculate_sma(df)

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

            signal = self._ribbon_signal(df)
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

            entry = float(df["close"].iloc[-1])
            if signal == "BUY":
                sl = entry - self.sl_atr_multiplier * atr
                tp = entry + self.tp_atr_multiplier * atr
            else:
                sl = entry + self.sl_atr_multiplier * atr
                tp = entry - self.tp_atr_multiplier * atr

            lot = self.risk_manager.get_lot_size(sym, entry, sl)
            if lot <= 0 or not self.risk_manager.check_free_margin(lot, sym):
                return

            ok = self.trade_manager.open_trade(sym, signal, lot, entry, sl, tp)
            if ok:
                ts_distance = self.ts_atr_multiplier * atr
                self.trade_manager.manage_trailing_stop(sym, ts_atr=ts_distance)

        except Exception as e:
            self.logger.log(f"❌ Error in MARibbonStrategy {sym}: {e}")
            self.logger.log(f"🔍 {traceback.format_exc()}")
