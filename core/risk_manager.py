import MetaTrader5 as mt5
import datetime
from core.utils import is_forex_market_open

class RiskManager:
    def __init__(self, config, logger, trade_manager):
        self.config = config
        self.logger = logger
        self.trade_manager = trade_manager

        # parametri din YAML
        self.max_daily_loss = config.get("daily_loss", -100)  # în valută cont
        self.risk_per_trade = config.get("risk_per_trade", 0.01)  # % din equity
        self.max_equity_risk = config.get("max_equity_risk", 0.1)  # max 10% din equity per simbol
        self.max_daily_profit = config.get("daily_profit", 500)

        # track pierdere zilnică
        self.daily_loss = 0.0
        self.last_reset_date = datetime.date.today()

    def _reset_if_new_day(self):
        today = datetime.date.today()
        if today != self.last_reset_date:
            self.logger.log("🔄 Reset daily loss counter (new trading day).")
            self.daily_loss = 0.0
            self.last_reset_date = today

    def update_daily_loss(self):
        """
        Calculează pierderea/profitul zilnic din istoricul contului + poziții curente.
        """
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

        # profit/pierdere flotantă pe poziții curente
        positions = mt5.positions_get()
        floating_pnl = 0.0
        if positions is not None:
            for p in positions:
                floating_pnl += p.profit

        self.daily_loss = realized_pnl + floating_pnl
        return self.daily_loss

    def check_max_daily_loss(self):
        """
        Verifică dacă pierderea zilnică a depășit limita setată.
        """
        current_loss = self.update_daily_loss()
        if current_loss <= -abs(self.max_daily_loss):
            self.logger.log(f"🚨 Max daily loss depășit: {current_loss:.2f} (limită {self.max_daily_loss})")
            return False
        return True

    def calculate_lot_size(self, symbol, direction, entry_price, stop_loss):
        """
        Calculează mărimea lotului în funcție de risk_per_trade și distanța SL.
        """
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
            return 0.0

        # valoare per pip
        pip_value = tick.trade_tick_value
        if pip_value <= 0:
            pip_value = 0.0001

        lot_size = risk_amount / (sl_distance / tick.point * pip_value)

        # verifică să nu depășească max_equity_risk
        max_allowed = equity * self.max_equity_risk
        if risk_amount > max_allowed:
            lot_size *= max_allowed / risk_amount

        return round(lot_size, 2)

    def check_free_margin(self):
        """
        Verifică dacă există suficient free margin pentru a deschide un ordin.
        """
        account_info = mt5.account_info()
        if account_info is None:
            self.logger.log("⚠️ Nu s-a putut obține account_info pentru free margin.")
            return False

        if account_info.margin_free <= 0:
            self.logger.log("⛔ Nu există free margin disponibil.")
            return False

        return True
        
    def can_trade(self):
        """Returnează True dacă bot-ul ar trebui să faca trading acum."""
        total_profit = self.trade_manager.get_today_total_profit()
        is_market_open = is_forex_market_open()
        return is_market_open and (total_profit > self.max_daily_loss) and (total_profit < self.max_daily_profit)
