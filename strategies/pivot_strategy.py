import pandas as pd
import traceback

from strategies.base_strategy import BaseStrategy
from core.utils import calculate_atr


class PivotStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, mt5_connector):
        # mt5_connector este folosit ca self.mt5 în BaseStrategy
        super().__init__(symbol, config, logger, risk_manager, trade_manager, mt5_connector)
        self.atr_period = int(config.get("atr_period", 14))
        self.atr_multiplier = float(config.get("atr_multiplier", 2.5))
        tf_str = config.get("timeframe", "M1")
        self.timeframe = self.mt5.get_timeframe(tf_str)

    def generate_signal(self, df: pd.DataFrame):
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])
        close = float(df["close"].iloc[-1])

        pivot = (high + low + close) / 3.0
        r1 = 2 * pivot - low
        s1 = 2 * pivot - high

        if close <= s1:
            return "BUY", pivot
        elif close >= r1:
            return "SELL", pivot
        return None, None

    def run_once(self, symbol=None):
        sym = symbol or self.symbol
        try:
            # Respectăm blocajele de risk
            if not self.risk_manager.can_trade(verbose=False):
                return

            # === rates ===
            rates = self.mt5.get_rates(sym, self.timeframe, 200)
            if rates is None:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): rates is None")
                return
            # numpy array/list explicit
            if hasattr(rates, "__len__") and len(rates) < 50:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): rates too few: {len(rates)}")
                return

            df = pd.DataFrame(rates)
            if df.empty:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): DataFrame is empty")
                return

            # === ATR în PREȚ (nu points) ===
            df = calculate_atr(df, self.atr_period)
            atr_price = float(df["atr"].iloc[-1])

            pip = self.mt5.get_pip_size(sym)
            if pip <= 0.0:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): pip size invalid")
                return

            atr_pips = atr_price / pip
            if atr_pips <= 0.0:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): ATR pips invalid ({atr_pips:.2f})")
                return

            # === Semnal ===
            signal, tp_level = self.generate_signal(df)
            if signal is None:
                return

            entry = float(df["close"].iloc[-1])

            # SL din multipli ATR (ATR este în preț, multiplicatorul rămâne dimensionless)
            if signal == "BUY":
                sl = entry - self.atr_multiplier * atr_price
            else:
                sl = entry + self.atr_multiplier * atr_price

            # TP = nivel pivot returnat din generate_signal
            tp = float(tp_level)

            # === Risk & Open ===
            lot = self.risk_manager.calculate_lot_size(sym, signal, entry, sl)
            if lot <= 0.0:
                return
            if not self.risk_manager.check_free_margin():
                return

            ok = self.trade_manager.open_trade(sym, signal, lot, entry, sl, tp)
            if ok:
                self.trade_manager.manage_trailing_stop(sym)

        except Exception as e:
            trace = traceback.format_exc()
            self.logger.log(f"❌ Error in {self.__class__.__name__} {sym}: {e}")
            self.logger.log(f"🔍 {trace}")
