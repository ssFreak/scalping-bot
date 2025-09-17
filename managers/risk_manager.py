import datetime
import pytz

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

        trailing_cfg = config.get("trailing", {})
        self.trailing_params = {
            "be_min_profit_pips": trailing_cfg.get("be_min_profit_pips", 10),
            "step_pips": trailing_cfg.get("step_pips", 5),
            "atr_multiplier": trailing_cfg.get("atr_multiplier", 1.5),
        }

        # protecție suplimentară: SL minim în pips (evită lot uriaș la SL foarte mic)
        self.min_sl_pips = float(config.get("min_sl_pips", 5.0))

        self.daily_loss = 0.0
        self.last_reset_date = datetime.date.today()

    def _reset_if_new_day(self):
        today = datetime.date.today()
        if today != self.last_reset_date:
            self.logger.log("🔄 Reset daily loss counter (new trading day).")
            self.daily_loss = 0.0
            self.last_reset_date = today

    def get_today_total_profit(self):
        """
        Profit realizat astăzi + profit flotant curent (timezone Europe/Bucharest).
        """
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

    def _round_lot_to_step(self, symbol_info, lot):
        step = float(getattr(symbol_info, "volume_step", 0.01) or 0.01)
        # rotunjire la cel mai apropiat multiplu de step
        rounded = round(round(lot / step) * step, 8)
        return max(rounded, float(getattr(symbol_info, "volume_min", step) or step))

    def calculate_lot_size(self, symbol, direction, entry_price, stop_loss):
        """
        Calculează lot-ul în funcție de risc % și distanța SL în PIPS,
        folosind pip_value corect (din tick_value * pip/point), și limitează
        la volume_min/step/max + max_position_lot din config.
        """
        account_info = self.mt5.get_account_info()
        if not account_info:
            self.logger.log("⚠️ Nu s-a putut obține account_info pentru lot size.")
            return 0.0

        equity = float(account_info.equity)
        risk_amount = equity * float(self.risk_per_trade)

        sym_info = self.mt5.get_symbol_info(symbol)
        if not sym_info:
            self.logger.log(f"⚠️ Nu s-a putut obține symbol_info pentru {symbol}")
            return 0.0

        pip = self.mt5.get_pip_size(symbol)
        point = float(getattr(sym_info, "point", 0.0) or 0.0)
        tick_value = float(getattr(sym_info, "trade_tick_value", 0.0) or 0.0)

        if pip <= 0 or point <= 0 or tick_value <= 0:
            self.logger.log(f"⚠️ pip/point/tick_value invalid pentru {symbol}: pip={pip}, point={point}, tick_value={tick_value}")
            return 0.0

        # valoarea pe PIP pentru 1.0 lot: tick_value (pe tick) * (pip/point)
        # ex: EURUSD/USDCHF -> point=0.00001, pip=0.0001 => pip/point=10 => pip_value ≈ 10× tick_value
        pip_value = tick_value * (pip / point)

        sl_distance = abs(float(entry_price) - float(stop_loss))
        sl_pips = sl_distance / pip

        # protecție SL minim
        if sl_pips < self.min_sl_pips:
            self.logger.log(f"⚠️ SL prea mic ({sl_pips:.2f} pips), aplic minim {self.min_sl_pips} pips")
            sl_pips = self.min_sl_pips

        # Risk per lot = sl_pips * pip_value; Lot = risc / risk_per_lot
        risk_per_1_lot = sl_pips * pip_value
        if risk_per_1_lot <= 0:
            return 0.0

        raw_lot = risk_amount / risk_per_1_lot

        # limite simbol + config
        vol_min = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
        vol_max = float(getattr(sym_info, "volume_max", 100.0) or 100.0)
        vol_step = float(getattr(sym_info, "volume_step", 0.01) or 0.01)
        cfg_max = float(self.config.get("max_position_lot", vol_max))

        # clamp + round la step
        clamped = max(min(raw_lot, vol_max, cfg_max), vol_min)
        lot = self._round_lot_to_step(sym_info, clamped)

        # debug util
        self.logger.log(
            f"🧮 LotCalc {symbol}: equity={equity:.2f}, risk%={self.risk_per_trade:.3f}, "
            f"risk_amount={risk_amount:.2f}, sl_pips={sl_pips:.2f}, pip_value/lot={pip_value:.4f} -> "
            f"raw={raw_lot:.3f} => lot={lot:.2f} (min={vol_min}, step={vol_step}, max={min(vol_max, cfg_max)})"
        )

        return lot

    def check_free_margin(self):
        account_info = self.mt5.get_account_info()
        if not account_info:
            self.logger.log("⚠️ Nu s-a putut obține account_info pentru free margin.")
            return False
        if float(account_info.margin_free) <= 0:
            self.logger.log("⛔ Nu există free margin disponibil.")
            return False
        return True

    def can_trade(self, verbose=False):
        self._reset_if_new_day()
        info = self.mt5.get_account_info()
        if not info:
            if verbose:
                self.logger.log("❌ [can_trade] Nu am putut obține account_info -> False")
            return False

        equity = float(info.equity)
        free_margin = float(info.margin_free)
        total_profit = self.get_today_total_profit()

        cond_loss = total_profit >= float(self.max_daily_loss)
        cond_profit = total_profit <= float(self.max_daily_profit)
        cond_margin = (equity > 0 and (free_margin / equity) >= float(self.min_free_margin_ratio))

        result = cond_loss and cond_profit and cond_margin

        if verbose:
            self.logger.log(
                f"🔍 [can_trade] equity={equity:.2f}, free_margin={free_margin:.2f}, "
                f"PnL(today)={total_profit:.2f} => {result}"
            )
        return result

    def get_trailing_params(self):
        return self.trailing_params
