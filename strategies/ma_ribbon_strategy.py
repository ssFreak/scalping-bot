import pandas as pd
import traceback
from datetime import datetime, timedelta

from strategies.base_strategy import BaseStrategy

class MARibbonStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, mt5):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, mt5)

        self.timeframe = self.mt5.get_timeframe(config.get("timeframe", "M5"))
        self.sma_periods = config.get("sma_periods", [5, 8, 13])
        self.atr_period = config.get("atr_period", 14)
        self.tp_atr_multiplier = config.get("tp_atr_multiplier", 1.5)
        self.sl_atr_multiplier = config.get("sl_atr_multiplier", 2.5)

        # Trailing configurabil la nivel de strategie (dacă lipsește, folosim fallback din RiskManager)
        self.trailing_cfg = config.get("trailing", {})

        # Filtre suplimentare
        self.volume_lookback = config.get("volume_lookback", 20)
        self.min_volume_multiplier = config.get("min_volume_multiplier", 1.2)
        self.cooldown_minutes = config.get("cooldown_minutes", 5)
        self.last_trade_time = None

        # New-bar gating (evităm execuții multiple în cadrul aceleiași lumânări)
        self._last_bar_time = None

    def run_once(self):
        try:
            rates = self.mt5.get_rates(self.symbol, self.timeframe, 200)
            if rates is None or len(rates) < max(self.sma_periods) + self.atr_period:
                return

            df = pd.DataFrame(rates)
            if df.empty:
                return

            # New bar gating
            current_bar_time = df["time"].iloc[-1]
            if self._last_bar_time == current_bar_time:
                return
            self._last_bar_time = current_bar_time

            # SMAs
            for p in self.sma_periods:
                df[f"SMA_{p}"] = df["close"].rolling(window=p).mean()

            # ATR (în preț)
            df["atr"] = self._calculate_atr(df, self.atr_period)
            atr_price = float(df["atr"].iloc[-1])
            pip = self.mt5.get_pip_size(self.symbol)
            if pip <= 0:
                return
            atr_pips = atr_price / pip

            # === Filtru ATR minim (din RiskManager threshold pe simbol) ===
            if atr_pips < self.risk_manager.get_atr_threshold(self.symbol):
                return

            # === Filtru volum ===
            if len(df) >= self.volume_lookback + 1:
                recent_vol = df["tick_volume"].iloc[-1]
                avg_vol = df["tick_volume"].iloc[-self.volume_lookback-1:-1].mean()
                if pd.isna(avg_vol) or recent_vol < self.min_volume_multiplier * avg_vol:
                    return

            # === Cooldown ===
            if self.last_trade_time:
                if datetime.now() < self.last_trade_time + timedelta(minutes=self.cooldown_minutes):
                    return

            # === Confirmare trend H1 ===
            if not self._confirm_trend():
                return

            sma5, sma8, sma13 = df["SMA_5"].iloc[-1], df["SMA_8"].iloc[-1], df["SMA_13"].iloc[-1]
            if pd.isna(sma5) or pd.isna(sma8) or pd.isna(sma13):
                return

            entry_price = float(df["close"].iloc[-1])

            # Semnale
            direction = None
            sl = None
            tp = None

            if sma5 > sma8 > sma13:  # BUY
                sl = entry_price - self.sl_atr_multiplier * atr_price
                # R:R dinamic ușor: multiplicator în funcție de atr_pips
                rr_mult = self._dynamic_rr(atr_pips)
                tp = entry_price + rr_mult * (entry_price - sl)
                direction = "BUY"
            elif sma5 < sma8 < sma13:  # SELL
                sl = entry_price + self.sl_atr_multiplier * atr_price
                rr_mult = self._dynamic_rr(atr_pips)
                tp = entry_price - rr_mult * (sl - entry_price)
                direction = "SELL"

            if direction is None:
                # Nu există setup clar
                # Aplicăm trailing (dacă există poziții) chiar și fără semnal nou
                self._apply_trailing(df, atr_price, pip)
                return

            # Lot sizing + verificări de marjă
            lot = self.risk_manager.calculate_lot_size(self.symbol, direction, entry_price, sl)
            if lot > 0 and self.risk_manager.check_free_margin():
                placed = self.trade_manager.open_trade(self.symbol, direction, lot, entry_price, sl, tp)
                if placed:
                    self.last_trade_time = datetime.now()

            # Trailing integrat pe timeframe-ul strategiei
            self._apply_trailing(df, atr_price, pip)

        except Exception as e:
            self.logger.log(f"❌ Error in MARibbonStrategy {self.symbol}: {e}")
            self.logger.log(traceback.format_exc())

    # ----------------------------
    # Helpers
    # ----------------------------
    def _calculate_atr(self, df, period):
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def _dynamic_rr(self, atr_pips: float) -> float:
        # RR flexibil între 1 și 3 în funcție de vol
        return min(3.0, max(1.0, atr_pips / 10.0))

    def _confirm_trend(self) -> bool:
        tf_h1 = self.mt5.get_timeframe("H1")
        rates = self.mt5.get_rates(self.symbol, tf_h1, 200)
        if rates is None or len(rates) < 50:
            return True  # când nu avem destule date, nu blocăm strategia
        df = pd.DataFrame(rates)
        if df.empty:
            return True
        df["ema8"] = df["close"].ewm(span=8).mean()
        df["ema21"] = df["close"].ewm(span=21).mean()
        return float(df["ema8"].iloc[-1]) > float(df["ema21"].iloc[-1])

    # ----------------------------
    # Trailing integrat în strategie
    # ----------------------------
    def _get_trailing_params(self):
        """
        Ia trailing din config-ul strategiei dacă există;
        altfel, folosește fallback-ul din RiskManager (compatibil cu implementarea ta).
        """
        if self.trailing_cfg:
            return {
                "be_min_profit_pips": float(self.trailing_cfg.get("be_min_profit_pips", 10)),
                "step_pips": float(self.trailing_cfg.get("step_pips", 5)),
                "atr_multiplier": float(self.trailing_cfg.get("atr_multiplier", 1.5)),
            }
        if hasattr(self.risk_manager, "get_trailing_params"):
            return self.risk_manager.get_trailing_params()
        # fallback “safe”
        return {"be_min_profit_pips": 10.0, "step_pips": 5.0, "atr_multiplier": 1.5}

    def _apply_trailing(self, df: pd.DataFrame, atr_price: float, pip: float):
        """
        Trailing stop pe timeframe-ul strategiei:
          1) Break-even după be_min_profit_pips
          2) După BE, trailing dinamic: SL urmărește prețul cu atr_multiplier * ATR
          3) Actualizăm SL doar dacă depășim step_pips
        """
        positions = self.mt5.positions_get(symbol=self.symbol)
        if not positions:
            return

        trailing = self._get_trailing_params()
        be_pips = float(trailing["be_min_profit_pips"])
        step_pips = float(trailing["step_pips"])
        atr_mult = float(trailing["atr_multiplier"])

        tick = self.mt5.get_symbol_tick(self.symbol)
        if tick is None:
            return

        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        if bid <= 0.0 and ask <= 0.0:
            return

        for pos in positions:
            # Prețul curent relevant
            if pos.type == self.mt5.ORDER_TYPE_BUY:
                current = ask
            else:
                current = bid

            entry = float(pos.price_open)
            current_sl = float(pos.sl) if getattr(pos, "sl", 0.0) else 0.0

            # Profit în pips
            if pos.type == self.mt5.ORDER_TYPE_BUY:
                profit_pips = (current - entry) / pip
            else:
                profit_pips = (entry - current) / pip

            if profit_pips < be_pips:
                continue  # nu facem nimic până nu depășim pragul BE

            # === 1) Mutare la break-even dacă SL e dincolo de entry ===
            needs_be = (
                (pos.type == self.mt5.ORDER_TYPE_BUY and (current_sl == 0.0 or current_sl < entry)) or
                (pos.type == self.mt5.ORDER_TYPE_SELL and (current_sl == 0.0 or current_sl > entry))
            )
            if needs_be:
                self.trade_manager._update_sl(self.symbol, pos.ticket, entry)
                # pe următoarea iterație vom continua trailing-ul dinamic
                continue

            # === 2) Trailing dinamic după break-even ===
            distance_price = atr_mult * float(atr_price)

            if pos.type == self.mt5.ORDER_TYPE_BUY:
                candidate_sl = current - distance_price
                # actualizează doar dacă am avansat cel puțin step_pips
                if candidate_sl > current_sl + step_pips * pip:
                    new_sl = max(entry, candidate_sl)
                    self.trade_manager._update_sl(self.symbol, pos.ticket, new_sl)
            else:  # SELL
                candidate_sl = current + distance_price
                if candidate_sl < current_sl - step_pips * pip:
                    new_sl = min(entry, candidate_sl)
                    self.trade_manager._update_sl(self.symbol, pos.ticket, new_sl)
