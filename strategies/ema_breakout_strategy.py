import pandas as pd
import traceback
from datetime import datetime, timedelta, timezone

from strategies.base_strategy import BaseStrategy
from core.utils import calculate_atr


class EMABreakoutStrategy(BaseStrategy):
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, mt5):
        super().__init__(symbol, config, logger, risk_manager, trade_manager, mt5)
        self.timeframe_h1 = self.mt5.get_timeframe("H1")
        self.timeframe_m5 = self.mt5.get_timeframe("M5")

        # Parametri din config
        self.ema_periods = config.get("ema_periods", [8, 13, 21])
        self.offset_pips = float(config.get("offset_pips", 3))
        self.order_expiry_minutes = int(config.get("order_expiry_minutes", 60))
        self.min_atr_pips = float(config.get("min_atr_pips", 5))
        self.rr_dynamic = bool(config.get("rr_dynamic", True))
        self.trailing_cfg = config.get("trailing", {})

        # New bar gating
        self.last_bar_time = None

        # Expirare manuală: {order_ticket: datetime_expiry_utc}
        self._pending_expirations = {}

    # ========================
    # Helpers
    # ========================
    def _calculate_ema(self, df: pd.DataFrame) -> pd.DataFrame:
        for p in self.ema_periods:
            df[f"ema_{p}"] = df["close"].ewm(span=int(p)).mean()
        return df

    def _check_trend_h1(self) -> str:
        rates = self.mt5.get_rates(self.symbol, self.timeframe_h1, 200)
        if rates is None or len(rates) == 0:
            return "FLAT"

        df = pd.DataFrame(rates)
        if df.empty:
            return "FLAT"

        df = self._calculate_ema(df)

        try:
            ema1 = float(df[f"ema_{self.ema_periods[0]}"].iloc[-1])
            ema2 = float(df[f"ema_{self.ema_periods[1]}"].iloc[-1])
            ema3 = float(df[f"ema_{self.ema_periods[2]}"].iloc[-1])
        except Exception as e:
            self.logger.log(f"❌ EMA calc error: {e}")
            return "FLAT"

        if ema1 > ema2 > ema3:
            return "UP"
        if ema1 < ema2 < ema3:
            return "DOWN"
        return "FLAT"

    def _get_trailing_params(self):
        if self.trailing_cfg:
            return {
                "be_min_profit_pips": float(self.trailing_cfg.get("be_min_profit_pips", 10)),
                "step_pips": float(self.trailing_cfg.get("step_pips", 5)),
                "atr_multiplier": float(self.trailing_cfg.get("atr_multiplier", 1.5)),
            }
        if hasattr(self.risk_manager, "get_trailing_params"):
            return self.risk_manager.get_trailing_params()
        return {"be_min_profit_pips": 10.0, "step_pips": 5.0, "atr_multiplier": 1.5}

    def _apply_trailing(self, atr_price: float, pip: float) -> None:
        """
        Refactor: delegăm trailing-ul către TradeManager.apply_trailing pentru fiecare poziție.
        """
        positions = self.mt5.positions_get(symbol=self.symbol)
        if not positions:
            return

        params = self._get_trailing_params()
        for pos in positions:
            try:
                self.trade_manager.apply_trailing(self.symbol, pos, atr_price, pip, params)
            except Exception as e:
                self.logger.log(
                    f"❌ apply_trailing error {self.symbol} ticket={getattr(pos,'ticket','?')}: {e}"
                )

    # --- Expirare manuală (helperi) ---
    def _record_pending_expiry(self, order_ticket: int) -> None:
        """Memorează expirarea manuală pentru un ordin pending plasat."""
        expiry = datetime.now(timezone.utc) + timedelta(minutes=self.order_expiry_minutes)
        self._pending_expirations[int(order_ticket)] = expiry
        self.logger.log(f"⏱️ Pending ticket={order_ticket} va expira manual la {expiry.isoformat()}")

    def _check_and_purge_expired_pending(self) -> None:
        """Șterge ordinele pending expirate și curăță cele executate/dispărute."""
        if not self._pending_expirations:
            return

        now = datetime.now(timezone.utc)
        # Ordine active pentru simbol
        open_orders = self.mt5.orders_get(symbol=self.symbol) or []
        open_tickets = {int(getattr(o, "ticket", 0)) for o in open_orders}

        to_delete = []
        for ticket, exp_time in list(self._pending_expirations.items()):
            # dacă ordinul nu mai e în piață -> a fost executat/șters; îl scoatem din dict
            if ticket not in open_tickets:
                to_delete.append(ticket)
                continue
            # dacă a expirat -> îl ștergem explicit
            if now >= exp_time:
                req = {"action": self.mt5.TRADE_ACTION_REMOVE, "order": int(ticket), "comment": "Manual expiration"}
                res = self.mt5.order_send(req)
                if res and getattr(res, "retcode", None) == self.mt5.TRADE_RETCODE_DONE:
                    self.logger.log(f"⏰ Pending order {ticket} expirat și șters la {now.isoformat()}")
                    to_delete.append(ticket)
                else:
                    self.logger.log(f"⚠️ Remove failed ticket={ticket}: retcode={getattr(res,'retcode',None)}, "
                                    f"comment={getattr(res,'comment','')}")
        for t in to_delete:
            self._pending_expirations.pop(t, None)

    # ========================
    # Main loop
    # ========================
    def run_once(self):
        try:
            # housekeeping: expirări manuale
            self._check_and_purge_expired_pending()

            trend = self._check_trend_h1()
            if trend == "FLAT":
                return

            rates = self.mt5.get_rates(self.symbol, self.timeframe_m5, 50)
            if rates is None or len(rates) < 10:
                return

            df = pd.DataFrame(rates)
            if df.empty:
                return

            current_bar_time = int(df["time"].iloc[-1])
            if self.last_bar_time == current_bar_time:
                return
            self.last_bar_time = current_bar_time

            df = calculate_atr(df, 14)
            atr_price = float(df["atr"].iloc[-1])
            pip = float(self.mt5.get_pip_size(self.symbol))
            atr_pips = float(atr_price / pip)

            # threshold scalar safe
            try:
                atr_threshold = float(self.risk_manager.get_atr_threshold(self.symbol))
            except TypeError:
                # unele implementări cer timeframe
                atr_threshold = float(self.risk_manager.get_atr_threshold(self.symbol, self.timeframe_m5))
            except Exception:
                atr_threshold = self.min_atr_pips

            if atr_pips < atr_threshold:
                # self.logger.log(f"🔍 {self.symbol} ATR prea mic ({atr_pips:.2f} pips) < {atr_threshold} → skip")
                return

            high5 = float(df["high"].iloc[-5:].max())
            low5 = float(df["low"].iloc[-5:].min())

            if trend == "UP":
                entry = float(high5 + self.offset_pips * pip)
                sl = float(low5)
                rr = 1.0 + (atr_pips / 10.0 if self.rr_dynamic else 0.0)
                tp = float(entry + rr * (entry - sl))
                order_type = self.mt5.ORDER_TYPE_BUY_STOP
            elif trend == "DOWN":
                entry = float(low5 - self.offset_pips * pip)
                sl = float(high5)
                rr = 1.0 + (atr_pips / 10.0 if self.rr_dynamic else 0.0)
                tp = float(entry - rr * (sl - entry))
                order_type = self.mt5.ORDER_TYPE_SELL_STOP
            else:
                return

            lot = float(self.risk_manager.calculate_lot_size(self.symbol, trend, entry, sl))
            if lot <= 0.0 or not self.risk_manager.check_free_margin():
                return

            # Pending GTC (fără expiration pe request) + expirare manuală
            request = {
                "action": self.mt5.TRADE_ACTION_PENDING,
                "symbol": self.symbol,
                "volume": lot,
                "type": order_type,
                "price": float(entry),
                "sl": float(sl),
                "tp": float(tp),
                "deviation": 50,
                "magic": 13931993,
                "comment": "EMA Breakout strategy",
                "type_time": self.mt5.ORDER_TIME_GTC,   # GTC
                "type_filling": self.mt5.ORDER_FILLING_RETURN,
            }

            result = self.trade_manager.safe_order_send(request, f"pending {trend} {self.symbol}")
            if result is None:
                self.logger.log(f"❌ order_send returned None → simbol inactiv sau conexiune pierdută")
            else:
                self.logger.log(f"🔍 OrderSend result: retcode={getattr(result,'retcode',None)}, "
                                f"comment={getattr(result,'comment','')}")
                self.logger.log(f"🔍 Full request: {request}")
                # dacă e pending creat, memorăm expirarea manuală
                if getattr(result, "retcode", None) == self.mt5.TRADE_RETCODE_DONE:
                    order_ticket = int(getattr(result, "order", 0))
                    if order_ticket > 0:
                        self._record_pending_expiry(order_ticket)

            # trailing pentru pozițiile deja activate (apel centralizat în TradeManager)
            self._apply_trailing(atr_price, pip)

        except Exception as e:
            trace = traceback.format_exc()
            self.logger.log(f"❌ Error in EMABreakoutStrategy {self.symbol}: {e}")
            if self.config.get("debug", False):
                self.logger.log(f"🔍 Stack trace: {trace}")
