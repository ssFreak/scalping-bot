# strategies/asian_breakout.py - LONDON OPEN BREAKOUT STRATEGY

import pandas as pd
from strategies.base_strategy import BaseStrategy
from datetime import datetime, time

class AsianBreakoutStrategy(BaseStrategy):
    
    def __init__(self, symbol, config, broker_context):
        super().__init__(symbol, config, broker_context)
        
        # Parametri Timp (Server Time)
        self.range_start_hour = int(self.config.get("range_start_hour", 1)) # 01:00
        self.range_end_hour = int(self.config.get("range_end_hour", 8))     # 08:00 (London Open)
        
        # Parametri Execuție
        self.breakout_buffer_pips = float(self.config.get("breakout_buffer", 2.0))
        self.sl_pips = float(self.config.get("sl_pips", 20.0))
        self.tp_pips = float(self.config.get("tp_pips", 60.0))
        
        # Management Zilnic
        self.current_day = None
        self.daily_high = -1.0
        self.daily_low = 999999.0
        self.trade_taken_today = False
        
        # Timeframe M5 sau M15 e ideal
        self.timeframe_str = self.config.get("timeframe", "M15")

    def run_once(self, current_bar=None):
        try:
            is_backtest_run = current_bar is not None
            
            # 1. Determinăm timpul curent (Bar Time)
            if is_backtest_run:
                # current_bar.name este datetime-ul indexului în pandas
                curr_time = current_bar.name
                high = current_bar['high']
                low = current_bar['low']
                close = current_bar['close']
            else:
                # Live - ultima bară închisă
                df = self.broker.get_historical_data(self.symbol, self.timeframe_str, count=2)
                if df is None: return
                curr_time = df.index[-1]
                high = df['high'].iloc[-1]
                low = df['low'].iloc[-1]
                close = df['close'].iloc[-1]
            
            # Resetare la zi nouă
            day_of_year = curr_time.dayofyear
            if self.current_day != day_of_year:
                self.current_day = day_of_year
                self.daily_high = -1.0
                self.daily_low = 999999.0
                self.trade_taken_today = False
            
            hour = curr_time.hour
            
            # 2. Faza 1: Construcția Range-ului Asiatic (Ex: 01:00 - 08:00)
            if self.range_start_hour <= hour < self.range_end_hour:
                if high > self.daily_high: self.daily_high = high
                if low < self.daily_low: self.daily_low = low
                return # Doar monitorizăm

            # 3. Faza 2: Breakout (Ex: 08:00 - 12:00)
            # Intrăm doar în primele ore după deschidere (să zicem max 4 ore după range)
            max_entry_hour = self.range_end_hour + 4
            
            if self.range_end_hour <= hour < max_entry_hour:
                
                if self.trade_taken_today: return # O singură lovitură pe zi
                if self.daily_high == -1.0: return # Nu avem range valid
                
                # Verificăm poziții existente
                if self.broker.get_open_positions(self.symbol, self.magic_number):
                    self.trade_taken_today = True
                    return

                pip = self.broker.get_pip_size(self.symbol)
                buffer_price = self.breakout_buffer_pips * pip
                
                # Calcul Lot
                sl_dist_price = self.sl_pips * pip
                lot = 0.01
                if not is_backtest_run:
                    lot = self.broker.risk_manager.calculate_lot_size(self.symbol, "BUY", close, close - sl_dist_price)
                else:
                    lot = self.broker.calculate_lot_size(self.symbol, close - sl_dist_price)

                if lot <= 0: return

                # SEMNAL BUY: Prețul sparge High-ul Asiatic
                if close > (self.daily_high + buffer_price):
                    sl = close - sl_dist_price
                    tp = close + (self.tp_pips * pip)
                    self.broker.open_market_order(self.symbol, 0, lot, sl, tp, self.magic_number, "Asian_Break_Buy")
                    self.trade_taken_today = True
                    
                # SEMNAL SELL: Prețul sparge Low-ul Asiatic
                elif close < (self.daily_low - buffer_price):
                    sl = close + sl_dist_price
                    tp = close - (self.tp_pips * pip)
                    self.broker.open_market_order(self.symbol, 1, lot, sl, tp, self.magic_number, "Asian_Break_Sell")
                    self.trade_taken_today = True

        except Exception as e:
            self.logger.log(f"Err Asian {self.symbol}: {e}", "error")