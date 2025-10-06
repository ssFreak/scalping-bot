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
        self.rr_target = float(config.get("rr_target", 1.5))
        self.cooldown_minutes = int(config.get("cooldown_minutes", 0))
        
        # 🟢 FIX: Inițializarea atributelor noi (cauza erorii)
        self.min_bar_length_atr = float(config.get("min_bar_length_atr", 0.8))
        self.ema_alignment_lookback = int(config.get("ema_alignment_lookback", 3))

        # New bar gating
        self.last_bar_time = None
        self.last_trade_time = None 

        # Expirare manuală: {order_ticket: datetime_expiry_utc}
        self._pending_expirations = {}
        
        # Reîncarcă ordinele pending existente din sesiunea precedentă
        self._load_pending_orders_on_start()
    
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
    def _record_pending_expiry(self, order_ticket: int, start_time: datetime = None) -> None:
        """Înregistrează un ordin pending pentru expirare manuală."""
        start = start_time if start_time is not None else datetime.now(timezone.utc)
        expiry_limit = start + timedelta(minutes=self.order_expiry_minutes)
        self._pending_expirations[order_ticket] = expiry_limit
        self.logger.log(f"🕒 Pending ticket={order_ticket} va expira manual la {expiry_limit.isoformat()}")
        
    def _delete_pending_order(self, order_ticket: int) -> bool:
        """
        Șterge un ordin pending. Returnează True dacă ordinul a fost anulat SAU dacă nu a fost găsit.
        Erorile de tip 'Invalid Ticket' (10017) sunt tratate ca succes, indicând o curățare reușită.
        """
        # Folosim coduri direct din TradeManager sau MT5Connector, dar le definim aici ca fallback
        try:
            mt5_done = self.mt5.TRADE_RETCODE_DONE
            mt5_invalid_ticket = self.mt5.TRADE_RETCODE_INVALID_TICKET
        except AttributeError:
            # Fallback - Coduri numerice MetaTrader standard
            mt5_done = 10009 
            mt5_invalid_ticket = 10017 
            
        request = {
            "action": self.mt5.TRADE_ACTION_REMOVE,
            "order": order_ticket,
            "comment": "EMA Breakout manual expiry",
            "deviation": self.trade_manager.trade_deviation,
            "magic": 13931993,
        }

        result = self.mt5.order_send(request)
        retcode = getattr(result, "retcode", -1)

        if retcode == mt5_done:
            self.logger.log(f"✅ Pending Order {order_ticket} anulat cu succes.")
            return True
        elif retcode == mt5_invalid_ticket:
            # Ordinul nu mai există la broker (a fost executat sau anulat). 
            # Îl tratăm ca succes pentru curățarea stării locale.
            self.logger.log(f"⚠️ Pending Order {order_ticket} nu a putut fi anulat (Invalid Ticket / Executat). Curățare reușită.")
            return True 
        else:
            self.logger.log(f"❌ Eșec anulare Pending Order {order_ticket}. Retcode: {retcode}, Comentariu: {getattr(result, 'comment', '')}")
            return False

    def _check_and_purge_expired_pending(self) -> None:
        """Șterge ordinele pending expirate și curăță cele executate/dispărute."""
        if not self._pending_expirations:
            return

        # 🛑 FILTRU CRITIC: Asigură-te că simbolul este valid înainte de a interoga MT5
        if not self.symbol or self.symbol.upper() == 'NONE':
             self.logger.log(f"❌ [Curățare] Simbol invalid ({self.symbol}). Sărit peste verificare.")
             return

        now_utc = datetime.now(timezone.utc)
        # Ordine active pentru simbol
        open_orders = self.mt5.orders_get(symbol=self.symbol) or []
        open_tickets = {int(getattr(o, "ticket", 0)) for o in open_orders}

        tickets_to_remove = []
        for ticket, exp_time in list(self._pending_expirations.items()):
            # 1. Dacă ordinul nu mai e în lista de Pending a brokerului (a fost executat sau anulat manual)
            if ticket not in open_tickets:
                self.logger.log(f"ℹ️ Pending Order {ticket} nu mai este activ la broker. Curățare locală.")
                tickets_to_remove.append(ticket)
                continue
                
            # 2. Dacă a expirat manual
            # Folosim diferența de timp simplă (cum e probabil deja implementat)
            if (now_utc - exp_time).total_seconds() / 60 >= self.order_expiry_minutes:
                # Încercăm să ștergem ordinul pending
                if self._delete_pending_order(ticket):
                    tickets_to_remove.append(ticket)
                # Notă: Dacă _delete_pending_order returnează False (eroare gravă), 
                # tichetul rămâne în listă pentru a încerca din nou mai târziu.

        for t in tickets_to_remove:
            self._pending_expirations.pop(t, None)
            
    def _load_pending_orders_on_start(self) -> None:
        """
        Încercă să recupereze ordinele pending plasate anterior, 
        populând self._pending_expirations pentru gestionare.
        """
        orders = self.mt5.orders_get(symbol=self.symbol) or []
        strategy_name_prefix = self.__class__.__name__
        now_utc = datetime.now(timezone.utc)
        
        # Filtrează ordinele pending după prefixul strategiei (ex: 'EMABreakoutStrategy')
        strategy_orders = [
            o for o in orders 
            if getattr(o, "comment", "").startswith(strategy_name_prefix)
        ]

        if not strategy_orders:
            return

        self.logger.log(f"🔄 [Restart] {self.symbol}: Am găsit {len(strategy_orders)} ordine pending active din sesiunea precedentă.")

        # Reînregistrează fiecare ordin pending cu un nou timp de expirare
        for order in strategy_orders:
            ticket = int(getattr(order, "ticket", 0))
            if ticket > 0 and ticket not in self._pending_expirations:
                # Setează o nouă oră de expirare manuală începând cu momentul restartului.
                self._record_pending_expiry(ticket, start_time=now_utc)
            
    # ========================
    # Main loop
    # ========================
    def run_once(self):
        try:
            strategy_name_prefix = self.__class__.__name__

            # 1. Housekeeping & Risk Checks
            self._check_and_purge_expired_pending()

            trend = self._check_trend_h1()
            if trend == "FLAT":
                pip = float(self.mt5.get_pip_size(self.symbol))
                self._apply_trailing(0.0, pip) 
                return

            # Cooldown check
            if self.cooldown_minutes > 0 and self.last_trade_time:
                if datetime.now() < self.last_trade_time + timedelta(minutes=self.cooldown_minutes):
                    return

            # Filtru Safe Exit (Previne plasarea de ordine în perioada de risc a serii)
            now_time = datetime.now().time()
            safe_exit_start = datetime.strptime("22:30", "%H:%M").time() 
            safe_exit_end = datetime.strptime("23:59", "%H:%M").time()

            if now_time >= safe_exit_start and now_time <= safe_exit_end:
                self.logger.log(f"⏸️ [Safe Exit] {self.symbol}: Oprire plasarea de noi ordine pending.")
                pip = float(self.mt5.get_pip_size(self.symbol))
                self._apply_trailing(0.0, pip)
                return

            # --- PRELUARE DATE ȘI CALCUL INDICATORI ---
            
            rates = self.mt5.get_rates(self.symbol, self.timeframe_m5, 100)
            if rates is None or len(rates) < 50:
                return

            df = pd.DataFrame(rates)
            if df.empty:
                return
            
            # 🟢 NOU: FILTRU NEW BAR GATING (M5)
            # Verifică ora de deschidere a ultimei lumânări disponibile
            current_bar_time = df["time"].iloc[-1] 
            
            if current_bar_time == self.last_bar_time:
                # Nu s-a format o nouă bară M5 de la ultima verificare. Sărim.
                pip = float(self.mt5.get_pip_size(self.symbol))
                self._apply_trailing(0.0, pip) # Rulează trailing pe pozițiile deschise
                return
                
            # Dacă am ajuns aici, o nouă bară M5 s-a deschis. Actualizăm timpul și continuăm.
            self.last_bar_time = current_bar_time
            #self.logger.log(f"✅ {self.symbol}: New Bar Gating. Rulare logică pe bara M5 deschisă la {current_bar_time}.")


            # Calcul ATR
            df = calculate_atr(df, 14)
            atr_price = float(df["atr"].iloc[-1])
            pip = float(self.mt5.get_pip_size(self.symbol))
            atr_pips = float(atr_price / pip)

            # Calcul EMA M5 pentru aliniere
            df["ema8"] = df["close"].ewm(span=8).mean()
            df["ema13"] = df["close"].ewm(span=13).mean()
            df["ema21"] = df["close"].ewm(span=21).mean()

            # threshold scalar safe
            try:
                atr_threshold = float(self.risk_manager.get_atr_threshold(self.symbol))
            except TypeError:
                atr_threshold = float(self.risk_manager.get_atr_threshold(self.symbol, self.timeframe_m5))
            except Exception:
                atr_threshold = self.min_atr_pips

            if atr_pips < atr_threshold:
                return

            # --- 🛑 FILTRE DE CALITATE A SEMNALULUI 🛑 ---
            
            # 1. Filtru de Lungime a Barei (Volatilitate/Impuls)
            current_bar_length = df["high"].iloc[-1] - df["low"].iloc[-1]
            if current_bar_length < self.min_bar_length_atr * atr_price:
                self.logger.log(f"⚠️ {self.symbol}: Filtru Lungime Bară. Scurt ({current_bar_length:.5f} < {self.min_bar_length_atr * atr_price:.5f}).")
                pip = float(self.mt5.get_pip_size(self.symbol))
                self._apply_trailing(0.0, pip)
                return 
            
            # 2. Filtru de Aliniere Recentă a EMA (Confluență M5)
            lookback = self.ema_alignment_lookback
            
            if len(df) < lookback:
                lookback = 1
                
            is_aligned_up = all(df["ema8"].iloc[i] > df["ema13"].iloc[i] > df["ema21"].iloc[i] 
                                for i in range(-lookback, 0))
            is_aligned_down = all(df["ema8"].iloc[i] < df["ema13"].iloc[i] < df["ema21"].iloc[i] 
                                  for i in range(-lookback, 0))

            if trend == "UP" and not is_aligned_up:
                self.logger.log(f"⚠️ {self.symbol}: Filtru Aliniere. Trend H1 UP, dar EMA M5 nu e aliniat recent (UP).")
                pip = float(self.mt5.get_pip_size(self.symbol))
                self._apply_trailing(0.0, pip)
                return
            if trend == "DOWN" and not is_aligned_down:
                self.logger.log(f"⚠️ {self.symbol}: Filtru Aliniere. Trend H1 DOWN, dar EMA M5 nu e aliniat recent (DOWN).")
                pip = float(self.mt5.get_pip_size(self.symbol))
                self._apply_trailing(0.0, pip)
                return
            
            # --- 🛑 FIX LIMITĂ DE EXPUNERE 🛑 ---
            
            # Verificare strictă: dacă există deja un ordin pending al acestei strategii.
            current_orders = self.mt5.orders_get(symbol=self.symbol) or []
            
            if any(getattr(o, "comment", "").startswith(strategy_name_prefix) for o in current_orders):
                self.logger.log(f"⚠️ {self.symbol}: Există deja un ordin pending pentru {strategy_name_prefix}. Săritura la trailing.")
                pip = float(self.mt5.get_pip_size(self.symbol))
                self._apply_trailing(0.0, pip)
                return
            
            # Verificare limită de expunere (max_positions=2)
            if not self.risk_manager.check_strategy_exposure("ema_breakout", self.symbol):
                return  

            # --- LOGICĂ PLASARE ---
            
            high5 = float(df["high"].iloc[-5:].max())
            low5 = float(df["low"].iloc[-5:].min())

            rr_factor = self.rr_target # 2.0

            if trend == "UP":
                entry = float(high5 + self.offset_pips * pip)
                sl = float(low5)
                sl_distance_points = entry - sl 
                tp = entry + rr_factor * sl_distance_points
                order_type = self.mt5.ORDER_TYPE_BUY_STOP
            elif trend == "DOWN":
                entry = float(low5 - self.offset_pips * pip)
                sl = float(high5)
                sl_distance_points = sl - entry
                tp = entry - rr_factor * sl_distance_points
                order_type = self.mt5.ORDER_TYPE_SELL_STOP
            else:
                return

            lot = float(self.risk_manager.calculate_lot_size(self.symbol, trend, entry, sl))
            if lot <= 0.0 or not self.risk_manager.check_free_margin():
                return

            # Plasare ordin pending
            request = {
                "action": self.mt5.TRADE_ACTION_PENDING,
                "symbol": self.symbol,
                "volume": lot,
                "type": order_type,
                "price": float(entry),
                "sl": float(sl),
                "tp": float(tp),
                "deviation": self.trade_manager.trade_deviation,
                "magic": 13931993,
                "comment": strategy_name_prefix,
                "type_time": self.mt5.ORDER_TIME_GTC,
                "type_filling": self.mt5.ORDER_FILLING_RETURN,
            }

            result = self.trade_manager.safe_order_send(request, f"pending {trend} {self.symbol}")
            if result is None:
                self.logger.log(f"❌ order_send returned None → simbol inactiv sau conexiune pierdută")
            else:
                self.logger.log(f"🔍 OrderSend result: retcode={getattr(result,'retcode',None)}, "
                                f"comment={getattr(result,'comment','')}")
                
                if getattr(result, "retcode", None) == self.mt5.TRADE_RETCODE_DONE:
                    self.last_trade_time = datetime.now()
                    order_ticket = int(getattr(result, "order", 0))
                    if order_ticket > 0:
                        self._record_pending_expiry(order_ticket)

            # trailing pentru pozițiile deja activate
            self._apply_trailing(atr_price, pip)

        except Exception as e:
            trace = traceback.format_exc()
            self.logger.log(f"❌ Error in EMABreakoutStrategy {self.symbol}: {e}")
            if self.config.get("debug", False):
                self.logger.log(f"🔍 Stack trace: {trace}")