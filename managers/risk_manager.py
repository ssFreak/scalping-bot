import datetime
import pytz
import time


class RiskManager:
    def __init__(self, config, logger, trade_manager, mt5):
        self.config = config
        self.logger = logger
        self.trade_manager = trade_manager
        self.mt5 = mt5

        # parametri din YAML
        self.max_daily_loss = config.get("daily_loss", -100)
        self.risk_per_trade = config.get("risk_per_trade", 0.01)
        self.max_equity_risk = config.get("max_equity_risk", 0.1)
        self.max_daily_profit = config.get("daily_profit", 500)
        self.min_free_margin_ratio = config.get("min_free_margin_ratio", 0.6)
        self.max_drawdown = config.get("max_drawdown", 0.2)
        
        # sesiuni de trading
        general_cfg = config.get("general", {})
        self.sessions = general_cfg.get("session_hours", [])
        self.min_atr_pips = float(config.get("min_atr_pips", 5))
        self._last_can_trade_log_ts = 0.0
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

    # --------------------------------
    # Daily profit & loss management
    # --------------------------------
    def _reset_if_new_day(self):
        today = datetime.date.today()
        if today != self.last_reset_date:
            self.logger.log("🔄 Reset daily loss counter (new trading day).")
            self.daily_loss = 0.0
            self.last_reset_date = today

    def get_today_total_profit(self):
        timezone = pytz.timezone("Europe/Bucharest")
        now_local = datetime.datetime.now(timezone)

        start_local = timezone.localize(datetime.datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0))
        start_utc = start_local.astimezone(pytz.UTC)
        end_utc = now_local.astimezone(pytz.UTC)

        total_profit = 0.0
        deals = self.mt5.history_deals_get(start_utc, end_utc)
        if deals:
            for d in deals:
                try:
                    total_profit += float(d.profit)
                except Exception:
                    continue

        positions = self.mt5.positions_get()
        if positions:
            for p in positions:
                try:
                    total_profit += float(p.profit)
                except Exception:
                    continue

        return total_profit

    def update_daily_loss(self):
        self._reset_if_new_day()
        self.daily_loss = self.get_today_total_profit()
        return self.daily_loss

    def check_max_daily_loss(self):
        current_loss = self.update_daily_loss()
        if current_loss <= -abs(self.max_daily_loss):
            self.logger.log(f"🚨 Max daily loss depășit: {current_loss:.2f} (limită {self.max_daily_loss})")
            return False
        return True

    # -------------------------
    # Lot sizing
    # -------------------------
    def _round_lot_to_step(self, symbol_info, lot):
        step = float(getattr(symbol_info, "volume_step", 0.01) or 0.01)
        rounded = round(round(lot / step) * step, 8)
        return max(rounded, float(getattr(symbol_info, "volume_min", step) or step))

    def calculate_lot_size(self, symbol, direction, entry_price, stop_loss):
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

        pip = self.mt5.get_pip_size(symbol)
        point = float(getattr(sym_info, "point", 0.0) or 0.0)
        tick_value = float(getattr(sym_info, "trade_tick_value", 0.0) or 0.0)

        if pip <= 0 or point <= 0 or tick_value <= 0:
            self.logger.log(f"⚠️ pip/point/tick_value invalid pentru {symbol}")
            return 0.0

        pip_value = tick_value * (pip / point)

        sl_distance = abs(float(entry_price) - float(stop_loss))
        sl_pips = sl_distance / pip
        if sl_pips < self.min_sl_pips:
            self.logger.log(f"⚠️ SL prea mic ({sl_pips:.2f} pips), aplic minim {self.min_sl_pips} pips")
            sl_pips = self.min_sl_pips

        risk_per_1_lot = sl_pips * pip_value
        if risk_per_1_lot <= 0:
            return 0.0

        raw_lot = risk_amount / risk_per_1_lot

        vol_min = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
        vol_max = float(getattr(sym_info, "volume_max", 100.0) or 100.0)
        cfg_max = float(self.config.get("max_position_lot", vol_max))

        clamped = max(min(raw_lot, vol_max, cfg_max), vol_min)
        lot = self._round_lot_to_step(sym_info, clamped)

        self.logger.log(
            f"🧮 LotCalc {symbol}: equity={equity:.2f}, risk%={self.risk_per_trade:.3f}, "
            f"risk_amount={risk_amount:.2f}, sl_pips={sl_pips:.2f}, pip_value/lot={pip_value:.4f} -> "
            f"raw={raw_lot:.3f} => lot={lot:.2f}"
        )

        return lot

    # -------------------------
    # Margin & gating
    # -------------------------
    def check_free_margin(self):
        account_info = self.mt5.get_account_info()
        if not account_info:
            self.logger.log("⚠️ Nu s-a putut obține account_info pentru free margin.")
            return False
        if float(account_info.margin_free) <= 0:
            self.logger.log("⛔ Nu există free margin disponibil.")
            return False
        return True

    # --- filtru sesiuni ---
    def _in_trading_session(self):
        """
        Returnează True dacă acum suntem într-una din ferestrele de timp definite în general.session_hours.
        Format sesiuni: [["10:00","19:00"], ["14:00","23:00"]]
        """
        sessions = getattr(self, "sessions", [])
        if not sessions:
            return True
        now = datetime.datetime.now().time()
        for pair in sessions:
            try:
                start = datetime.datetime.strptime(pair[0], "%H:%M").time()
                end = datetime.datetime.strptime(pair[1], "%H:%M").time()
                if start <= now <= end:
                    return True
            except Exception:
                continue
        return False

    def can_trade(self, verbose=False):
        """Returnează True dacă botul poate deschide noi tranzacții.
        Păstrează toate condițiile existente și ADĂUGĂ filtrul de sesiuni + throttling la log.
        """
        if hasattr(self, "_reset_if_new_day"):
            try:
                self._reset_if_new_day()
            except Exception:
                pass

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
        
    def get_atr_threshold(self, symbol: str) -> float:
        """Returnează pragul ATR în pips pentru simbolul dat"""
        return float(self.atr_thresholds.get(symbol, 5.0))  # fallback default = 5 pips
