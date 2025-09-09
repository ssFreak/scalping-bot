import MetaTrader5 as mt5

class MT5Connector:
    def __init__(self, logger=None):
        self.logger = logger

    def initialize(self, login=None, password=None, server=None):
        """
        Inițializează conexiunea cu MetaTrader5.
        Dacă nu se dau login/parola/server, folosește terminalul deja deschis.
        """
        if not mt5.initialize():
            error = mt5.last_error()
            if self.logger:
                self.logger.log(f"❌ MT5 init failed: {error}")
            raise RuntimeError(f"MT5 initialization failed: {error}")
        else:
            if self.logger:
                self.logger.log("✅ MT5 initialized successfully")

        # Dacă s-a dat login explicit -> facem și login
        if login and password and server:
            authorized = mt5.login(login=login, password=password, server=server)
            if not authorized:
                error = mt5.last_error()
                if self.logger:
                    self.logger.log(f"❌ MT5 login failed: {error}")
                raise RuntimeError(f"MT5 login failed: {error}")
            else:
                if self.logger:
                    self.logger.log(f"✅ Logged in to account {login} on server {server}")

        # Verifică contul
        account_info = mt5.account_info()
        if account_info is None:
            if self.logger:
                self.logger.log("❌ No account connected")
            raise RuntimeError("No account connected to MT5")
        else:
            if self.logger:
                self.logger.log(
                    f"✅ Connected to account {account_info.login}, balance={account_info.balance}"
                )
        return True

    def shutdown(self):
        """Închide conexiunea cu MetaTrader5."""
        mt5.shutdown()
        if self.logger:
            self.logger.log("👋 MT5 connection closed")

    def get_symbols(self):
        """Returnează lista de simboluri disponibile."""
        return [s.name for s in mt5.symbols_get()]

    def get_rates(self, symbol, timeframe=mt5.TIMEFRAME_M15, count=10):
        """Returnează ultimele candele pentru un simbol dat."""
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None:
            if self.logger:
                self.logger.log(f"⚠️ No rates data for {symbol}")
        return rates
