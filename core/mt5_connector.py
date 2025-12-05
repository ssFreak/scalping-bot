# core/mt5_connector.py - IMPLEMENTARE THREAD-SAFE COMPLETĂ

import MetaTrader5 as mt5
import threading

class MT5Connector:
    def __init__(self, logger):
        self.logger = logger
        self.initialized = False
        self.mt5 = None
        # 🔒 LOCK GLOBAL: Previne accesul simultan la terminalul MT5 din thread-uri diferite
        self._lock = threading.Lock()

    def initialize(self, login=None, password=None, server=None):
        with self._lock:
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
                self.logger.log("✅ MT5 initialized (Thread-Safe Mode)")
            return True
        
    def _expose_constants(self):
        # Constantele nu necesită lock deoarece sunt asignări locale
        self.ORDER_TYPE_BUY = mt5.ORDER_TYPE_BUY
        self.ORDER_TYPE_SELL = mt5.ORDER_TYPE_SELL
        self.ORDER_TYPE_BUY_LIMIT = mt5.ORDER_TYPE_BUY_LIMIT
        self.ORDER_TYPE_SELL_LIMIT = mt5.ORDER_TYPE_SELL_LIMIT
        self.ORDER_TYPE_BUY_STOP = mt5.ORDER_TYPE_BUY_STOP
        self.ORDER_TYPE_SELL_STOP = mt5.ORDER_TYPE_SELL_STOP
        
        self.TRADE_ACTION_DEAL = mt5.TRADE_ACTION_DEAL
        self.TRADE_ACTION_SLTP = mt5.TRADE_ACTION_SLTP
        self.TRADE_ACTION_MODIFY = mt5.TRADE_ACTION_MODIFY
        self.TRADE_ACTION_REMOVE = mt5.TRADE_ACTION_REMOVE

        self.ORDER_TIME_GTC = mt5.ORDER_TIME_GTC
        self.ORDER_TIME_DAY = mt5.ORDER_TIME_DAY
        
        self.ORDER_FILLING_FOK = mt5.ORDER_FILLING_FOK
        self.ORDER_FILLING_IOC = mt5.ORDER_FILLING_IOC
        
        self.TRADE_RETCODE_DONE = mt5.TRADE_RETCODE_DONE
        self.TRADE_RETCODE_NO_CHANGES = mt5.TRADE_RETCODE_NO_CHANGES

    def _resolve_timeframe(self, timeframe):
        # Helper intern, apelat deja sub lock de funcțiile publice
        if isinstance(timeframe, str):
            tf_upper = timeframe.upper()
            return getattr(self.mt5, f"TIMEFRAME_{tf_upper}", None)
        elif isinstance(timeframe, int):
            return timeframe
        return None

    # --- Wrapperi Thread-Safe pentru funcțiile MT5 ---

    def get_account_info(self):
        with self._lock:
            return self.mt5.account_info() if self.mt5 else None

    def symbol_select(self, symbol, enable=True):
        with self._lock:
            return self.mt5.symbol_select(symbol, enable) if self.mt5 else False

    def get_symbol_info(self, symbol):
        with self._lock:
            return self.mt5.symbol_info(symbol) if self.mt5 else None

    def get_symbol_info_tick(self, symbol):
        with self._lock:
            return self.mt5.symbol_info_tick(symbol) if self.mt5 else None

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        with self._lock:
            resolved_tf = self._resolve_timeframe(timeframe)
            if resolved_tf is None:
                if self.logger: self.logger.log(f"❌ Timeframe invalid: {timeframe}", "error")
                return None
            return self.mt5.copy_rates_from_pos(symbol, resolved_tf, start_pos, count) if self.mt5 else None

    def get_rates(self, symbol, timeframe, count):
        return self.copy_rates_from_pos(symbol, timeframe, 0, count)

    def order_send(self, request):
        with self._lock:
            symbol = request.get("symbol")
            result = self.mt5.order_send(request)
            
            if self.logger:
                if result is None:
                    self.logger.log(f"❌ order_send failed pentru {symbol}")
                elif result.retcode != self.mt5.TRADE_RETCODE_DONE:
                    # Logăm ca eroare doar dacă nu e o verificare de marjă
                    if request.get("action") == self.mt5.TRADE_ACTION_DEAL:
                         self.logger.log(f"⚠️ order_send error {symbol}: {result.comment} ({result.retcode})")
            return result
    
    def positions_get(self, symbol=None, ticket=None):
        with self._lock:
            if not self.mt5: return None
            if ticket is not None:
                return self.mt5.positions_get(ticket=ticket)
            if symbol is not None:
                return self.mt5.positions_get(symbol=symbol)
            return self.mt5.positions_get()

    def orders_get(self, symbol=None, ticket=None):
        with self._lock:
            if not self.mt5: return None
            if ticket is not None:
                return self.mt5.orders_get(ticket=ticket)
            if symbol is not None:
                return self.mt5.orders_get(symbol=symbol)
            return self.mt5.orders_get()

    def history_deals_get(self, date_from, date_to):
        with self._lock:
            return self.mt5.history_deals_get(date_from, date_to) if self.mt5 else None

    def get_timeframe(self, tf_str):
        with self._lock:
            return getattr(self.mt5, f"TIMEFRAME_{tf_str.upper()}") if self.mt5 else None
        
    def last_error(self):
        with self._lock:
            return mt5.last_error()
        
    def get_pip_size(self, symbol):
        with self._lock:
            info = self.mt5.symbol_info(symbol)
            if not info: return 0.0001
            return 0.01 if info.digits == 3 else 0.0001

    def get_digits(self, symbol: str) -> int:
        with self._lock:
            info = self.mt5.symbol_info(symbol)
            return info.digits if info else 5

    def shutdown(self):
        with self._lock:
            if self.mt5:
                self.mt5.shutdown()
                self.initialized = False
                if self.logger:
                    self.logger.log("🛑 MT5 shutdown complete")