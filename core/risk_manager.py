import MetaTrader5 as mt5
import datetime
from core.utils import is_forex_market_open

class RiskManager:
    def __init__(self, config, logger, trade_manager):
        self.config = config
        self.logger = logger
        self.trade_manager = trade_manager

        # Parametri din YAML
        self.max_daily_loss = config.get("daily_loss", -100)      # în valută cont
        self.max_daily_profit = config.get("daily_profit", 500)   # în valută cont
        self.risk_per_trade = config.get("risk_per_trade", 0.01)  # % din equity
        self.max_equity_risk = config.get("max_equity_risk", 0.1) # max 10% equity / simbol
        self.min_free_margin_ratio = config.get("min_free_margin_ratio", 0.5)

        # Tracking pierdere zilnică
        self.daily_loss = 0.0
        self.last_reset_date = datetime.date.today()

    # ========================
    # 🔄 Daily PnL tracking
    # ========================
    def _reset_if_new_day(self):
        today = datetime.date.today()
        if today != self.last_reset_date:
            self.logger.log("🔄 Reset daily loss counter (new trading day).")
            self.daily_loss = 0.0
            self.last_reset_date = today

    def update_daily_loss(self):
        """Calculează PnL zilnic (realizat + flotant)."""
        self._reset_if_new_day()

        account_info = mt5.account_info()
        if account_info is None:
            self.logger.log("⚠️ Nu s-a putut obține account_info pentru daily loss.")
            return self.daily_loss

        today = datetime.date.today()
        from_date = datetime.datetime(today.year, today.month, today.day, 0, 0)
        deals = mt5.history_deals_get(from_date, datetime.datetime.now())

        realized_pnl = 0.0
        if deals is not None:
            for d in deals:
                realized_pnl += d.profit

        floating_pnl = 0.0
        positions = mt5.positions_get()
        if positions is not None:
            for p in positions:
                floating_pnl += p.profit

        self.daily_loss = realized_pnl + floating_pnl
        return self.daily_loss

    def check_max_daily_loss(self):
        """Blochează trading-ul dacă s-a depășit max daily loss."""
        current_loss = self.update_daily_loss()
        if current_loss <= -abs(self.max_daily_loss):
            self.logger.log(
                f"🚨 Max daily loss depășit: {current_loss:.2f} "
                f"(limită {self.max_daily_loss})"
            )
            return False
        return True

    # ========================
    # 📊 Lot sizing
    # ========================
    def calculate_lot_size(self, symbol, direction, entry_price, stop_loss):
        """Calculează lot size în funcție de risc și distanța SL."""
        account_info = mt5.account_info()
        if account_info is None:
            self.logger.log("⚠️ Nu s-a putut obține account_info pentru lot size.")
            return 0.0

        equity = account_info.equity
        risk_amount = equity * self.risk_per_trade

        tick = mt5.symbol_info(symbol)
        if tick is None:
            self.logger.log(f"⚠️ Nu s-a putut obține symbol_info pentru {symbol}")
            return 0.0

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            self.logger.log(f"⚠️ SL distance invalid pentru {symbol}.")
            return 0.0

        pip_value = tick.trade_tick_value
        if pip_value <= 0:
            pip_value = 0.0001

        lot_size = risk_amount / (sl_distance / tick.point * pip_value)

        # verifică max_equity_risk
        max_allowed = equity * self.max_equity_risk
        if risk_amount > max_allowed:
            lot_size *= max_allowed / risk_amount

        lot_size = round(lot_size, 2)
        self.logger.log(
            f"🔍 Lot calculat pentru {symbol}: {lot_size} "
            f"(Risk {self.risk_per_trade*100:.1f}%, SL dist {sl_distance}, "
            f"Equity {equity:.2f})"
        )

        return lot_size

    # ========================
    # 💰 Margin checks
    # ========================
    def check_free_margin(self):
        """Verifică dacă free margin ratio >= min_free_margin_ratio."""
        account_info = mt5.account_info()
        if account_info is None:
            self.logger.log("⚠️ Nu s-a putut obține account_info pentru free margin.")
            return False

        if account_info.equity <= 0:
            self.logger.log("⚠️ Equity invalid <= 0.")
            return False

        free_margin_ratio = account_info.margin_free / account_info.equity
        self.logger.log(
            f"🔍 [check_free_margin] ratio: {free_margin_ratio:.2f} "
            f"(min {self.min_free_margin_ratio})"
        )

        if free_margin_ratio < self.min_free_margin_ratio:
            self.logger.log(
                f"⛔ Free margin ratio prea mic ({free_margin_ratio:.2f}) "
                f"< {self.min_free_margin_ratio}"
            )
            return False

        return True

    # ========================
    # 🚦 Trade permission
    # ========================
    def can_trade(self, verbose=False):
        """Returnează True dacă botul poate tranzacționa acum."""
        total_profit = self.trade_manager.get_today_total_profit()
        is_market_open = is_forex_market_open()

        if verbose:
            self.logger.log(f"🔍 [can_trade] Market open: {is_market_open}")
            self.logger.log(f"🔍 [can_trade] Today's total profit: {total_profit:.2f}")
            self.logger.log(f"🔍 [can_trade] Max daily loss: {self.max_daily_loss}")
            self.logger.log(f"🔍 [can_trade] Max daily profit: {self.max_daily_profit}")

        can_trade_result = (
            is_market_open
            and (total_profit > self.max_daily_loss)
            and (total_profit < self.max_daily_profit)
        )

        if verbose:
            self.logger.log(f"🔍 [can_trade] Final result: {can_trade_result}")
            if not can_trade_result:
                reasons = []
                if not is_market_open:
                    reasons.append("Market closed")
                if total_profit <= self.max_daily_loss:
                    reasons.append(
                        f"Daily loss limit reached ({total_profit:.2f} <= {self.max_daily_loss})"
                    )
                if total_profit >= self.max_daily_profit:
                    reasons.append(
                        f"Daily profit target reached ({total_profit:.2f} >= {self.max_daily_profit})"
                    )
                self.logger.log(f"🔍 [can_trade] Blocking reasons: {', '.join(reasons)}")

        return can_trade_result
