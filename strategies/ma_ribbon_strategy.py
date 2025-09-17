import pandas as pd
import traceback

from strategies.base_strategy import BaseStrategy
from core.utils import calculate_atr


class MARibbonStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, mt5_connector):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, mt5_connector)
        self.sma_periods = list(config.get('sma_periods', [5, 8, 13]))
        self.atr_period = int(config.get('atr_period', 14))
        self.tp_atr_multiplier = float(config.get('tp_atr_multiplier', 1.5))
        self.sl_atr_multiplier = float(config.get('sl_atr_multiplier', 2.5))
        tf_str = config.get('timeframe', 'M5')
        self.timeframe = self.mt5.get_timeframe(tf_str)

    def _calculate_sma(self, df: pd.DataFrame):
        for period in self.sma_periods:
            df[f'SMA_{period}'] = df['close'].rolling(window=period, min_periods=1).mean()
        return df

    def generate_signal(self, df: pd.DataFrame):
        df = self._calculate_sma(df)

        s5 = float(df['SMA_5'].iloc[-1])
        s8 = float(df['SMA_8'].iloc[-1])
        s13 = float(df['SMA_13'].iloc[-1])

        info = self.mt5.get_symbol_info(self.symbol)
        point_val = getattr(info, 'point', 0.00001) if info else 0.00001

        # BUY dacă ribbon-ul este aliniat și separația e minimă
        if s5 > s8 > s13 and (s5 - s8) > 0.5 * point_val and (s8 - s13) > 0.5 * point_val:
            return "BUY"

        # SELL dacă ribbon-ul este aliniat invers și separația e minimă
        if s5 < s8 < s13 and (s8 - s5) > 0.5 * point_val and (s13 - s8) > 0.5 * point_val:
            return "SELL"

        return None

    def run_once(self, symbol=None):
        sym = symbol or self.symbol
        try:
            if not self.risk_manager.can_trade(verbose=False):
                return

            count = max(self.sma_periods) + self.atr_period + 5

            # === rates ===
            rates = self.mt5.get_rates(sym, self.timeframe, count)
            if rates is None:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): rates is None")
                return
            if hasattr(rates, "__len__") and len(rates) == 0:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): rates.length == 0")
                return

            df = pd.DataFrame(rates)
            if df.empty:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): DataFrame is empty")
                return

            # === ATR în PREȚ ===
            df = calculate_atr(df, self.atr_period)
            atr_price = float(df['atr'].iloc[-1])

            pip = self.mt5.get_pip_size(sym)
            if pip <= 0.0:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): pip size invalid")
                return

            atr_pips = atr_price / pip
            if atr_pips <= 0.0:
                self.logger.log(f"⚠️ {self.__class__.__name__}({sym}): ATR pips invalid ({atr_pips:.2f})")
                return

            # === Semnal ===
            signal = self.generate_signal(df)
            if signal is None:
                return

            entry = float(df['close'].iloc[-1])
            if signal == "BUY":
                sl = entry - self.sl_atr_multiplier * atr_price
                tp = entry + self.tp_atr_multiplier * atr_price
            else:
                sl = entry + self.sl_atr_multiplier * atr_price
                tp = entry - self.tp_atr_multiplier * atr_price

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
