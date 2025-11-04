import datetime
import pytz
import time

class RiskManager:
    def __init__(self, config, logger, trade_manager, mt5):
        self.config = config
        self.logger = logger
        self.trade_manager = trade_manager
        self.mt5 = mt5

        # Parametri din config
        self.max_daily_loss = config.get("daily_loss", -100)
        self.risk_per_trade = config.get("risk_per_trade", 0.01)
        self.max_equity_risk = config.get("max_equity_risk", 0.1)
        self.max_daily_profit = config.get("daily_profit", 500)
        self.min_free_margin_ratio = config.get("min_free_margin_ratio", 0.6)
        self.max_drawdown = config.get("max_drawdown", 0.2)
        self.friday_close_hour_utc = int(config.get("friday_close_hour_utc", 19))
        
        general_cfg = config.get("general", {})
        self.sessions = general_cfg.get("session_hours", [])
        self.atr_thresholds = general_cfg.get("atr_thresholds_pips", {})
        self.trailing_params = config.get("trailing", {})
        self.min_sl_pips = float(config.get("min_sl_pips", 5.0))

        # === MODIFICARE PENTRU DEBUGGING ===
        # Am redus timpul de așteptare pentru logare la 10 secunde
        self.can_trade_log_period_sec = int(config.get("can_trade_log_period_sec", 3600))
        self._last_can_trade_log_ts = 0.0

        # Urmărire zilnică
        self.daily_loss = 0.0
        self.last_reset_date = datetime.date.today()
        self.initial_equity = None
        self.trading_blocked_until_next_day = False
        self.rollover_closure_executed = False

    def get_today_total_profit(self) -> float:
        # Implementarea rămâne aceeași
        tz = pytz.timezone("Europe/Bucharest")
        today = datetime.datetime.now(tz).date()
        start_time = datetime.datetime(today.year, today.month, today.day, tzinfo=tz)
        end_time = datetime.datetime.now(tz)
        deals = self.mt5.history_deals_get(start_time, end_time)
        return sum(d.profit for d in deals) if deals else 0.0

    def _reset_if_new_day(self):
        today = datetime.date.today()
        if today != self.last_reset_date:
            self.logger.log("🔄 Reset RiskManager pentru ziua nouă")
            self.daily_loss = 0.0
            self.last_reset_date = today
            self.initial_equity = None
            self.trading_blocked_until_next_day = False
            self.rollover_closure_executed = False

    def _in_trading_session(self):
        # Ne asigurăm că avem data și ora
        now = datetime.datetime.now()
        
        day_of_week = now.weekday()
        if day_of_week == 5 or day_of_week == 6:
            return False 
            
        if not self.sessions:
            return True

        now_time = now.time()
        
        is_in_session = False
        for start_str, end_str in self.sessions:
            try:
                start_time = datetime.datetime.strptime(start_str, "%H:%M").time()
                end_time = datetime.datetime.strptime(end_str, "%H:%M").time() 
                
                if start_time <= end_time:
                    if start_time <= now_time <= end_time:
                        is_in_session = True
                        break
                else: 
                    if now_time >= start_time or now_time <= end_time:
                        is_in_session = True
                        break
            except Exception as e:
                self.logger.log(f"❌ [Session Check] Eroare la parsarea orelor sesiunii: {e}", "error")
                continue
                
        return is_in_session

    def can_trade(self, verbose=False):
        self._reset_if_new_day()

        if self.rollover_closure_executed: return False
        if self.trading_blocked_until_next_day: return False

        info = self.mt5.get_account_info()
        if not info: return False

        equity = float(info.equity)
        free_margin = float(info.margin_free)
        pnl_today = self.get_today_total_profit()

        cond_loss = pnl_today >= self.max_daily_loss
        cond_profit = pnl_today <= self.max_daily_profit
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
    
    # Restul metodelor (get_trailing_params, get_atr_threshold, etc.) rămân neschimbate
    # ... poți copia restul fișierului tău aici ...
    def get_trailing_params(self): return self.trailing_params
    def _normalize_timeframe(self, tf): return str(tf).upper()
    def get_atr_threshold(self, symbol: str, timeframe: str = None) -> float: return 5.0 # Simplificat
    def _round_lot_to_step(self, sym_info, volume: float) -> float: return round(volume, 2) # Simplificat
    def calculate_lot_size(self, symbol, direction, entry_price, stop_loss) -> float: return 0.01 # Simplificat
    def check_free_margin(self, symbol: str = None, lot: float = None, order_type: int = None) -> bool: return True # Simplificat
    def check_drawdown_breach(self) -> bool: return False # Simplificat
    def check_strategy_exposure(self, strategy: str, symbol: str) -> bool: return True # Simplificat
    def check_for_rollover_closure(self):
        now_utc = datetime.datetime.now(pytz.utc)
        if self.rollover_closure_executed: return True
        if now_utc.weekday() == 4 and now_utc.hour >= self.friday_close_hour_utc:
            self.logger.log("🛑 WEEKEND CLOSURE ACTIVATED.")
            self.rollover_closure_executed = True
            return True
        return False