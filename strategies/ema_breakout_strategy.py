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
        self.trailing_cfg = config.get("trailing", {})

        # New bar gating
        self.last_bar_time = None

    # ========================
    # Helpers
    # ========================
    def _calculate_ema(self, df):
        for p in self.ema_periods:
            df[f"ema_{p}"] = df["close"].ewm(span=p).mean()
        return df

    def _check_trend_h1(self):
        rates = self.mt5.get_rates(self.symbol, self.timeframe_h1, 200)
        if rates is None or len(rates) == 0:
            return "FLAT"

        df = pd.DataFrame(rates)
        if df.empty:
            return "FLAT"

        df = self._calculate_ema(df)
        last = df.iloc[-1]

        if last[f"ema_{self.ema_periods[0]}"] > last[f"ema_{self.ema_periods[1]}"] > last[f"ema_{self.ema_periods[2]}"]:
            return "UP"
        elif last[f"ema_{self.ema_periods[0]}"] < last[f"ema_{self.ema_periods[1]}"] < last[f"ema_{self.ema_periods[2]}"]:
            return "DOWN"
        return "FLAT"

    def _get_trailing_params(self):
        """Returnează parametrii trailing din config sau fallback din RiskManager."""
        if self.trailing_cfg:
            return {
                "be_min_profit_pips": float(self.trailing_cfg.get("be_min_profit_pips", 10)),
                "step_pips": float(self.trailing_cfg.get("step_pips", 5)),
                "atr_multiplier": float(self.trailing_cfg.get("atr_multiplier", 1.5)),
            }
        if hasattr(self.risk_manager, "get_trailing_params"):
            return self.risk_manager.get_trailing_params()
        return {"be_min_profit_pips": 10.0, "step_pips": 5.0, "atr_multiplier": 1.5}

    def _apply_trailing(self, atr_price, pip):
        """
        Trailing stop pe timeframe-ul strategiei (M5):
          - BE la un prag minim de profit
          - trailing ATR după BE
          - update SL doar dacă depășim step_pips
        """
        positions = self.mt5.positions_get(symbol=self.symbol)
        if not positions:
            return

        trailing = self._get_trailing_params()
        be_pips = trailing["be_min_profit_pips"]
        step_pips = trailing["step_pips"]
        atr_mult = trailing["atr_multiplier"]

        tick = self.mt5.get_symbol_tick(self.symbol)
        if tick is None:
            return
        bid = float(getattr(tick, "bid", 0.0))
        ask = float(getattr(tick, "ask", 0.0))
        if bid <= 0 and ask <= 0:
            return

        for pos in positions:
            # Preț curent
            current = ask if pos.type == self.mt5.ORDER_TYPE_BUY else bid
            entry = float(pos.price_open)
            current_sl = float(pos.sl) if getattr(pos, "sl", 0.0) else 0.0

            # Profit în pips
            if pos.type == self.mt5.ORDER_TYPE_BUY:
                profit_pips = (current - entry) / pip
            else:
                profit_pips = (entry - current) / pip

            if profit_pips < be_pips:
                continue

            # === Break-even ===
            needs_be = (
                (pos.type == self.mt5.ORDER_TYPE_BUY and (current_sl == 0.0 or current_sl < entry)) or
                (pos.type == self.mt5.ORDER_TYPE_SELL and (current_sl == 0.0 or current_sl > entry))
            )
            if needs_be:
                self.trade_manager._update_sl(self.symbol, pos.ticket, entry)
                continue

            # === Trailing dinamic ATR ===
            distance_price = atr_mult * float(atr_price)
            if pos.type == self.mt5.ORDER_TYPE_BUY:
                candidate_sl = current - distance_price
                if candidate_sl > current_sl + step_pips * pip:
                    self.trade_manager._update_sl(self.symbol, pos.ticket, max(entry, candidate_sl))
            else:
                candidate_sl = current + distance_price
                if candidate_sl < current_sl - step_pips * pip:
                    self.trade_manager._update_sl(self.symbol, pos.ticket, min(entry, candidate_sl))

    # ========================
    # Main loop
    # ========================
    def run_once(self):
        try:
            trend = self._check_trend_h1()
            if trend == "FLAT":
                return

            rates = self.mt5.get_rates(self.symbol, self.timeframe_m5, 50)
            if rates is None or len(rates) < 10:
                return

            df = pd.DataFrame(rates)
            if df.empty:
                return

            # New bar gating
            current_bar_time = df["time"].iloc[-1]
            if self.last_bar_time == current_bar_time:
                return
            self.last_bar_time = current_bar_time

            # ATR și filtru volatilitate
            df = calculate_atr(df, 14)
            atr_price = float(df["atr"].iloc[-1])
            pip = self.mt5.get_pip_size(self.symbol)
            atr_pips = atr_price / pip
            if atr_pips < self.risk_manager.get_atr_threshold(self.symbol):
                self.logger.log(f"🔍 {self.symbol} ATR prea mic ({atr_pips:.2f} pips) → skip")
                return

            high5 = df["high"].iloc[-5:].max()
            low5 = df["low"].iloc[-5:].min()

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

            expiration_dt = datetime.datetime.now() + datetime.timedelta(minutes=60)
            request = {
                "action": self.mt5.TRADE_ACTION_PENDING,
                "symbol": self.symbol,
                "volume": lot,
                "type": order_type,
                "price": entry,
                "sl": sl,
                "tp": tp,
                "deviation": 50,
                "magic": 13931993,
                "comment": "EMA Breakout strategy",
                "type_time": self.mt5.ORDER_TIME_SPECIFIED,
                "expiration": int(expiration_dt.timestamp()),
                "type_filling": self.mt5.ORDER_FILLING_RETURN,
            }

            result = self.trade_manager.safe_order_send(request, f"pending {trend} {self.symbol}")
            if result is None:
                self.logger.log(f"❌ order_send returned None → simbol inactiv sau conexiune pierdută")
            else:
                self.logger.log(f"🔍 OrderSend result: retcode={result.retcode}, comment={getattr(result, 'comment', '')}")
                self.logger.log(f"🔍 Full request: {request}")

            # === trailing pentru pozițiile activate ===
            self._apply_trailing(atr_price, pip)

        except Exception as e:
            trace = traceback.format_exc()
            self.logger.log(f"❌ Error in EMABreakoutStrategy {self.symbol}: {e}")
            if self.config.get("debug", False):
                self.logger.log(f"🔍 Stack trace: {trace}")
