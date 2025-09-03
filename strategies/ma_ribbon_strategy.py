# strategies/ma_ribbon_strategy.py
import pandas as pd
import MetaTrader5 as mt5
import traceback
import time

from strategies.base_strategy import BaseStrategy
from core.utils import calculate_atr

class MARibbonStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, bot_manager):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, bot_manager)

        # Parametrii din YAML
        self.sma_periods = config.get('sma_periods', [5, 8, 13])
        self.atr_period = config.get('atr_period', 14)
        self.tp_atr_multiplier = config.get('tp_atr_multiplier', 1.5)
        self.sl_atr_multiplier = config.get('sl_atr_multiplier', 2.5)
        self.timeframe = getattr(mt5, f"TIMEFRAME_{config.get('timeframe', 'M5')}")

    def _calculate_sma(self, df):
        for period in self.sma_periods:
            df[f'SMA_{period}'] = df['close'].rolling(window=period, min_periods=1).mean()
        return df

    def generate_signal(self, df):
        """Semnalează BUY / SELL / None în funcție de alinierea SMA-urilor."""
        df = self._calculate_sma(df)

        sma_5 = float(df['SMA_5'].iloc[-1])
        sma_8 = float(df['SMA_8'].iloc[-1])
        sma_13 = float(df['SMA_13'].iloc[-1])

        symbol_info = mt5.symbol_info(self.symbol)
        point_val = getattr(symbol_info, 'point', 0.00001)

        # Semnal BUY
        if sma_5 > sma_8 and sma_8 > sma_13:
            if (sma_5 - sma_8 > 0.5 * point_val) and (sma_8 - sma_13 > 0.5 * point_val):
                return "BUY"

        # Semnal SELL
        elif sma_5 < sma_8 and sma_8 < sma_13:
            if (sma_8 - sma_5 > 0.5 * point_val) and (sma_13 - sma_8 > 0.5 * point_val):
                return "SELL"

        return None

    def run_once(self):
        """Execută o singură iterație de strategie."""
        try:
            rates = mt5.copy_rates_from_pos(
                self.symbol, self.timeframe,
                0, max(self.sma_periods) + self.atr_period + 5
            )
            if rates is None or len(rates) == 0:
                return  # nu sunt date, skip

            df = pd.DataFrame(rates)
            df = calculate_atr(df, self.atr_period)
            atr = float(df['atr'].iloc[-1])

            if atr == 0.0 or df.isnull().any().any():
                return  # ATR invalid

            signal = self.generate_signal(df)
            if signal:
                entry_price = float(df['close'].iloc[-1])

                if signal == "BUY":
                    sl = entry_price - self.sl_atr_multiplier * atr
                    tp = entry_price + self.tp_atr_multiplier * atr
                else:  # SELL
                    sl = entry_price + self.sl_atr_multiplier * atr
                    tp = entry_price - self.tp_atr_multiplier * atr

                lot = self.risk_manager.calculate_lot_size(self.symbol, signal, entry_price, sl)
                if lot > 0 and self.risk_manager.check_free_margin():
                    self.trade_manager.open_trade(self.symbol, signal, lot, entry_price, sl, tp)

            # trailing stop pentru simbol
            self.trade_manager.manage_trailing_stop(self.symbol)

        except Exception as e:
            trace = traceback.format_exc()
            self.logger.log(f"❌ Error in MARibbonStrategy {self.symbol}: {e}\n{trace}")
            time.sleep(2)
