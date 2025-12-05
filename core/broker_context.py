# core/broker_context.py

import pandas as pd
from core.mt5_connector import MT5Connector

class LiveBrokerContext:
    def __init__(self, logger, risk_manager, trade_manager, mt5_connector: MT5Connector):
        self.logger = logger
        self.risk_manager = risk_manager
        self.trade_manager = trade_manager
        self.mt5 = mt5_connector

    def can_trade(self, verbose=False) -> bool:
        return self.risk_manager.can_trade(verbose=verbose)

    def get_historical_data(self, symbol: str, timeframe, count: int):
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
        """
        Returnează pozițiile deschise.
        FIX: Returnează None explicit dacă apare o eroare de comunicare cu MT5.
        """
        all_positions = self.mt5.positions_get(symbol=symbol)
        
        # Dacă MT5 returnează None, înseamnă eroare/deconectare.
        if all_positions is None: 
            return None 
            
        if not all_positions: 
            return []
            
        return [p for p in all_positions if p.magic == magic_number]

    def get_pending_orders(self, symbol: str, magic_number: int):
        all_orders = self.mt5.orders_get(symbol=symbol)
        if all_orders is None: return None
        if not all_orders: return []
        return [o for o in all_orders if o.magic == magic_number]
        
    def get_pip_size(self, symbol: str) -> float:
        return self.mt5.get_pip_size(symbol)

    def get_digits(self, symbol: str) -> int:
        return self.mt5.get_digits(symbol)

    def open_market_order(self, symbol, order_type, lot, sl, tp, magic_number, comment="", ml_features=None):
        if not self.risk_manager.check_free_margin(symbol, lot, order_type):
            self.logger.log(f"BrokerContext: Marjă insuficientă pentru {symbol} lot={lot}.")
            return None
            
        return self.trade_manager.open_trade(
            symbol=symbol, order_type=order_type, lot=lot, sl=sl, tp=tp,
            deviation_points=self.trade_manager.trade_deviation,
            magic_number=magic_number, comment=comment,
            ml_features=ml_features
        )

    def place_pending_order(self, request: dict):
        return self.trade_manager.safe_order_send(request, f"pending order")

    def close_position(self, symbol: str, ticket: int, magic_number: int):
        return self.trade_manager.close_trade(symbol, ticket, magic_number)

    def apply_trailing_stop(self, symbol, position, atr_price, pip, params):
        self.trade_manager.apply_trailing(symbol, position, atr_price, pip, params)