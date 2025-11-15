# managers/risk_manager.py
import datetime
import pytz
import time
import numpy as np

class RiskManager:
    def __init__(self, config, logger, trade_manager, mt5):
        self.config = config
        self.logger = logger
        self.trade_manager = trade_manager
        self.mt5 = mt5

        # Parametri din config
        self.max_daily_loss = config.get("daily_loss", -100)
        self.risk_per_trade = config.get("risk_per_trade", 0.01)
        self.max_daily_profit = config.get("daily_profit", 500)
        self.min_free_margin_ratio = config.get("min_free_margin_ratio", 0.6)
        self.max_drawdown = config.get("max_drawdown", 0.2)
        self.friday_close_hour_utc = int(config.get("friday_close_hour_utc", 19))
        
        general_cfg = config.get("general", {})
        self.sessions = general_cfg.get("session_hours", [])
        self.atr_thresholds = general_cfg.get("atr_thresholds_pips", {})
        self.trailing_params = config.get("trailing", {})
        self.min_sl_pips = float(config.get("min_sl_pips", 5.0))
        self.exposure_limits = config.get("exposure_limits", {})

        # Logare (setat la 1h by default)
        self.can_trade_log_period_sec = int(config.get("can_trade_log_period_sec", 3600))
        self._last_can_trade_log_ts = 0.0

        # Urmărire zilnică
        self.daily_loss = 0.0
        self.last_reset_date = datetime.date.today()
        self.initial_equity = None
        self.trading_blocked_until_next_day = False
        self.rollover_closure_executed = False

    def get_today_total_profit(self) -> float:
        """Calculează PnL-ul de astăzi pe baza istoricului."""
        try:
            tz = pytz.timezone(self.config.get("general", {}).get("account_timezone", "Europe/Bucharest"))
        except Exception:
            tz = pytz.utc
            
        today = datetime.datetime.now(tz).date()
        start_time = datetime.datetime(today.year, today.month, today.day, tzinfo=tz)
        end_time = datetime.datetime.now(tz)
        
        deals = self.mt5.history_deals_get(start_time, end_time)
        return sum(d.profit for d in deals) if deals else 0.0

    def _reset_if_new_day(self):
        """Resetează contorii zilnici dacă a început o nouă zi."""
        today = datetime.date.today()
        if today != self.last_reset_date:
            self.logger.log("🔄 Reset RiskManager pentru ziua nouă")
            self.daily_loss = 0.0
            self.last_reset_date = today
            self.initial_equity = None
            self.trading_blocked_until_next_day = False
            self.rollover_closure_executed = False

    def _in_trading_session(self):
        """Verifică dacă ora curentă este în sesiunea de tranzacționare (Luni-Vineri)."""
        now = datetime.datetime.now()
        day_of_week = now.weekday()
        
        # 0=Luni, 5=Sâmbătă, 6=Duminică
        if day_of_week == 5 or day_of_week == 6:
            return False 
            
        # Lista de sesiuni este goală = tranzacționează 24/5
        if not self.sessions:
            return True

        now_time = now.time()
        is_in_session = False
        for start_str, end_str in self.sessions:
            try:
                start_time = datetime.datetime.strptime(start_str, "%H:%M").time()
                end_time = datetime.datetime.strptime(end_str, "%H:%M").time() 
                
                # Cazul 1: Sesiune normală (ex: 09:00 - 17:00)
                if start_time <= end_time:
                    if start_time <= now_time <= end_time:
                        is_in_session = True; break
                else: 
                # Cazul 2: Sesiune peste noapte (ex: 22:00 - 05:00)
                    if now_time >= start_time or now_time <= end_time:
                        is_in_session = True; break
            except Exception as e:
                self.logger.log(f"❌ [Session Check] Eroare la parsarea orelor sesiunii: {e}", "error")
                continue
        return is_in_session

    def can_trade(self, verbose=False):
        """Verificarea principală apelată de BotManager și Strategii."""
        self._reset_if_new_day()

        if self.rollover_closure_executed: return False
        if self.trading_blocked_until_next_day: return False

        info = self.mt5.get_account_info()
        if not info: 
            if verbose: self.logger.log("❌ [can_trade] Nu am putut obține account_info -> False", "warning")
            return False

        equity = float(info.equity)
        free_margin = float(info.margin_free)
        pnl_today = self.get_today_total_profit()

        # Dezactivăm limitele dacă sunt setate la valori nerealiste (ex: 999999)
        cond_loss = pnl_today >= self.max_daily_loss if self.max_daily_loss > -9999 else True
        cond_profit = pnl_today <= self.max_daily_profit if self.max_daily_profit < 9999 else True
        
        cond_margin = (equity == 0) or (free_margin / equity) >= self.min_free_margin_ratio
        cond_session = self._in_trading_session()
        
        if self.initial_equity is None: self.initial_equity = equity
        cond_drawdown = equity >= self.initial_equity * (1 - self.max_drawdown)

        result = cond_loss and cond_profit and cond_margin and cond_session and cond_drawdown

        now_ts = time.time()
        if verbose and (now_ts - self._last_can_trade_log_ts >= self.can_trade_log_period_sec):
            self.logger.log(
                f"🔍 [can_trade Check] "
                f"SesiuneOK: {cond_session}, "
                f"PierdereOK: {cond_loss} (P/L: {pnl_today:.2f}), "
                f"ProfitOK: {cond_profit}, "
                f"MarjăOK: {cond_margin}, "
                f"DrawdownOK: {cond_drawdown} "
                f"=> Rezultat: {result}"
            )
            self._last_can_trade_log_ts = now_ts

        return result
    
    def get_trailing_params(self): 
        return self.trailing_params

    def _normalize_timeframe(self, tf): 
        return str(tf).upper()

    def get_atr_threshold(self, symbol: str, timeframe: str = None) -> float:
        """Funcția reală de citire a pragului ATR (dacă e folosită de vreo strategie)."""
        default_val = 5.0
        sym_map = self.atr_thresholds.get(symbol)
        if sym_map is None: return default_val
        if isinstance(sym_map, (int, float)): return float(sym_map)
        if isinstance(sym_map, dict):
            tf = self._normalize_timeframe(timeframe)
            if tf and tf in sym_map:
                return float(sym_map[tf])
            for k in ("M1", "M5", "H1"):
                if k in sym_map: return float(sym_map[k])
        return default_val

    def _round_lot_to_step(self, sym_info, volume: float) -> float:
        """Rotunjește volumul la cel mai apropiat pas permis de broker."""
        step = float(getattr(sym_info, "volume_step", 0.01) or 0.01)
        vmin = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
        vmax = float(getattr(sym_info, "volume_max", 100.0) or 100.0)
        v = max(min(float(volume), vmax), vmin)
        if step > 0:
            v = round(round(v / step) * step, 8)
        return v
    
    def calculate_lot_size(self, symbol, direction, entry_price, stop_loss) -> float:
        """
        Funcția reală de calcul al lotului, JPY-safe, bazată pe % risc.
        """
        account_info = self.mt5.get_account_info()
        if not account_info:
            self.logger.log("⚠️ Nu s-a putut obține account_info pentru lot size.", "warning")
            return 0.0
        
        equity = float(account_info.equity)
        if self.initial_equity is None:
            self.initial_equity = equity
        
        risk_amount = equity * float(self.risk_per_trade)
        
        sym_info = self.mt5.get_symbol_info(symbol)
        if not sym_info:
            self.logger.log(f"⚠️ Nu s-a putut obține symbol_info pentru {symbol}", "warning")
            return 0.0
        
        point = float(getattr(sym_info, "point", 0.0) or 0.0)
        tick_value = float(getattr(sym_info, "trade_tick_value", 0.0) or 0.0)
        digits = int(getattr(sym_info, "digits", 5))
        
        if point <= 0 or tick_value <= 0:
            self.logger.log(f"⚠️ point/tick_value invalid pentru {symbol} (point={point}, tick_value={tick_value})", "warning")
            return 0.0

        sl_distance_points = abs(float(entry_price) - float(stop_loss))
        
        # Protecție pentru SL prea mic
        min_sl_distance = self.min_sl_pips * self.mt5.get_pip_size(symbol)
        if sl_distance_points < min_sl_distance:
            self.logger.log(f"⚠️ SL prea mic ({sl_distance_points}), se folosește minimul {min_sl_distance} puncte.", "warning")
            sl_distance_points = min_sl_distance

        # Câți "ticks" sunt într-o unitate de preț (1.0)
        ticks_per_unit = 1.0 / point
        # Valoarea unui lot standard (în valuta contului) la o mișcare de 1.0
        value_per_lot_per_unit = tick_value * ticks_per_unit
        
        # Riscul (în valuta contului) pentru 1 lot
        risk_per_1_lot = sl_distance_points * value_per_lot_per_unit
        
        if risk_per_1_lot <= 0:
            self.logger.log(f"⚠️ Calculul riscului per lot este <= 0 pentru {symbol}", "warning")
            return 0.0
        
        raw_lot = risk_amount / risk_per_1_lot
        
        vol_min = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
        vol_max = float(getattr(sym_info, "volume_max", 100.0) or 100.0)
        cfg_max = float(self.config.get("max_position_lot", vol_max))
        
        clamped = max(min(raw_lot, vol_max, cfg_max), vol_min)
        lot = self._round_lot_to_step(sym_info, clamped)
        
        return lot
        
    def check_free_margin(self, symbol: str = None, lot: float = None, order_type: int = None) -> bool:
        """Verificarea reală a marjei libere."""
        if not self.mt5: return False
        info = self.mt5.get_account_info()
        if not info: return False

        equity = float(info.equity)
        free_margin = float(info.margin_free)
        
        ratio = free_margin / equity if equity > 0 else 0
        ok = ratio >= float(self.min_free_margin_ratio)
        if not ok:
            # Acest log va apărea doar o dată pe oră (prin 'verbose=True' din BotManager)
            self.logger.log(f"❌ [check_free_margin] Free margin ratio={ratio:.2%} < min {self.min_free_margin_ratio:.2%}")
        return ok
        
    def check_drawdown_breach(self) -> bool:
        """Verificarea reală a drawdown-ului maxim al contului."""
        info = self.mt5.get_account_info()
        if not info: return False
        equity = float(info.equity)
        if self.initial_equity is None: self.initial_equity = equity; return False
        if self.max_drawdown is None: return False
        
        limit = self.initial_equity * (1 - float(self.max_drawdown))
        if equity < limit:
            self.logger.log(f"⚠️ [RiskManager] Drawdown limit depășit! Equity={equity:.2f} < {limit:.2f}", "error")
            return True
        return False
        
    def check_strategy_exposure(self, strategy: str, symbol: str) -> bool:
        """Verificarea reală a expunerii."""
        limits = self.exposure_limits.get(strategy, {}).get(symbol, {})
        max_positions = int(limits.get("max_positions", 0))
        if max_positions <= 0:
            return True  # fără limită
    
        positions = self.mt5.positions_get(symbol=symbol) or []
        pos_count = sum(1 for p in positions if getattr(p, "comment", "").startswith(strategy))
    
        orders = self.mt5.orders_get(symbol=symbol) or []
        ord_count = sum(1 for o in orders if getattr(o, "comment", "").startswith(strategy))
    
        total = pos_count + ord_count
        if total >= max_positions:
            self.logger.log(f"❌ Exposure limit: {strategy} {symbol} already has {total}/{max_positions} positions+orders")
            return False
        return True
        
    def check_for_rollover_closure(self):
        """Verificarea reală pentru închiderea de vineri."""
        now_utc = datetime.datetime.now(pytz.utc)
        if self.rollover_closure_executed: return True
        if now_utc.weekday() == 4 and now_utc.hour >= self.friday_close_hour_utc:
            self.logger.log("🛑 WEEKEND CLOSURE ACTIVATED: Returning True for BotManager.")
            self.rollover_closure_executed = True
            return True
        return False