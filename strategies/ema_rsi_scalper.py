# strategies/ema_rsi_scalper.py - IMPLEMENTARE CU PERIOADE GENERICI

import pandas as pd
import traceback
from datetime import datetime
from strategies.base_strategy import BaseStrategy
import numpy as np 

class EMARsiTrendScalper(BaseStrategy):
    
    def __init__(self, symbol, config, broker_context):
        super().__init__(symbol, config, broker_context)
        
        # TIME FRAME-URI
        self.timeframe_str = self.config.get("timeframe", "M5") 
        self.timeframe_trend_str = self.config.get("timeframe_trend", "H1") 
        self.is_backtest = not hasattr(self.broker, 'mt5')

        if not self.is_backtest: 
            self.timeframe_base_live = self.broker.mt5.get_timeframe(self.timeframe_str) 
            self.timeframe_trend_live = self.broker.mt5.get_timeframe(self.timeframe_trend_str)
        
        # ‼️ PARAMETRII NOI GENERICI ‼️
        self.ema_period_trend = int(self.config.get("ema_period", 50))
        self.atr_period_base = int(self.config.get("atr_period", 14))
        self.rsi_period_base = int(self.config.get("rsi_period", 14))

        self.rr_target = float(self.config.get("rr_target", 1.5))
        self.sl_atr_multiplier = float(self.config.get("sl_atr_multiplier", 2.0))
        self.rsi_oversold = float(self.config.get("rsi_oversold", 30))
        self.rsi_overbought = float(self.config.get("rsi_overbought", 70))
        
        self.last_bar_time = None
        
        magic_base = self.config.get('magic_number_base', 19133000)
        offset_magic = self.config.get('magic_number_offset', 0)
        self.magic_number = magic_base + offset_magic

    def run_once(self, current_bar=None):
        try:
            is_backtest_run = current_bar is not None
            
            # Citim distanța din config
            ema_dist_pips = float(self.config.get("ema_distance_pips", 8.0)) 
            
            # Inițializare variabile
            current_atr = 0.0
            current_rsi = 0.0
            previous_rsi = 0.0 
            trend_up = False 
            entry_price = 0.0
            current_trend_ema = 0.0
            
            if not is_backtest_run:
                # --- LIVE ---
                df_trend = self.broker.get_historical_data(self.symbol, self.timeframe_trend_live, count=self.ema_period_trend + 5)
                if df_trend is None or len(df_trend) < self.ema_period_trend: return
                
                trend_ema_series = df_trend['close'].ewm(span=self.ema_period_trend, adjust=False).mean()
                
                # 🛑 FIX LIVE: Verificăm trendul pe bara ANTERIOARĂ (Închisă)
                # iloc[-1] este bara curentă (neterminată). iloc[-2] este ultima închisă.
                trend_up = df_trend['close'].iloc[-2] > trend_ema_series.iloc[-2]
                
                current_trend_ema = trend_ema_series.iloc[-1] # Pentru distanță e OK cea curentă
                
                df_base = self.broker.get_historical_data(self.symbol, self.timeframe_base_live, count=150)
                if df_base is None or df_base.empty or len(df_base) < 50: return

                current_bar_time = df_base.index[-1]
                if current_bar_time == self.last_bar_time: return
                self.last_bar_time = current_bar_time

                atr_series = self._calculate_atr(df_base, self.atr_period_base, 'ema')
                rsi_series = self._calculate_rsi(df_base, self.rsi_period_base)
                
                if len(rsi_series) < 2: return 
                
                current_atr = atr_series.iloc[-1]
                current_rsi = rsi_series.iloc[-1]
                previous_rsi = rsi_series.iloc[-2] 
                entry_price = float(df_base["close"].iloc[-1])

            else:
                # --- BACKTEST ---
                # Verificăm dacă avem datele necesare
                if 'M5_rsi' not in current_bar or 'M5_atr' not in current_bar or 'H1_trend_up' not in current_bar:
                    return
                
                current_atr = current_bar['M5_atr']
                current_rsi = current_bar['M5_rsi']
                trend_up = current_bar['H1_trend_up'] 
                entry_price = current_bar['close']
                
                # 🛠️ FIX 1: Citim EMA reală din pre-procesare (dacă există), altfel fallback
                current_trend_ema = current_bar.get('H1_ema_trend', entry_price)
                
                # Gestionare trailing în backtest
                positions = self.broker.get_open_positions(self.symbol, self.magic_number)
                if positions:
                    # Aplica trailing și verifică dacă poziția s-a închis
                    for pos in positions:
                        self.broker.apply_trailing_stop(self.symbol, pos, 0, 0, {})
                    # Dacă încă avem poziții deschise, nu intrăm din nou (o poziție per simbol)
                    if self.broker.get_open_positions(self.symbol, self.magic_number): return
                
                previous_rsi = current_rsi # Simplificare

            # --- LOGICA COMUNĂ ---
            pip = self.broker.get_pip_size(self.symbol)
            digits = self.broker.get_digits(self.symbol)
            
            if current_atr <= 0: return

            sl_distance_price = self.sl_atr_multiplier * current_atr
            tp_distance_price = sl_distance_price * self.rr_target
            
            # 🛠️ FIX 2: CALCUL DINAMIC AL LOTULUI (ȘI ÎN BACKTEST)
            lot = 0.0
            test_sl_price = entry_price - sl_distance_price # Ipotetic SL
            
            if not is_backtest_run:
                lot = self.broker.risk_manager.calculate_lot_size(
                    self.symbol, "BUY", entry_price, test_sl_price
                )
            else:
                # Folosim metoda din BacktestBroker care respectă risk_per_trade din config
                lot = self.broker.calculate_lot_size(self.symbol, test_sl_price)
            
            if lot <= 0: return

            # --- FILTRU PROXIMITATE ---
            max_dist_price = ema_dist_pips * pip 
            dist_to_ema = abs(entry_price - current_trend_ema)
            cond_ema_proximity = dist_to_ema <= max_dist_price
            
            # --- SEMNAL INTRARE ---
            if not is_backtest_run:
                # Live logic (crossover)
                cond_buy = trend_up and cond_ema_proximity and (previous_rsi <= self.rsi_oversold) and (current_rsi > self.rsi_oversold)
                cond_sell = (not trend_up) and cond_ema_proximity and (previous_rsi >= self.rsi_overbought) and (current_rsi < self.rsi_overbought)
            else:
                # Backtest logic (level based)
                cond_buy = trend_up and cond_ema_proximity and (current_rsi <= self.rsi_oversold)
                cond_sell = (not trend_up) and cond_ema_proximity and (current_rsi >= self.rsi_overbought)
            
            ml_features = {
                'H1_trend_up': bool(trend_up), 
                'M5_rsi': round(current_rsi, 2), 
                'M5_atr': round(current_atr, digits) 
            }
            
            if cond_buy:
                sl = round(entry_price - sl_distance_price, digits)
                tp = round(entry_price + tp_distance_price, digits)
                
                if not is_backtest_run:
                    self.logger.log(f"🚀 LIVE BUY {self.symbol} | Lot: {lot} | RSI: {current_rsi:.2f}")
                
                self.broker.open_market_order(self.symbol, 0, lot, sl, tp, self.magic_number, "EMA_RSI_BUY", ml_features=ml_features)
            
            elif cond_sell:
                sl = round(entry_price + sl_distance_price, digits)
                tp = round(entry_price - tp_distance_price, digits)
                
                if not is_backtest_run:
                    self.logger.log(f"🔻 LIVE SELL {self.symbol} | Lot: {lot} | RSI: {current_rsi:.2f}")

                self.broker.open_market_order(self.symbol, 1, lot, sl, tp, self.magic_number, "EMA_RSI_SELL", ml_features=ml_features)
            
        except Exception as e:
            self.logger.log(f"❌ Eroare în EMARsiTrendScalper {self.symbol}: {e}", "error")
            if not is_backtest_run:
                self.logger.log(traceback.format_exc(), "debug")

    @staticmethod
    def _calculate_atr(df, period, method="ema"):
        df_copy = df.copy()
        high_low = df_copy["high"] - df_copy["low"]
        high_close = (df_copy["high"] - df_copy["close"].shift()).abs()
        low_close = (df_copy["low"] - df_copy["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        if method == "sma": return tr.rolling(period).mean()
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        df_copy = df.copy()
        delta = df_copy['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(span=period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(span=period, adjust=False).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))