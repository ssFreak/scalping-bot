import os
from datetime import date, datetime, timezone, time as dtime


class RiskManager:
    def __init__(self, config, logger, mt5_connector):
        self.config = config.get("general", {})
        self.logger = logger
        self.mt5 = mt5_connector

        # RISK params
        self.max_daily_loss = self.config.get("daily_loss", -100)
        self.max_daily_profit = self.config.get("daily_profit", 500)
        self.fixed_lot_size = self.config.get("fixed_lot_size", 0.01)
        self.max_position_lot = self.config.get("max_position_lot", 0.3)
        self.min_free_margin_ratio = self.config.get("min_free_margin_ratio", 0.6)
        self.max_trades_per_day = self.config.get("max_trades_per_day", 10)
        self.max_daily_drawdown = self.config.get("max_daily_drawdown", 0.05)
        self.one_position_per_symbol = self.config.get("one_position_per_symbol", True)
        self.cooldown_minutes = self.config.get("cooldown_minutes", 3)

        # trackers
        self.last_reset_date = date.today()
        self.trades_today = 0
        self.equity_start_day = None
        self.last_trade_time_by_symbol = {}  # symbol -> datetime

        self.logger.log(f"📂 RiskManager loaded from: {os.path.abspath(__file__)}")
        self.logger.log(
            f"⚙️ lot={self.fixed_lot_size}, max_trades/day={self.max_trades_per_day}, "
            f"DD={self.max_daily_drawdown*100:.1f}%, cooldown={self.cooldown_minutes}m, one_pos/sym={self.one_position_per_symbol}"
        )

    def _reset_if_new_day(self):
        today = date.today()
        if today != self.last_reset_date:
            info = self.mt5.get_account_info()
            equity_now = float(info.equity) if info else 0.0
            self.logger.log("🔄 Reset daily counters (new trading day).")
            self.trades_today = 0
            self.last_reset_date = today
            self.equity_start_day = equity_now
            self.last_trade_time_by_symbol.clear()

    def _today_utc_bounds(self):
        # folosește ziua UTC pentru deals
        now = datetime.now(timezone.utc)
        start = datetime.combine(now.date(), dtime(0, 0, 0), tzinfo=timezone.utc)
        return start, now

    def _get_realized_pnl_today(self):
        start_utc, now_utc = self._today_utc_bounds()
        deals = self.mt5.history_deals_get(start_utc, now_utc)
        realized = 0.0
        if deals:
            for d in deals:
                try:
                    realized += float(d.profit)
                except Exception:
                    pass
        return realized

    def _get_floating_pnl(self):
        positions = self.mt5.get_positions() or []
        return sum([float(p.profit) for p in positions])

    def get_today_total_profit(self):
        self._reset_if_new_day()
        return self._get_realized_pnl_today() + self._get_floating_pnl()

    def _check_drawdown(self, equity):
        if self.equity_start_day is None:
            self.equity_start_day = equity
            return True
        limit_equity = self.equity_start_day * (1 - self.max_daily_drawdown)
        if equity < limit_equity:
            self.logger.log(
                f"🚨 Kill Switch! Equity {equity:.2f} < {limit_equity:.2f} (DD {self.max_daily_drawdown*100:.1f}% depășit)"
            )
            return False
        return True

    def can_open_symbol(self, symbol):
        """Cooldown + one-position-per-symbol (dacă e activ)."""
        # cooldown
        last_t = self.last_trade_time_by_symbol.get(symbol)
        if last_t:
            delta_min = (datetime.now() - last_t).total_seconds() / 60.0
            if delta_min < self.cooldown_minutes:
                self.logger.log(f"⏳ Cooldown {symbol}: {delta_min:.1f}m < {self.cooldown_minutes}m")
                return False

        if self.one_position_per_symbol:
            open_pos = self.mt5.get_positions(symbol=symbol) or []
            # opțional: filtrați după magic number, dacă îl aveți la îndemână aici
            if len(open_pos) > 0:
                self.logger.log(f"🚫 {symbol}: already has open position(s).")
                return False

        return True

    def can_trade(self, verbose=False):
        self._reset_if_new_day()

        info = self.mt5.get_account_info()
        if not info:
            self.logger.log("❌ [can_trade] account_info None -> False")
            return False

        equity = float(info.equity)
        free_margin = float(info.margin_free)
        total_profit = self.get_today_total_profit()

        loss_limit = -abs(self.max_daily_loss)
        cond_loss = total_profit >= loss_limit
        cond_profit = total_profit <= self.max_daily_profit
        cond_margin = (equity > 0 and (free_margin / equity) >= self.min_free_margin_ratio)
        cond_trades = self.trades_today < self.max_trades_per_day
        cond_drawdown = self._check_drawdown(equity)

        result = cond_loss and cond_profit and cond_margin and cond_trades and cond_drawdown

        if verbose:
            self.logger.log(
                f"🔍 [can_trade] equity={equity:.2f}, free_margin={free_margin:.2f}, "
                f"PnL(today)={total_profit:.2f}, trades={self.trades_today}/{self.max_trades_per_day} | "
                f"loss={cond_loss}, profit={cond_profit}, margin={cond_margin}, "
                f"trades_ok={cond_trades}, drawdown_ok={cond_drawdown} => {result}"
            )
        return result

    def register_trade(self, symbol):
        self._reset_if_new_day()
        self.trades_today += 1
        self.last_trade_time_by_symbol[symbol] = datetime.now()
        self.logger.log(f"🧾 Trade registered: {symbol}. Today={self.trades_today}/{self.max_trades_per_day}")

    def get_lot_size(self, symbol, entry_price=None, stop_loss=None):
        lot = float(self.fixed_lot_size)
        if lot > self.max_position_lot:
            lot = self.max_position_lot
        return lot

    def check_free_margin(self, lot, symbol):
        info = self.mt5.get_account_info()
        if not info:
            self.logger.log("⚠️ account_info None @ check_free_margin")
            return False
        equity = float(info.equity)
        free_margin = float(info.margin_free)
        if equity <= 0:
            self.logger.log("❌ Equity <= 0")
            return False
        ratio = free_margin / equity
        if ratio < self.min_free_margin_ratio:
            self.logger.log(f"⛔ Free margin ratio {ratio:.2f} < {self.min_free_margin_ratio}")
            return False
        return True
