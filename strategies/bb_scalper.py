# strategies/bb_scalper.py - DYNAMIC LOT SIZE FIXED

import pandas as pd
import numpy as np
import traceback
from strategies.base_strategy import BaseStrategy

class BollingerReversionScalper(BaseStrategy):
    
    def __init__(self, symbol, config, broker_context):
        super().__init__(symbol, config, broker_context)
        
        # Parametri
        self.bb_period = int(self.config.get("bb_period", 20))
        self.bb_dev = float(self.config.get("bb_dev", 2.0))
        # ADX Settings
        self.adx_period = int(self.config.get("adx_period", 14))
        self.adx_max = float(self.config.get("adx_max", 30.0))
        
        self.timeframe_str = self.config.get("timeframe", "M5")
        self.is_backtest = not hasattr(self.broker, 'mt5')
        
        if not self.is_backtest:
            self.timeframe_live = self.broker.mt5.get_timeframe(self.timeframe_str)

    def run_once(self, current_bar=None):
        try:
            is_backtest_run = current_bar is not None
            
            # Variabile pentru semnal
            price = 0.0
            upper = 0.0
            lower = 0.0
            sma = 0.0
            adx = 0.0
            
            # --- 1. PRELUARE DATE ---
            if not is_backtest_run:
                # [LIVE]
                df = self.broker.get_historical_data(self.symbol, self.timeframe_live, count=150)
                if df is None or len(df) < 50: return
                
                # A. Calcul Bollinger Bands
                sma_series = df['close'].rolling(self.bb_period).mean()
                std_series = df['close'].rolling(self.bb_period).std()
                upper_series = sma_series + (std_series * self.bb_dev)
                lower_series = sma_series - (std_series * self.bb_dev)
                
                # B. Calcul ADX
                adx_series = self._calculate_adx_series(df, self.adx_period)
                
                price = df['close'].iloc[-1]
                upper = upper_series.iloc[-1]
                lower = lower_series.iloc[-1]
                sma = sma_series.iloc[-1]
                adx = adx_series.iloc[-1]

            else:
                # [BACKTEST]
                if 'bb_upper' not in current_bar or 'bb_lower' not in current_bar:
                    return

                price = current_bar['close']
                upper = current_bar['bb_upper']
                lower = current_bar['bb_lower']
                sma = current_bar['bb_sma']
                adx = current_bar.get('adx', 0.0)
            
            # --- 2. LOGICA DE TRANZACȚIONARE ---
            
            positions = self.broker.get_open_positions(self.symbol, self.magic_number)
            
            # A. IEȘIRE (Mean Reversion)
            if positions:
                for pos in positions:
                    ticket = pos.ticket if not is_backtest_run else pos['ticket']
                    p_type = pos.type if not is_backtest_run else pos['type']
                    
                    if p_type == 0: # BUY
                        if price >= sma:
                            self.broker.close_position(self.symbol, ticket, self.magic_number)
                            if not is_backtest_run:
                                self.logger.log(f"💰 [{self.symbol}] BB Profit Taken at Mean ({price:.5f})")
                                
                    elif p_type == 1: # SELL
                        if price <= sma:
                            self.broker.close_position(self.symbol, ticket, self.magic_number)
                            if not is_backtest_run:
                                self.logger.log(f"💰 [{self.symbol}] BB Profit Taken at Mean ({price:.5f})")
                return 

            # B. INTRARE
            if adx > self.adx_max:
                return 

            # Semnal BUY
            if price <= lower:
                band_width = sma - lower
                sl = price - band_width
                tp = sma 
                
                # 🛠️ FIX: Calcul Dinamic Lot
                lot = 0.01
                if not is_backtest_run:
                    lot = self.broker.risk_manager.calculate_lot_size(self.symbol, "BUY", price, sl)
                else:
                    lot = self.broker.calculate_lot_size(self.symbol, sl)
                
                if lot > 0:
                    self.broker.open_market_order(
                        self.symbol, 0, lot, sl, tp, self.magic_number, 
                        comment="BB_Spike_Buy", 
                        ml_features={'bb_dev': self.bb_dev, 'adx': adx}
                    )
                
            # Semnal SELL
            elif price >= upper:
                band_width = upper - sma
                sl = price + band_width
                tp = sma
                
                # 🛠️ FIX: Calcul Dinamic Lot
                lot = 0.01
                if not is_backtest_run:
                    lot = self.broker.risk_manager.calculate_lot_size(self.symbol, "SELL", price, sl)
                else:
                    lot = self.broker.calculate_lot_size(self.symbol, sl)
                
                if lot > 0:
                    self.broker.open_market_order(
                        self.symbol, 1, lot, sl, tp, self.magic_number, 
                        comment="BB_Spike_Sell",
                        ml_features={'bb_dev': self.bb_dev, 'adx': adx}
                    )

        except Exception as e:
            self.logger.log(f"Err BB {self.symbol}: {e}", "error")
            if not is_backtest_run:
                self.logger.log(traceback.format_exc(), "debug")

    # --- HELPER ADX ---
    @staticmethod
    def _calculate_adx_series(df, period):
        df = df.copy()
        df['h-l'] = df['high'] - df['low']
        df['h-pc'] = (df['high'] - df['close'].shift(1)).abs()
        df['l-pc'] = (df['low'] - df['close'].shift(1)).abs()
        df['tr'] = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)
        
        df['up_move'] = df['high'] - df['high'].shift(1)
        df['down_move'] = df['low'].shift(1) - df['low']
        
        df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0.0)
        df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0.0)
        
        alpha = 1.0 / period
        tr_smooth = df['tr'].ewm(alpha=alpha, adjust=False).mean()
        plus_dm_smooth = df['plus_dm'].ewm(alpha=alpha, adjust=False).mean()
        minus_dm_smooth = df['minus_dm'].ewm(alpha=alpha, adjust=False).mean()
        
        plus_di = 100 * (plus_dm_smooth / tr_smooth)
        minus_di = 100 * (minus_dm_smooth / tr_smooth)
        
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
        adx = dx.ewm(alpha=alpha, adjust=False).mean()
        
        return adx