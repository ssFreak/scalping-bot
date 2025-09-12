import threading
import time


class BaseStrategy:
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, mt5_connector, stop_event=None):
        self.symbol = symbol
        self.config = config
        self.logger = logger
        self.risk_manager = risk_manager
        self.trade_manager = trade_manager
        self.mt5 = mt5_connector
        self.stop_event = stop_event or threading.Event()

    def run_threaded(self):
        name = self.__class__.__name__
        self.logger.log(f"🧵 Start {name} {self.symbol}")
        try:
            while not self.stop_event.is_set():
                if self.risk_manager.can_trade(verbose=True):
                    self.run_once()
                # pace
                if self.stop_event.wait(timeout=5):
                    break
        except Exception as e:
            self.logger.log(f"❌ Fatal error in {name}({self.symbol}): {e}")
        finally:
            self.logger.log(f"🧵 Stop {name} {self.symbol}")

    def run_once(self):
        raise NotImplementedError("Subclasses must implement run_once()")

    def stop(self):
        self.stop_event.set()
