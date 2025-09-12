import MetaTrader5 as mt5


class MT5Connector:
    def __init__(self, logger):
        self.logger = logger
        self.connected = False

        # Expose MT5 constants
        self.ORDER_TYPE_BUY = mt5.ORDER_TYPE_BUY
        self.ORDER_TYPE_SELL = mt5.ORDER_TYPE_SELL
        self.TRADE_ACTION_DEAL = mt5.TRADE_ACTION_DEAL
        self.TRADE_ACTION_SLTP = mt5.TRADE_ACTION_SLTP
        self.ORDER_TIME_GTC = mt5.ORDER_TIME_GTC
        self.ORDER_FILLING_FOK = mt5.ORDER_FILLING_FOK
        self.TRADE_RETCODE_DONE = mt5.TRADE_RETCODE_DONE

    # Connection
    def initialize(self, login=None, password=None, server=None):
        if not mt5.initialize():
            self.logger.log(f"❌ MT5 initialize failed: {mt5.last_error()}")
            return False
        if login and password and server:
            if not mt5.login(login=login, password=password, server=server):
                self.logger.log(f"❌ MT5 login failed: {mt5.last_error()}")
                return False
        self.connected = True
        self.logger.log("✅ MT5 connection established.")
        return True

    def shutdown(self):
        if self.connected:
            mt5.shutdown()
            self.connected = False
            self.logger.log("📴 MT5 connection closed.")

    # Account
    def get_account_info(self):
        return mt5.account_info()

    def get_equity(self):
        info = mt5.account_info()
        return info.equity if info else 0.0

    def get_free_margin(self):
        info = mt5.account_info()
        return info.margin_free if info else 0.0

    # Market Data
    def get_symbol_info(self, symbol):
        return mt5.symbol_info(symbol)

    def symbol_select(self, symbol, enable=True):
        return mt5.symbol_select(symbol, enable)

    def get_tick(self, symbol):
        return mt5.symbol_info_tick(symbol)

    def get_rates(self, symbol, timeframe, count=100):
        return mt5.copy_rates_from_pos(symbol, timeframe, 0, count)

    def history_deals_get(self, frm, to):
        return mt5.history_deals_get(frm, to)

    def get_positions(self, symbol=None):
        return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()

    # Orders
    def order_send(self, request):
        return mt5.order_send(request)

    # Helpers
    def get_timeframe(self, tf_str: str):
        return getattr(mt5, f"TIMEFRAME_{tf_str}", None)
