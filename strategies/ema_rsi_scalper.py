# strategies/ema_rsi_scalper.py
import pandas as pd
import traceback
from datetime import datetime
from strategies.base_strategy import BaseStrategy

class EMARsiTrendScalper(BaseStrategy):
    def __init__(self, symbol, config, broker_context):
        super().__init__(symbol, config, broker_context)
        
        self.timeframe_str = self.config.get("timeframe", "M5")
        self.timeframe_h1_str = "H1"
        self.is_backtest = not hasattr(self.broker, 'mt5')

        if not self.is_backtest: # Doar pentru modul live
            self.timeframe_m5_live = self.broker.mt5.get_timeframe(self.timeframe_str)
            self.timeframe_h1_live = self.broker.mt5.get_timeframe(self.timeframe_h1_str)
        
        self.rr_target = float(self.config.get("rr_target", 1.5))
        self.sl_atr_multiplier = float(self.config.get("sl_atr_multiplier", 2.0))
        self.rsi_oversold = float(self.config.get("rsi_oversold", 30))
        self.rsi_overbought = float(self.config.get("rsi_overbought", 70))
        
        self.h1_ema_period = int(self.config.get("h1_ema_period", 50))
        self.m5_atr_period = int(self.config.get("m5_atr_period", 14))
        self.m5_rsi_period = int(self.config.get("m5_rsi_period", 14))
        
        self.last_bar_time = None

    def run_once(self):
        try:
            entry_price = 0.0
            current_m5_atr = 0.0
            current_m5_rsi = 0.0
            trend_up = False
            sl_distance_price = 0.0
            log_prefix = f"    -> [{self.symbol}]" # Prefix pentru loguri de diagnosticare

            if not self.is_backtest:
                # --- LOGICA PENTRU MODUL LIVE ---
                
                # FILTRU 1: Poziție deja deschisă? (Ieșire silențioasă)
                if self.broker.get_open_positions(self.symbol, self.magic_number):
                    return
                
                # FILTRU 2: Date M5 disponibile?
                df_m5 = self.broker.get_historical_data(self.symbol, self.timeframe_m5_live, 100) 
                if df_m5 is None or len(df_m5) < 50: 
                    # Nu logăm aici, get_historical_data ar trebui să logheze deja eroarea
                    return

                # FILTRU 3: Bară nouă? (Ieșire silențioasă, normală)
                current_bar_time = df_m5.index[-1]
                if current_bar_time == self.last_bar_time: return
                self.last_bar_time = current_bar_time
                
                # Logul "Heartbeat"
                self.logger.log(f"🏃 [{self.symbol}] Checking for new signal on bar: {current_bar_time}")
                
                # FILTRU 4: Date H1 disponibile?
                df_h1 = self.broker.get_historical_data(self.symbol, self.timeframe_h1_live, self.h1_ema_period + 5)
                if df_h1 is None or len(df_h1) < self.h1_ema_period:
                    self.logger.log(f"{log_prefix} ⚠️ [FILTER] Date H1 insuficiente. Aștept...")
                    return
                
                # Calcul indicatori
                h1_ema = df_h1['close'].ewm(span=self.h1_ema_period, adjust=False).mean()
                trend_up = df_h1['close'].iloc[-1] > h1_ema.iloc[-1]
                current_m5_atr = self.__class__._calculate_atr(df_m5, self.m5_atr_period, 'ema').iloc[-1]
                current_m5_rsi = self.__class__._calculate_rsi(df_m5, self.m5_rsi_period).iloc[-1]
                entry_price = float(df_m5["close"].iloc[-1])
                sl_distance_price = self.sl_atr_multiplier * current_m5_atr
                
            else:
                # --- LOGICA PENTRU BACKTEST (ULTRA-RAPID) ---
                if self.broker.has_open_position(): return
                current_bar = self.broker.get_current_bar_data()
                if current_bar is None: return
                
                current_m5_rsi = current_bar['M5_rsi']
                current_m5_atr = current_bar['M5_atr'] 
                trend_up = current_bar['H1_trend_up']
                entry_price = float(current_bar["close"])
                sl_distance_price = self.sl_atr_multiplier * current_m5_atr
            
            # FILTRU 5: ATR valid?
            if current_m5_atr <= 0.0: 
                if not self.is_backtest: self.logger.log(f"{log_prefix} ⚠️ [FILTER] ATR M5 este 0 sau negativ. Aștept...")
                return
            
            # --- LOGICA DE PLASARE (COMUNĂ) ---
            pip = self.broker.get_pip_size(self.symbol)
            digits = self.broker.get_digits(self.symbol)
            tp_distance_price = sl_distance_price * self.rr_target
            
            lot = 0.0
            if not self.is_backtest:
                # FILTRU 6: Calcul Lot (Live)
                sl_price = round(entry_price - sl_distance_price, digits) if trend_up else round(entry_price + sl_distance_price, digits)
                lot = self.broker.risk_manager.calculate_lot_size(
                    self.symbol, "BUY" if trend_up else "SELL", entry_price, sl_price
                )
            else:
                # FILTRU 6: Calcul Lot (Backtest)
                lot = self.broker.calculate_lot_size(self.symbol, sl_distance_price / pip)
            
            if lot <= 0:
                if not self.is_backtest: self.logger.log(f"{log_prefix} ⚠️ [FILTER] Lotul calculat este 0 (verifică marja sau calculul riscului). Aștept...")
                return

            # --- FILTRU 7: CONDIȚIILE DE INTRARE (BUY/SELL) ---
            buy_signal = trend_up and current_m5_rsi < self.rsi_oversold
            sell_signal = not trend_up and current_m5_rsi > self.rsi_overbought

            if buy_signal:
                sl = round(entry_price - sl_distance_price, digits)
                tp = round(entry_price + tp_distance_price, digits)
                if not self.is_backtest: self.logger.log(f"{log_prefix} 🚀 Deschidere BUY {self.symbol} @ {entry_price} (RSI: {current_m5_rsi:.2f})")
                self.broker.open_market_order(self.symbol, 0, lot, sl, tp, self.magic_number, "EMA_RSI_BUY")
            
            elif sell_signal:
                sl = round(entry_price + sl_distance_price, digits)
                tp = round(entry_price - tp_distance_price, digits)
                if not self.is_backtest: self.logger.log(f"{log_prefix} 🔻 Deschidere SELL {self.symbol} @ {entry_price} (RSI: {current_m5_rsi:.2f})")
                self.broker.open_market_order(self.symbol, 1, lot, sl, tp, self.magic_number, "EMA_RSI_SELL")
            
            else:
                # Dacă niciun semnal nu e activ, logăm starea curentă
                if not self.is_backtest:
                    trend_str = "UP" if trend_up else "DOWN"
                    self.logger.log(f"{log_prefix} 🚫 [NO-TRADE] Condițiile nu sunt îndeplinite. "
                                    f"(Trend H1: {trend_str}, "
                                    f"RSI M5: {current_m5_rsi:.2f} "
                                    f"[Buy if < {self.rsi_oversold}, Sell if > {self.rsi_overbought}])")
                    
        except Exception as e:
            if not self.is_backtest:
                self.logger.log(f"Eroare în EMARsiTrendScalper {self.symbol}: {e}", "error")
                self.logger.log(traceback.format_exc(), "debug")

    # Metodele statice (folosite și de optimizator)
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
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(span=period, adjust=False).mean()
        avg_loss = loss.ewm(span=period, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)
