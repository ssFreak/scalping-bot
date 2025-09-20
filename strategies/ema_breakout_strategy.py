import pandas as pd
import traceback
from datetime import datetime, timedelta

from strategies.base_strategy import BaseStrategy
from core.utils import calculate_atr


class EMABreakoutStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, mt5):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, mt5)
        self.timeframe_h1 = self.mt5.get_timeframe("H1")
        self.timeframe_m5 = self.mt5.get_timeframe("M5")

        # Parametri din config
        self.ema_periods = config.get("ema_periods", [8, 13, 21])
        self.offset_pips = config.get("offset_pips", 3)
        self.order_expiry_minutes = config.get("order_expiry_minutes", 60)
        self.min_atr_pips = config.get("min_atr_pips", 5)
        self.rr_dynamic = config.get("rr_dynamic", True)

    def _calculate_ema(self, df):
        for p in self.ema_periods:
            df[f"ema_{p}"] = df["close"].ewm(span=p).mean()
        return df

    def _check_trend_h1(self):
        df = self.mt5.get_rates(self.symbol, self.timeframe_h1, 200)
        if df is None or df.empty:
            return "FLAT"

        df = self._calculate_ema(df)
        last = df.iloc[-1]

        if last[f"ema_{self.ema_periods[0]}"] > last[f"ema_{self.ema_periods[1]}"] > last[f"ema_{self.ema_periods[2]}"]:
            return "UP"
        elif last[f"ema_{self.ema_periods[0]}"] < last[f"ema_{self.ema_periods[1]}"] < last[f"ema_{self.ema_periods[2]}"]:
            return "DOWN"
        return "FLAT"

    def run_once(self):
        try:
            trend = self._check_trend_h1()
            if trend == "FLAT":
                return

            df = self.mt5.get_rates(self.symbol, self.timeframe_m5, 50)
            if df is None or df.empty or len(df) < 10:
                return

            df = calculate_atr(df, 14)
            atr_pips = df["atr"].iloc[-1] / self.mt5.get_pip_size(self.symbol)
            if atr_pips < self.min_atr_pips:
                self.logger.log(f"🔍 {self.symbol} ATR prea mic ({atr_pips:.2f} pips) → skip")
                return

            high5 = df["high"].iloc[-5:].max()
            low5 = df["low"].iloc[-5:].min()
            pip = self.mt5.get_pip_size(self.symbol)

            if trend == "UP":
                entry = high5 + self.offset_pips * pip
                sl = low5
                rr = 1.0 + (atr_pips / 10 if self.rr_dynamic else 0.0)
                tp = entry + rr * (entry - sl)
                order_type = self.mt5.ORDER_TYPE_BUY_STOP
            elif trend == "DOWN":
                entry = low5 - self.offset_pips * pip
                sl = high5
                rr = 1.0 + (atr_pips / 10 if self.rr_dynamic else 0.0)
                tp = entry - rr * (sl - entry)
                order_type = self.mt5.ORDER_TYPE_SELL_STOP
            else:
                return

            lot = self.risk_manager.calculate_lot_size(self.symbol, trend, entry, sl)
            if lot <= 0 or not self.risk_manager.check_free_margin():
                return

            expiration = datetime.now() + timedelta(minutes=self.order_expiry_minutes)

            request = {
                "action": self.mt5.TRADE_ACTION_PENDING,
                "symbol": self.symbol,
                "volume": lot,
                "type": order_type,
                "price": entry,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": 445566,
                "comment": "EMA Breakout strategy",
                "type_time": self.mt5.ORDER_TIME_SPECIFIED,
                "expiration": expiration,
                "type_filling": self.mt5.ORDER_FILLING_RETURN,
            }

            result = self.mt5.order_send(request)
            if result and result.retcode == 10009:  # TRADE_RETCODE_DONE
                self.logger.log(f"✅ Pending order {trend} {self.symbol} plasat @ {entry}")
            else:
                self.logger.log(f"❌ Eroare plasare order {self.symbol}: {result}")

        except Exception as e:
            trace = traceback.format_exc()
            self.logger.log(f"❌ Error in EMABreakoutStrategy {self.symbol}: {e}")
            if self.config.get("debug", False):
                self.logger.log(f"🔍 Stack trace: {trace}")
