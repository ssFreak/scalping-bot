# strategies/pivot_strategy.py
import pandas as pd
import MetaTrader5 as mt5
import traceback
import time

from strategies.base_strategy import BaseStrategy
from core.utils import calculate_pivots, calculate_atr

class PivotStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, bot_manager):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, bot_manager)

    def generate_signal(self, df):
        pivots = calculate_pivots(df)

        s1 = pivots['S1'].iloc[-1] if isinstance(pivots['S1'], pd.Series) else pivots['S1']
        r1 = pivots['R1'].iloc[-1] if isinstance(pivots['R1'], pd.Series) else pivots['R1']
        pp = pivots['PP'].iloc[-1] if isinstance(pivots['PP'], pd.Series) else pivots['PP']

        price = df.iloc[-1]['close']

        if price <= s1:
            return "BUY", pp
        elif price >= r1:
            return "SELL", pp
        return None, None

    def run(self, symbol=None):
        """Unified interface method that calls run_once()."""
        # Use the provided symbol parameter or fall back to self.symbol for backward compatibility
        if symbol is not None:
            # Temporarily use the provided symbol for this run
            original_symbol = self.symbol
            self.symbol = symbol
            try:
                self.run_once()
            finally:
                # Restore original symbol
                self.symbol = original_symbol
        else:
            # Use default behavior with self.symbol
            self.run_once()

    def run_once(self):
        """Execută o singură iterație de strategie."""
        try:
            rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 0, 100)
            if rates is None or len(rates) < 15:
                return  # nu sunt destule date

            df = pd.DataFrame(rates)

            # Generează semnal
            signal, tp = self.generate_signal(df)
            if signal:
                entry_price = float(df.iloc[-1]['close'])
                atr = float(calculate_atr(df, 14).iloc[-1]['atr'])
                if atr <= 0:
                    return

                sl = entry_price - 2.5 * atr if signal == "BUY" else entry_price + 2.5 * atr

                lot = self.risk_manager.calculate_lot_size(self.symbol, signal, entry_price, sl)
                if lot > 0 and self.risk_manager.check_free_margin():
                    self.trade_manager.open_trade(self.symbol, signal, lot, entry_price, sl, tp)

            # trailing stop pentru simbol
            self.trade_manager.manage_trailing_stop(self.symbol)

        except Exception as e:
            trace = traceback.format_exc()
            self.logger.log(f"❌ Error in PivotStrategy {self.symbol}: {e}\n{trace}")
            time.sleep(2)
