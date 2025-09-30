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
        self.atr_method = config.get("atr_method", "ema")
        self.tp_atr_multiplier = config.get("tp_atr_multiplier", 1.5)
        self.sl_atr_multiplier = config.get("sl_atr_multiplier", 2.5)

        # Trailing configurabil la nivel de strategie (fallback pe RiskManager)
        self.trailing_cfg = config.get("trailing", {})

        # Filtre suplimentare
        self.volume_lookback = config.get("volume_lookback", 20)
        self.min_volume_multiplier = config.get("min_volume_multiplier", 1.2)
        self.cooldown_minutes = config.get("cooldown_minutes", 5)
        self.last_trade_time = None

        # New-bar gating
        self._last_bar_time = None

    def run_once(self):
        try:
            rates = self.mt5.get_rates(self.symbol, self.timeframe, 200)
            if rates is None or len(rates) < max(self.sma_periods) + self.atr_period:
                return

            df = pd.DataFrame(rates)
            if df.empty:
                return
                
            if not self.risk_manager.check_strategy_exposure("ma_ribbon", self.symbol):
                return  # skip trade

            # New bar gating
            current_bar_time = df["time"].iloc[-1]
            if self._last_bar_time == current_bar_time:
                return
            self._last_bar_time = current_bar_time

            # SMAs
            for p in self.sma_periods:
                df[f"SMA_{p}"] = df["close"].rolling(window=p).mean()

            # ATR
            df["atr"] = self._calculate_atr(df, self.atr_period, self.atr_method)
            atr_price = float(df["atr"].iloc[-1])
            pip = self.mt5.get_pip_size(self.symbol)
            if pip <= 0:
                return
            atr_pips = atr_price / pip
            atr_threshold = self.risk_manager.get_atr_threshold(self.symbol, self.timeframe)
            if atr_pips < atr_threshold:
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
            direction, sl, tp = None, None, None

            if sma5 > sma8 > sma13:  # BUY
                sl = entry_price - self.sl_atr_multiplier * atr_price
                tp = entry_price + self._dynamic_rr(atr_pips) * (entry_price - sl)
                direction = "BUY"
            elif sma5 < sma8 < sma13:  # SELL
                sl = entry_price + self.sl_atr_multiplier * atr_price
                tp = entry_price - self._dynamic_rr(atr_pips) * (sl - entry_price)
                direction = "SELL"

            if direction is None:
                # Aplicăm trailing chiar și fără semnal nou
                self._apply_trailing(df, atr_price, pip)
                return

            order_type = self.mt5.ORDER_TYPE_BUY if direction == "BUY" else self.mt5.ORDER_TYPE_SELL

            # Lot sizing + verificări de marjă    
            lot = self.risk_manager.calculate_lot_size(self.symbol, direction, entry_price, sl)
            if lot > 0 and self.risk_manager.check_free_margin():
                info = self.mt5.get_symbol_info(self.symbol)
                digits = info.digits if info else 5
                sl = round(sl, digits)
                tp = round(tp, digits)

                placed = self.trade_manager.open_trade(
                    symbol=self.symbol, 
                    order_type=order_type,
                    lot=lot,
                    sl=sl, 
                    tp=tp,
                    deviation=self.trade_manager.trade_deviation,
                    comment=f"MA Ribbon {direction}"
                )
                if placed:
                    self.last_trade_time = datetime.now()

            # Trailing integrat pe timeframe
            self._apply_trailing(df, atr_price, pip)

        except Exception as e:
            self.logger.log(f"❌ Error in MARibbonStrategy {self.symbol}: {e}")
            self.logger.log(traceback.format_exc())

    # ----------------------------
    # Helpers
    # ----------------------------
    def _calculate_atr(self, df, period, method="ema"):
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

        if method == "sma":
            return tr.rolling(period).mean()
        elif method == "ema":
            return tr.ewm(span=period, adjust=False).mean()
        elif method == "rma":
            alpha = 1 / period
            return tr.ewm(alpha=alpha, adjust=False).mean()
        else:
            raise ValueError(f"Unknown ATR method: {method}")

    def _dynamic_rr(self, atr_pips: float) -> float:
        return min(3.0, max(1.0, atr_pips / 10.0))

    def _confirm_trend(self) -> bool:
        tf_h1 = self.mt5.get_timeframe("H1")
        rates = self.mt5.get_rates(self.symbol, tf_h1, 200)
        if rates is None or len(rates) < 50:
            return True
        df = pd.DataFrame(rates)
        if df.empty:
            return True
        df["ema8"] = df["close"].ewm(span=8).mean()
        df["ema21"] = df["close"].ewm(span=21).mean()
        return float(df["ema8"].iloc[-1]) > float(df["ema21"].iloc[-1])

    # ----------------------------
    # Trailing (delegat)
    # ----------------------------
    def _apply_trailing(self, df: pd.DataFrame, atr_price: float, pip: float):
        positions = self.mt5.positions_get(symbol=self.symbol)
        if not positions:
            return

        # Parametrii per strategie sau fallback
        params = self.trailing_cfg or (
            self.risk_manager.get_trailing_params() if hasattr(self.risk_manager, "get_trailing_params") else {
                "be_min_profit_pips": 10.0,
                "step_pips": 5.0,
                "atr_multiplier": 1.5,
            }
        )

        for pos in positions:
            try:
                self.trade_manager.apply_trailing(self.symbol, pos, atr_price, pip, params)
            except Exception as e:
                self.logger.log(
                    f"❌ apply_trailing error {self.symbol} ticket={getattr(pos,'ticket','?')}: {e}"
                )
