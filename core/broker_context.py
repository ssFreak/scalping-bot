# core/broker_context.py
import pandas as pd
from core.mt5_connector import MT5Connector

class LiveBrokerContext:
    """
    Acționează ca o interfață unificată între o strategie și managerii
    care interacționează cu piața reală (MT5).
    """
    def __init__(self, logger, risk_manager, trade_manager, mt5_connector: MT5Connector):
        self.logger = logger
        self.risk_manager = risk_manager
        self.trade_manager = trade_manager
        self.mt5 = mt5_connector # mt5_connector este o instanță a wrapper-ului tău

    def can_trade(self, verbose=False) -> bool:
        """Deleagă verificarea către RiskManager."""
        return self.risk_manager.can_trade(verbose=verbose)

    def get_historical_data(self, symbol: str, timeframe, count: int):
        """Obține date istorice și le returnează ca un pandas DataFrame."""
        rates = self.mt5.get_rates(symbol, timeframe, count) 
        if rates is None:
            self.logger.log(f"Eroare: get_rates a returnat None pentru {symbol} {timeframe}", "error")
            return None
        
        try:
            df = pd.DataFrame(rates)
            df['datetime'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('datetime', inplace=True)
            return df
        except Exception as e:
            self.logger.log(f"Eroare la conversia datelor în DataFrame: {e}", "error")
            return None

    def get_open_positions(self, symbol: str, magic_number: int):
        """Returnează pozițiile deschise filtrate după simbol și magic number."""
        all_positions = self.mt5.positions_get(symbol=symbol)
        if not all_positions:
            return []
        return [p for p in all_positions if p.magic == magic_number]

    def get_pending_orders(self, symbol: str, magic_number: int):
        """Returnează ordinele în așteptare filtrate după simbol și magic number."""
        all_orders = self.mt5.orders_get(symbol=symbol)
        if not all_orders:
            return []
        return [o for o in all_orders if o.magic == magic_number]
        
    def get_pip_size(self, symbol: str) -> float:
        """Pasează apelul către connectorul MT5."""
        return self.mt5.get_pip_size(symbol)

    def get_digits(self, symbol: str) -> int:
        """Pasează apelul către connectorul MT5 pentru a obține nr. de zecimale."""
        return self.mt5.get_digits(symbol)

    def open_market_order(self, symbol, order_type, lot, sl, tp, magic_number, comment=""):
        """Plasează un ordin la piață."""
        if not self.risk_manager.check_free_margin(symbol, lot, order_type):
            self.logger.log(f"BrokerContext: Marjă insuficientă pentru {symbol} lot={lot}.")
            return None
            
        return self.trade_manager.open_trade(
            symbol=symbol, order_type=order_type, lot=lot, sl=sl, tp=tp,
            deviation_points=self.trade_manager.trade_deviation,
            magic_number=magic_number, comment=comment
        )

    def place_pending_order(self, request: dict):
        """Plasează un ordin în așteptare (pending)."""
        return self.trade_manager.safe_order_send(request, f"pending order")

    def close_position(self, symbol: str, ticket: int, magic_number: int):
        """Închide o poziție existentă."""
        return self.trade_manager.close_trade(symbol, ticket, magic_number)

    def apply_trailing_stop(self, symbol, position, atr_price, pip, params):
        """Apelează logica de trailing stop din TradeManager."""
        self.trade_manager.apply_trailing(symbol, position, atr_price, pip, params)