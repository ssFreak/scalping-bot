import MetaTrader5 as mt5

class MT5Connector:
    def __init__(self, logger):
        self.logger = logger
        self.initialized = False
        self.mt5 = None

    def initialize(self, login=None, password=None, server=None):
        ok = False
        if login and password and server:
            ok = mt5.initialize(login=login, password=password, server=server)
        else:
            ok = mt5.initialize()
            
        self.ORDER_TYPE_BUY_STOP = mt5.ORDER_TYPE_BUY_STOP
        self.ORDER_TYPE_SELL_STOP = mt5.ORDER_TYPE_SELL_STOP
        self.TRADE_ACTION_PENDING = mt5.TRADE_ACTION_PENDING
        self.ORDER_TIME_SPECIFIED = mt5.ORDER_TIME_SPECIFIED
        self.ORDER_FILLING_RETURN = mt5.ORDER_FILLING_RETURN

        if not ok:
            if self.logger:
                self.logger.log(f"❌ MT5 initialization failed: {mt5.last_error()}")
            return False

        self.mt5 = mt5
        self.initialized = True
        if self.logger:
            self.logger.log("✅ MT5 initialized")
        return True

    # --- account
    def get_account_info(self):
        return self.mt5.account_info() if self.mt5 else None

    # --- symbol ops
    def symbol_select(self, symbol, enable=True):
        return self.mt5.symbol_select(symbol, enable) if self.mt5 else False

    def get_symbol_info(self, symbol):
        return self.mt5.symbol_info(symbol) if self.mt5 else None

    def get_symbol_info_tick(self, symbol):
        return self.mt5.symbol_info_tick(symbol) if self.mt5 else None

    def get_symbol_tick(self, symbol):
        # alias convenabil (nu schimbă semnături existente)
        return self.get_symbol_info_tick(symbol)

    # --- market data
    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        return self.mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count) if self.mt5 else None

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        return self.mt5.copy_rates_range(symbol, timeframe, date_from, date_to) if self.mt5 else None

    # wrapper comod, des folosit în proiect
    def get_rates(self, symbol, timeframe, count):
        return self.copy_rates_from_pos(symbol, timeframe, 0, count)

    # --- orders / positions / history
    def order_send(self, request):
        return self.mt5.order_send(request) if self.mt5 else None

    def positions_get(self, symbol=None):
        if not self.mt5:
            return None
        return self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()

    def orders_get(self, symbol=None):
        if not self.mt5:
            return None
        return self.mt5.orders_get(symbol=symbol) if symbol else self.mt5.orders_get()

    def history_deals_get(self, date_from, date_to):
        return self.mt5.history_deals_get(date_from, date_to) if self.mt5 else None

    def history_orders_get(self, date_from, date_to):
        return self.mt5.history_orders_get(date_from, date_to) if self.mt5 else None

    # --- timeframes
    def get_timeframe(self, tf_str):
        # ex: 'M1', 'M5', 'H1'
        return getattr(self.mt5, f"TIMEFRAME_{tf_str.upper()}") if self.mt5 else None

    # --- pips util
    def get_pip_size(self, symbol):
        """
        Returnează dimensiunea unui PIP pentru simbol:
          - digits=5/4 -> 0.0001 (majore cu 4/5 zecimale)
          - digits=3/2 -> 0.01   (JPY etc.)
        """
        info = self.get_symbol_info(symbol)
        if not info:
            return 0.0001
        return 10 ** (-(info.digits - 1))

    # --- shutdown
    def shutdown(self):
        if self.mt5:
            self.mt5.shutdown()
            self.initialized = False
            if self.logger:
                self.logger.log("🛑 MT5 shutdown complete")
