import datetime
import pytz
import time


class RiskManager:
    def __init__(self, config, logger, trade_manager, mt5):
        self.config = config
        self.logger = logger
        self.trade_manager = trade_manager
        self.mt5 = mt5

        # parametri din YAML (istoric - citite direct din rădăcina config)
        self.max_daily_loss = config.get("daily_loss", -100)
        self.risk_per_trade = config.get("risk_per_trade", 0.01)
        self.max_equity_risk = config.get("max_equity_risk", 0.1)
        self.max_daily_profit = config.get("daily_profit", 500)
        self.min_free_margin_ratio = config.get("min_free_margin_ratio", 0.6)
        self.max_drawdown = config.get("max_drawdown", 0.2)

        # sesiuni de trading (din general.session_hours)
        general_cfg = config.get("general", {})
        self.sessions = general_cfg.get("session_hours", [])
        self.min_atr_pips = float(config.get("min_atr_pips", 5))
        self._last_can_trade_log_ts = 0.0

        # praguri ATR pe simbol; pot fi fie număr, fie dict pe TF (M1/M5/H1)
        # ex: { "EURUSD": { "M1": 2.0, "M5": 5.0, "H1": 17.0 }, ... }
        self.atr_thresholds = general_cfg.get("atr_thresholds_pips", {})

        # trailing (dacă există în config)
        trailing_cfg = config.get("trailing", {})
        self.trailing_params = {
            "be_min_profit_pips": float(trailing_cfg.get("be_min_profit_pips", 10)),
            "step_pips": float(trailing_cfg.get("step_pips", 5)),
            "atr_multiplier": float(trailing_cfg.get("atr_multiplier", 1.5)),
        }

        # protecție: SL minim
        self.min_sl_pips = float(config.get("min_sl_pips", 5.0))

        # logging throttle
        self.can_trade_log_period_sec = int(config.get("can_trade_log_period_sec", 300))
        self._last_can_trade_log_ts = 0.0

        # daily tracking
        self.daily_loss = 0.0
        self.last_reset_date = datetime.date.today()
        self.initial_equity = None
        
        self.trading_blocked_until_next_day = False

    # ------------------
    # Profit / Loss tracking
    # ------------------
    def get_today_total_profit(self) -> float:
        """Calculează PnL-ul de astăzi pe baza istoricului."""
        tz = pytz.timezone("Europe/Bucharest")
        today = datetime.datetime.now(tz).date()
        deals = self.mt5.history_deals_get(
            datetime.datetime(today.year, today.month, today.day, tzinfo=tz),
            datetime.datetime.now(tz),
        )
        if deals is None:
            return 0.0

        pnl = 0.0
        for d in deals:
            pnl += d.profit
        return pnl

    def _reset_if_new_day(self):
        today = datetime.date.today()
        if today != self.last_reset_date:
            self.logger.log("🔄 Reset RiskManager pentru ziua nouă")
            self.daily_loss = 0.0
            self.last_reset_date = today
            self.initial_equity = None
            self.trading_blocked_until_next_day = False  # ✅ deblocăm tradingul

    # ------------------
    # Trading Sessions
    # ------------------
    def _in_trading_session(self):
        if not self.sessions:
            return True

        now = datetime.datetime.now().time()
        #self.logger.log(f"⏰ [Session Check] Ora curentă: {now.strftime('%H:%M:%S')}") # Adaugă logarea orei
        
        is_in_session = False
        for pair in self.sessions:
            try:
                start = datetime.datetime.strptime(pair[0], "%H:%M").time()
                end = datetime.datetime.strptime(pair[1], "%H:%M").time() 
                
                #self.logger.log(f"⏰ [Session Check] Verifică sesiunea: {pair[0]} - {pair[1]}") # Adaugă logarea intervalului verificat
                
                if start <= now <= end:
                    is_in_session = True
                    break
            except Exception as e:
                self.logger.log(f"❌ [Session Check] Eroare la parsing oră: {e}")
                continue
                
        #self.logger.log(f"⏰ [Session Check] Rezultat final: {is_in_session}")
        return is_in_session

    def can_trade(self, verbose=False):
        """Returnează True dacă botul poate deschide noi tranzacții."""
        if hasattr(self, "_reset_if_new_day"):
            try:
                self._reset_if_new_day()
            except Exception:
                pass
                
        # dacă tradingul e blocat din cauza drawdown-ului
        if self.trading_blocked_until_next_day:
            if verbose:
                self.logger.log("⏸️ Trading blocat până mâine din cauza drawdown-ului.")
            return False

        info = self.mt5.get_account_info()
        if not info:
            now_ts = time.time()
            last_ts = getattr(self, "_last_can_trade_log_ts", 0.0)
            if verbose and (now_ts - last_ts) >= getattr(self, "can_trade_log_period_sec", 300):
                self.logger.log("❌ [can_trade] Nu am putut obține account_info -> False")
                self._last_can_trade_log_ts = now_ts
            return False

        equity = float(info.equity)
        free_margin = float(info.margin_free)

        if hasattr(self, "get_today_total_profit"):
            pnl_today = self.get_today_total_profit()
        else:
            pnl_today = 0.0

        cond_loss   = pnl_today >= float(getattr(self, "max_daily_loss", -100))
        cond_profit = pnl_today <= float(getattr(self, "max_daily_profit", 500))
        cond_margin = (equity > 0 and (free_margin / equity) >= float(getattr(self, "min_free_margin_ratio", 0.6)))

        cond_session = True
        try:
            cond_session = self._in_trading_session()
        except Exception:
            cond_session = True

        cond_drawdown = True
        if hasattr(self, "initial_equity") and hasattr(self, "max_drawdown") and self.max_drawdown is not None:
            try:
                cond_drawdown = equity >= self.initial_equity * (1 - float(self.max_drawdown))
            except Exception:
                cond_drawdown = True

        result = cond_loss and cond_profit and cond_margin and cond_session and cond_drawdown

        now_ts = time.time()
        last_ts = getattr(self, "_last_can_trade_log_ts", 0.0)
        if verbose and (now_ts - last_ts) >= getattr(self, "can_trade_log_period_sec", 300):
            self.logger.log(
                f"🔍 [can_trade] equity={equity:.2f}, free_margin={free_margin:.2f}, "
                f"PnL(today)={pnl_today:.2f}, session={cond_session} => {result}"
            )
            self._last_can_trade_log_ts = now_ts

        return result

    def get_trailing_params(self):
        return self.trailing_params

    # -------------------------
    # ATR thresholds (per symbol + timeframe)
    # -------------------------
    def _normalize_timeframe(self, tf) -> str:
        """Normalizează reprezentări de timeframe la 'M1'/'M5'/'H1'."""
        if tf is None:
            return None
        s = str(tf).upper()
        # Acceptă și valori gen '1', '5', '60' din unele configurări
        if s in ("M1", "1"): return "M1"
        if s in ("M5", "5"): return "M5"
        if s in ("H1", "60", "1H"): return "H1"
        return s  # dacă vine altceva, returnăm ca atare

    def get_atr_threshold(self, symbol: str, timeframe: str = None) -> float:
        """
        Returnează pragul ATR (în pips) pentru simbolul dat.
        - Suportă atât valori simple (float) pe simbol, cât și dict pe TF (M1/M5/H1).
        - Dacă timeframe nu e dat și există dict, folosește prioritar M1 > M5 > H1 (fallback compatibil).
        - Dacă simbolul lipsește din config, folosește 5.0 pips ca default.
        """
        default_val = 5.0
        sym_map = self.atr_thresholds.get(symbol)

        # Nici o intrare pentru simbol -> fallback
        if sym_map is None:
            return default_val

        # Dacă e număr simplu (compatibil cu vechiul config)
        if isinstance(sym_map, (int, float)):
            try:
                return float(sym_map)
            except Exception:
                return default_val

        # Dacă e dict pe timeframe
        if isinstance(sym_map, dict):
            tf = self._normalize_timeframe(timeframe)
            if tf and tf in sym_map:
                try:
                    return float(sym_map[tf])
                except Exception:
                    return default_val

            # fără timeframe explicit: prioritate M1 -> M5 -> H1 (compatibil & conservator)
            for k in ("M1", "M5", "H1"):
                if k in sym_map:
                    try:
                        return float(sym_map[k])
                    except Exception:
                        continue

        # alt format neașteptat -> fallback
        return default_val

    # === Lot sizing (compat cu strategii) ===
    def _round_lot_to_step(self, sym_info, volume: float) -> float:
        """
        Rotunjește volumul la step-ul permis de simbol și îl limitează în [volume_min, volume_max].
        """
        step = float(getattr(sym_info, "volume_step", 0.01) or 0.01)
        vmin = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
        vmax = float(getattr(sym_info, "volume_max", 100.0) or 100.0)
        v = max(min(float(volume), vmax), vmin)
        if step > 0:
            # rotunjire pe multipli de step (păstrăm destule zecimale pentru metale/CFD-uri)
            v = round(round(v / step) * step, 8)
        return v
    
    def calculate_lot_size(self, symbol, direction, entry_price, stop_loss) -> float:
        """
        Calculează lotul astfel încât riscul = equity * risk_per_trade.
        - direction: ignorat (e acceptat pentru compatibilitate: 'UP'/'DOWN'/'BUY'/'SELL' sau coduri MT5)
        - entry_price, stop_loss: prețuri absolute
        """
        account_info = self.mt5.get_account_info()
        if not account_info:
            self.logger.log("⚠️ Nu s-a putut obține account_info pentru lot size.")
            return 0.0
    
        equity = float(account_info.equity)
        if self.initial_equity is None:
            self.initial_equity = equity
    
        risk_amount = equity * float(self.risk_per_trade)
    
        sym_info = self.mt5.get_symbol_info(symbol)
        if not sym_info:
            self.logger.log(f"⚠️ Nu s-a putut obține symbol_info pentru {symbol}")
            return 0.0
    
        pip = float(self.mt5.get_pip_size(symbol))
        point = float(getattr(sym_info, "point", 0.0) or 0.0)
        tick_value = float(getattr(sym_info, "trade_tick_value", 0.0) or 0.0)
    
        if pip <= 0 or point <= 0 or tick_value <= 0:
            self.logger.log(f"⚠️ pip/point/tick_value invalid pentru {symbol} (pip={pip}, point={point}, tick_value={tick_value})")
            return 0.0
    
        # Valoarea unui pip pentru 1 lot (în valuta contului), pe baza tick_value
        pip_value_per_lot = tick_value * (pip / point)
    
        # Distanța până la SL în pips (absolut)
        sl_distance = abs(float(entry_price) - float(stop_loss))
        sl_pips = sl_distance / pip
        if sl_pips < float(getattr(self, "min_sl_pips", 5.0)):
            self.logger.log(f"⚠️ SL prea mic ({sl_pips:.2f} pips), aplic minim {self.min_sl_pips} pips")
            sl_pips = float(self.min_sl_pips)
    
        risk_per_1_lot = sl_pips * pip_value_per_lot
        if risk_per_1_lot <= 0:
            return 0.0
    
        raw_lot = risk_amount / risk_per_1_lot
    
        # Respectă limitele brokerului + eventualul plafon din config
        vol_min = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
        vol_max = float(getattr(sym_info, "volume_max", 100.0) or 100.0)
        cfg_max = float(self.config.get("max_position_lot", vol_max))
    
        clamped = max(min(raw_lot, vol_max, cfg_max), vol_min)
        lot = self._round_lot_to_step(sym_info, clamped)
    
        self.logger.log(
            f"🧮 LotCalc {symbol}: equity={equity:.2f}, risk%={self.risk_per_trade:.3f}, "
            f"risk_amount={risk_amount:.2f}, sl_pips={sl_pips:.2f}, pip_value/lot={pip_value_per_lot:.4f} "
            f"-> raw={raw_lot:.3f} => lot={lot:.2f}"
        )
        return lot
        
    def check_free_margin(self, symbol: str = None, lot: float = None, order_type: int = None) -> bool:
        """
        Verifică dacă există suficientă marjă liberă.
        - Dacă se dau symbol/lot/order_type -> verifică ordinul propus.
        - Dacă NU se dau parametri -> verifică doar că free_margin/equity >= min_free_margin_ratio.
        """
        if not self.mt5:
            self.logger.log("❌ [check_free_margin] MT5 connector is None")
            return False

        info = self.mt5.get_account_info()
        if not info:
            self.logger.log("❌ [check_free_margin] Nu am putut obține account_info")
            return False

        equity = float(info.equity)
        free_margin = float(info.margin_free)

        # Caz simplu: apel fără parametri
        if symbol is None or lot is None or order_type is None:
            ratio = free_margin / equity if equity > 0 else 0
            ok = ratio >= float(self.min_free_margin_ratio)
            if not ok:
                self.logger.log(
                    f"❌ [check_free_margin] Free margin ratio={ratio:.2%} < min {self.min_free_margin_ratio:.2%}"
                )
            return ok

        # Caz avansat: verifică un ordin propus
        tick = self.mt5.get_symbol_tick(symbol)
        if not tick:
            self.logger.log(f"❌ [check_free_margin] Nu am tick pentru {symbol}")
            return False

        price = tick.ask if order_type == self.mt5.ORDER_TYPE_BUY else tick.bid
        margin_req = self.mt5.order_calc_margin(order_type, symbol, lot, price)
        if margin_req is None:
            self.logger.log(f"⚠️ [check_free_margin] Nu am putut calcula margin_req pentru {symbol}, lot={lot}")
            return False

        margin_after = free_margin - margin_req
        ratio_after = margin_after / equity if equity > 0 else 0

        ok = ratio_after >= float(self.min_free_margin_ratio)
        if not ok:
            self.logger.log(
                f"❌ [check_free_margin] {symbol} lot={lot}: equity={equity:.2f}, "
                f"free_margin={free_margin:.2f}, margin_req={margin_req:.2f}, "
                f"ratio_after={ratio_after:.2%} < min {self.min_free_margin_ratio:.2%}"
            )
        return ok
        
    def check_drawdown_breach(self) -> bool:
        """
        Verifică dacă equity a depășit drawdown-ul maxim admis.
        Dacă da -> returnează True și loghează un avertisment.
        """
        info = self.mt5.get_account_info()
        if not info:
            return False

        equity = float(info.equity)
        if self.initial_equity is None:
            # La prima rulare salvăm equity-ul inițial
            self.initial_equity = equity
            return False

        if self.max_drawdown is None:
            return False

        limit = self.initial_equity * (1 - float(self.max_drawdown))
        if equity < limit:
            self.logger.log(
                f"⚠️ [RiskManager] Drawdown limit depășit! Equity={equity:.2f} < {limit:.2f} (max_drawdown={self.max_drawdown:.0%})"
            )
            return True

        return False
        
    def check_strategy_exposure(self, strategy: str, symbol: str) -> bool:
        """Verifică dacă strategia mai are voie să deschidă poziții/ordine pe simbol."""
        limits = self.config.get("exposure_limits", {}).get(strategy, {}).get(symbol, {})
        max_positions = int(limits.get("max_positions", 0))
        if max_positions <= 0:
            return True  # fără limită
    
        # Poziții deschise
        positions = self.mt5.positions_get(symbol=symbol) or []
        pos_count = sum(1 for p in positions if getattr(p, "comment", "").startswith(strategy))
    
        # Ordine pending
        orders = self.mt5.orders_get(symbol=symbol) or []
        ord_count = sum(1 for o in orders if getattr(o, "comment", "").startswith(strategy))
    
        total = pos_count + ord_count
        if total >= max_positions:
            self.logger.log(f"❌ Exposure limit: {strategy} {symbol} already has {total}/{max_positions} positions+orders")
            return False
        return True
