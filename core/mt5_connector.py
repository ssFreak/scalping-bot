import MetaTrader5 as mt5

class MT5Connector:
    def __init__(self, logger):
        self.connected = False
        self.logger = logger

    def initialize(self, login=None, password=None, server=None):
        if self.connected:
            return True
        if login and password and server:
            self.connected = mt5.initialize(login=login, password=password, server=server)
        else:
            self.connected = mt5.initialize()
        return self.connected

    def shutdown(self):
        if self.connected:
            mt5.shutdown()
            self.connected = False

    # --------------------------
    #   Data
    # --------------------------
    def get_account_info(self):
        return mt5.account_info()
        
    def symbol_select(self, symbol: str, enable: bool = True):
        return mt5.symbol_select(symbol, enable)

    def get_timeframe(self, tf: str):
        return getattr(mt5, f"TIMEFRAME_{tf.upper()}", None)

    def get_symbol_tick(self, symbol):
        return mt5.symbol_info_tick(symbol)

    def get_symbol_info_tick(self, symbol):
        """
        Compatibilitate: întoarce tick-ul curent (bid/ask) pentru simbol.
        Echivalent cu mt5.symbol_info_tick(symbol).
        """
        return mt5.symbol_info_tick(symbol)

    def get_symbol_info(self, symbol):
        return mt5.symbol_info(symbol)

    def get_symbol_tick(self, symbol):
        return mt5.symbol_info_tick(symbol)

    def get_rates(self, symbol, timeframe, count=100):
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        return None if rates is None else rates

    def get_positions(self, symbol=None):
        return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()

    def history_deals_get(self, date_from, date_to):
        return mt5.history_deals_get(date_from, date_to)

    # --------------------------
    #   Orders
    # --------------------------
    def order_send(self, request):
        return mt5.order_send(request)

    def last_error(self):
        return mt5.last_error()

    # --------------------------
    #   Constants proxy
    # --------------------------
    @property
    def ORDER_TYPE_BUY(self): return mt5.ORDER_TYPE_BUY
    @property
    def ORDER_TYPE_SELL(self): return mt5.ORDER_TYPE_SELL
    @property
    def ORDER_TYPE_BUY_STOP(self): return mt5.ORDER_TYPE_BUY_STOP
    @property
    def ORDER_TYPE_SELL_STOP(self): return mt5.ORDER_TYPE_SELL_STOP

    @property
    def TRADE_ACTION_DEAL(self): return mt5.TRADE_ACTION_DEAL
    @property
    def TRADE_ACTION_PENDING(self): return mt5.TRADE_ACTION_PENDING
    @property
    def TRADE_ACTION_SLTP(self): return mt5.TRADE_ACTION_SLTP

    @property
    def TRADE_RETCODE_DONE(self): return mt5.TRADE_RETCODE_DONE

    @property
    def ORDER_FILLING_FOK(self): return mt5.ORDER_FILLING_FOK
    @property
    def ORDER_FILLING_RETURN(self): return mt5.ORDER_FILLING_RETURN

    @property
    def ORDER_TIME_GTC(self): return mt5.ORDER_TIME_GTC
    @property
    def ORDER_TIME_SPECIFIED(self): return mt5.ORDER_TIME_SPECIFIED

    @property
    def TIMEFRAME_M1(self): return mt5.TIMEFRAME_M1
    @property
    def TIMEFRAME_M5(self): return mt5.TIMEFRAME_M5
    @property
    def TIMEFRAME_H1(self): return mt5.TIMEFRAME_H1
