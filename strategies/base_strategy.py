# strategies/base_strategy.py
class BaseStrategy:
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, bot_manager, stop_event=None):
        self.symbol = symbol
        self.config = config
        self.logger = logger
        self.risk_manager = risk_manager
        self.trade_manager = trade_manager
        self.bot_manager = bot_manager
        # event pentru oprire ordonata a strategiei
        self.stop_event = stop_event

    def generate_signal(self, df):
        """
        Returnează BUY / SELL / None pe baza datelor.
        Fiecare strategie trebuie să suprascrie această metodă.
        """
        raise NotImplementedError
