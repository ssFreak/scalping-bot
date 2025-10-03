import pandas as pd
import traceback
from datetime import datetime, timedelta

from strategies.base_strategy import BaseStrategy


class PivotStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, mt5):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, mt5)

        self.timeframe = self.mt5.get_timeframe(config.get("timeframe", "M1"))
        self.atr_period = config.get("atr_period", 14)
        self.trailing_cfg = config.get("trailing", {})
        self.atr_method = config.get("atr_method", "ema")
        self.tp_atr_multiplier = config.get("tp_atr_multiplier", 1.5)
        self.sl_atr_multiplier = config.get("sl_atr_multiplier", 2.5)

        # Noile filtre
        self.volume_lookback = config.get("volume_lookback", 20)
        self.min_volume_multiplier = config.get("min_volume_multiplier", 1.2)
        self.cooldown_minutes = config.get("cooldown_minutes", 5)
        self.last_trade_time = None

    def run_once(self):
        try:
            rates = self.mt5.get_rates(self.symbol, self.timeframe, 200)
            if rates is None or len(rates) < 50:
                return
                
            if not self.risk_manager.check_strategy_exposure("pivot", self.symbol):
                return  # skip trade

            df = pd.DataFrame(rates)
            df["atr"] = self._calculate_atr(df, self.atr_period, self.atr_method)

            # === Filtru ATR minim ===
            atr_price = float(df["atr"].iloc[-1])
            pip = self.mt5.get_pip_size(self.symbol)
            atr_pips = atr_price / pip
            atr_threshold = self.risk_manager.get_atr_threshold(self.symbol, self.timeframe)
            if atr_pips < atr_threshold:
                # self.logger.log(f"🔍 Pivot: {self.symbol} ATR prea mic ({atr_pips:.2f} pips) < ({atr_threshold}) → skip")
                return

            # === Filtru volum ===
            if len(df) >= self.volume_lookback + 1:
                recent_vol = df["tick_volume"].iloc[-1]
                avg_vol = df["tick_volume"].iloc[-self.volume_lookback - 1 : -1].mean()
                if recent_vol < self.min_volume_multiplier * avg_vol:
                    return

            # === Cooldown ===
            if self.last_trade_time:
                if datetime.now() < self.last_trade_time + timedelta(minutes=self.cooldown_minutes):
                    return

            # === Confirmare trend H1 (EMA) ===
            trend_up = self._confirm_trend()

            entry_price = float(df["close"].iloc[-1])
            info = self.mt5.get_symbol_info(self.symbol)
            digits = info.digits if info else 5  # fallback

            if trend_up:
                # BUY setup
                sl = entry_price - self.sl_atr_multiplier * atr_price
                tp = entry_price + self._dynamic_rr(atr_pips) * (entry_price - sl)

                entry_price = round(entry_price, digits)
                sl = round(sl, digits)
                tp = round(tp, digits)

                lot = self.risk_manager.calculate_lot_size(self.symbol, "BUY", entry_price, sl)
                if lot > 0 and self.risk_manager.check_free_margin(self.symbol, lot, self.trade_manager.mt5.ORDER_TYPE_BUY):
                    self.trade_manager.open_trade(
                        symbol=self.symbol,
                        order_type=self.trade_manager.mt5.ORDER_TYPE_BUY,
                        lot=lot,
                        sl=sl,
                        tp=tp,
                        deviation_points=self.trade_manager.trade_deviation,
                        comment="Pivot BUY",
                    )
                    self.last_trade_time = datetime.now()

            else:
                # SELL setup
                sl = entry_price + self.sl_atr_multiplier * atr_price
                tp = entry_price - self._dynamic_rr(atr_pips) * (sl - entry_price)

                entry_price = round(entry_price, digits)
                sl = round(sl, digits)
                tp = round(tp, digits)

                lot = self.risk_manager.calculate_lot_size(self.symbol, "SELL", entry_price, sl)
                if lot > 0 and self.risk_manager.check_free_margin(self.symbol, lot, self.trade_manager.mt5.ORDER_TYPE_SELL):
                    self.trade_manager.open_trade(
                        symbol=self.symbol,
                        order_type=self.trade_manager.mt5.ORDER_TYPE_SELL,
                        lot=lot,
                        sl=sl,
                        tp=tp,
                        deviation_points=self.trade_manager.trade_deviation,
                        comment="Pivot SELL",
                    )
                    self.last_trade_time = datetime.now()

            # === Trailing stop pe timeframe-ul strategiei ===
            self._apply_trailing(df, atr_price, pip)

        except Exception as e:
            self.logger.log(f"❌ Error in PivotStrategy {self.symbol}: {e}")
            self.logger.log(traceback.format_exc())

    def _calculate_atr(self, df, period, method="ema"):
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

        if method == "sma":
            return tr.rolling(period).mean()
        elif method == "ema":
            return tr.ewm(span=period, adjust=False).mean()
        elif method == "rma":  # Wilder’s ATR
            alpha = 1 / period
            return tr.ewm(alpha=alpha, adjust=False).mean()
        else:
            raise ValueError(f"Unknown ATR method: {method}")

    def _dynamic_rr(self, atr_pips):
        return min(3, max(1, atr_pips / 10))

    def _confirm_trend(self):
        tf_h1 = self.mt5.get_timeframe("H1")
        rates = self.mt5.get_rates(self.symbol, tf_h1, 200)
        if rates is None or len(rates) < 50:
            return True
        df = pd.DataFrame(rates)
        df["ema8"] = df["close"].ewm(span=8).mean()
        df["ema21"] = df["close"].ewm(span=21).mean()
        return df["ema8"].iloc[-1] > df["ema21"].iloc[-1]

    def _apply_trailing(self, df, atr_price, pip):
        """
        Refactor trailing: delegăm către TradeManager.apply_trailing pentru fiecare poziție.
        """
        positions = self.mt5.positions_get(symbol=self.symbol)
        if not positions:
            return

        # Parametrii per strategie (fallback pe global din RiskManager)
        params = self.trailing_cfg or (
            self.risk_manager.get_trailing_params() if hasattr(self.risk_manager, "get_trailing_params") else {
                "be_min_profit_pips": 10.0,
                "step_pips": 5.0,
                "atr_multiplier": 1.5,
            }
        )

        for pos in positions:
            try:
                # APEL CORECT:
                self.trade_manager.apply_trailing(self.symbol, pos, atr_price, pip, params)
            except Exception as e:
                self.logger.log(
                    f"❌ apply_trailing error {self.symbol} ticket={getattr(pos,'ticket','?')}: {e}"
                )
