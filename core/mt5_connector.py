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

        if not ok:
            if self.logger:
                self.logger.log(f"❌ MT5 initialization failed: {mt5.last_error()}")
            return False

        self.mt5 = mt5
        self._expose_constants()
        self.initialized = True
        if self.logger:
            self.logger.log("✅ MT5 initialized")
        return True
        
    def _expose_constants(self):
        # === Tipuri ordine ===
        self.ORDER_TYPE_BUY = mt5.ORDER_TYPE_BUY
        self.ORDER_TYPE_SELL = mt5.ORDER_TYPE_SELL
        self.ORDER_TYPE_BUY_LIMIT = mt5.ORDER_TYPE_BUY_LIMIT
        self.ORDER_TYPE_SELL_LIMIT = mt5.ORDER_TYPE_SELL_LIMIT
        self.ORDER_TYPE_BUY_STOP = mt5.ORDER_TYPE_BUY_STOP
        self.ORDER_TYPE_SELL_STOP = mt5.ORDER_TYPE_SELL_STOP
        self.ORDER_TYPE_BUY_STOP_LIMIT = mt5.ORDER_TYPE_BUY_STOP_LIMIT
        self.ORDER_TYPE_SELL_STOP_LIMIT = mt5.ORDER_TYPE_SELL_STOP_LIMIT

        # === Acțiuni trade ===
        self.TRADE_ACTION_DEAL = mt5.TRADE_ACTION_DEAL
        self.TRADE_ACTION_PENDING = mt5.TRADE_ACTION_PENDING
        self.TRADE_ACTION_SLTP = mt5.TRADE_ACTION_SLTP
        self.TRADE_ACTION_MODIFY = mt5.TRADE_ACTION_MODIFY
        self.TRADE_ACTION_REMOVE = mt5.TRADE_ACTION_REMOVE

        # === Expirare ordine ===
        self.ORDER_TIME_GTC = mt5.ORDER_TIME_GTC
        self.ORDER_TIME_DAY = mt5.ORDER_TIME_DAY
        self.ORDER_TIME_SPECIFIED = mt5.ORDER_TIME_SPECIFIED
        self.ORDER_TIME_SPECIFIED_DAY = mt5.ORDER_TIME_SPECIFIED_DAY

        # === Tipuri filling ===
        self.ORDER_FILLING_FOK = mt5.ORDER_FILLING_FOK
        self.ORDER_FILLING_IOC = mt5.ORDER_FILLING_IOC
        self.ORDER_FILLING_RETURN = mt5.ORDER_FILLING_RETURN

        # === Rezultate trade (retcodes) ===
        self.TRADE_RETCODE_DONE = mt5.TRADE_RETCODE_DONE
        self.TRADE_RETCODE_DONE_PARTIAL = mt5.TRADE_RETCODE_DONE_PARTIAL
        self.TRADE_RETCODE_ERROR = mt5.TRADE_RETCODE_ERROR
        self.TRADE_RETCODE_TIMEOUT = mt5.TRADE_RETCODE_TIMEOUT
        self.TRADE_RETCODE_INVALID = mt5.TRADE_RETCODE_INVALID
        self.TRADE_RETCODE_INVALID_VOLUME = mt5.TRADE_RETCODE_INVALID_VOLUME
        self.TRADE_RETCODE_INVALID_PRICE = mt5.TRADE_RETCODE_INVALID_PRICE
        self.TRADE_RETCODE_INVALID_STOPS = mt5.TRADE_RETCODE_INVALID_STOPS
        self.TRADE_RETCODE_TRADE_DISABLED = mt5.TRADE_RETCODE_TRADE_DISABLED
        self.TRADE_RETCODE_MARKET_CLOSED = mt5.TRADE_RETCODE_MARKET_CLOSED
        self.TRADE_RETCODE_NO_MONEY = mt5.TRADE_RETCODE_NO_MONEY
        self.TRADE_RETCODE_PRICE_CHANGED = mt5.TRADE_RETCODE_PRICE_CHANGED
        self.TRADE_RETCODE_PRICE_OFF = mt5.TRADE_RETCODE_PRICE_OFF
        self.TRADE_RETCODE_INVALID_EXPIRATION = mt5.TRADE_RETCODE_INVALID_EXPIRATION
        self.TRADE_RETCODE_ORDER_CHANGED = mt5.TRADE_RETCODE_ORDER_CHANGED
        self.TRADE_RETCODE_TOO_MANY_REQUESTS = mt5.TRADE_RETCODE_TOO_MANY_REQUESTS
        self.TRADE_RETCODE_NO_CHANGES = mt5.TRADE_RETCODE_NO_CHANGES
        self.TRADE_RETCODE_SERVER_DISABLES_AT = mt5.TRADE_RETCODE_SERVER_DISABLES_AT
        self.TRADE_RETCODE_CLIENT_DISABLES_AT = mt5.TRADE_RETCODE_CLIENT_DISABLES_AT
        self.TRADE_RETCODE_LOCKED = mt5.TRADE_RETCODE_LOCKED
        self.TRADE_RETCODE_FROZEN = mt5.TRADE_RETCODE_FROZEN
        self.TRADE_RETCODE_INVALID_FILL = mt5.TRADE_RETCODE_INVALID_FILL
        self.TRADE_RETCODE_CONNECTION = mt5.TRADE_RETCODE_CONNECTION
        self.TRADE_RETCODE_ONLY_REAL = mt5.TRADE_RETCODE_ONLY_REAL
        self.TRADE_RETCODE_LIMIT_ORDERS = mt5.TRADE_RETCODE_LIMIT_ORDERS
        self.TRADE_RETCODE_LIMIT_VOLUME = mt5.TRADE_RETCODE_LIMIT_VOLUME
        self.TRADE_RETCODE_INVALID_ORDER = mt5.TRADE_RETCODE_INVALID_ORDER

        # === Expiration Mode (bitmask) — bitii reali folosiți de broker ===
        self.SYMBOL_EXPIRATION_GTC = 1              # Good Till Cancel
        self.SYMBOL_EXPIRATION_DAY = 2              # Valid doar azi
        self.SYMBOL_EXPIRATION_SPECIFIED = 4        # Expiră la un datetime
        self.SYMBOL_EXPIRATION_SPECIFIED_DAY = 8    # Expiră la 23:59:59 din ziua setată

        # === CHEIE compat pentru symbol_info_integer (virtuală în wrapper) ===
        # Unele build-uri MetaTrader5 pentru Python NU expun mt5.SYMBOL_EXPIRATION_MODE.
        # Ca să nu crape strategiile, oferim o cheie "virtuală" pe care o rezolvăm în wrapperul symbol_info_integer.
        self.SYMBOL_EXPIRATION_MODE = "_WRAP_EXPIRATION_MODE_"

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
        symbol = request.get("symbol")
        info = mt5.symbol_info(symbol)
        if not info:
            if self.logger:
                self.logger.log(f"❌ Symbol info indisponibil pentru {symbol}")
            return None

        # Fallback pentru filling_mode nesuportat
        if info.filling_mode != request.get("type_filling"):
            if self.logger:
                self.logger.log(f"⚠️ Filling mode {request.get('type_filling')} "
                                f"nu este suportat pentru {symbol}, "
                                f"fallback la ORDER_FILLING_RETURN")
            request["type_filling"] = mt5.ORDER_FILLING_RETURN

        result = mt5.order_send(request)

        if self.logger:
            if result is None:
                self.logger.log(f"❌ order_send failed pentru {symbol}")
            elif result.retcode != mt5.TRADE_RETCODE_DONE:
                self.logger.log(f"⚠️ order_send pentru {symbol} a eșuat "
                                f"(retcode={result.retcode}, comment={getattr(result, 'comment', '')})")
            else:
                self.logger.log(f"✅ order_send OK pentru {symbol}: ticket={result.order}")
        return result

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
        
    def last_error(self):
        return mt5.last_error()
        
    def symbol_info_integer(self, symbol, prop):
        """
        Compat layer:
        - Dacă prop == self.SYMBOL_EXPIRATION_MODE (cheia virtuală din wrapper),
          returnăm info.expiration_mode (sau 0 dacă nu e disponibil).
        - Altfel, delegăm către mt5.symbol_info_integer (dacă există prop în build-ul curent).
        """
        if prop == self.SYMBOL_EXPIRATION_MODE:
            info = self.get_symbol_info(symbol)
            return int(getattr(info, "expiration_mode", 0)) if info else 0
        # fallback către API-ul nativ
        return mt5.symbol_info_integer(symbol, prop)

    # --- timp server
    def time(self):
        return mt5.time()

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

    def order_calc_margin(self, action, symbol, volume, price):
        """
        Wrapper peste mt5.order_calc_margin.
        Returnează marja necesară pentru un ordin propus.
        """
        try:
            return mt5.order_calc_margin(action, symbol, volume, price)
        except Exception as e:
            self.logger.log(f"❌ Eroare la order_calc_margin pentru {symbol}: {e}")
            return None