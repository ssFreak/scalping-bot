# strategies/base_strategy.py
import threading
import time

class BaseStrategy:
    def __init__(self, symbol, config, logger, risk_manager, trade_manager, bot_manager, stop_event=None):
        self.symbol = symbol
        self.config = config
        self.logger = logger
        self.risk_manager = risk_manager
        self.trade_manager = trade_manager
        self.bot_manager = bot_manager
        # event pentru oprire ordonata a strategiei
        self.stop_event = stop_event or threading.Event()
        self.is_running = False

    def generate_signal(self, df):
        """
        Returnează BUY / SELL / None pe baza datelor.
        Fiecare strategie trebuie să suprascrie această metodă.
        """
        raise NotImplementedError
    
    def run_threaded(self, symbol=None):
        """
        Thread-safe wrapper pentru run method.
        Această metodă va fi apelată în thread-uri separate.
        """
        active_symbol = symbol if symbol is not None else self.symbol
        strategy_name = self.__class__.__name__
        thread_id = threading.current_thread().ident
        
        self.logger.log(f"🧵 Thread {thread_id}: Starting {strategy_name} for {active_symbol}")
        self.is_running = True
        
        try:
            # Call the strategy's run method in a controlled loop
            while not self.stop_event.is_set() and self.risk_manager.can_trade():
                #self.logger.log(f"🔍 [DEBUG] Loop activ pentru {strategy_name} pe {active_symbol}")
                try:
                    # Call the actual strategy implementation
                    self.run_once(active_symbol)
                    
                    # Check if we should stop
                    if self.stop_event.wait(timeout=5):  # 5 second intervals
                        break
                        
                except Exception as e:
                    self.logger.log(f"❌ Thread {thread_id}: Error in {strategy_name}({active_symbol}): {e}")
                    # Wait before retrying
                    if self.stop_event.wait(timeout=30):
                        break
                        
        except Exception as e:
            self.logger.log(f"❌ Thread {thread_id}: Fatal error in {strategy_name}({active_symbol}): {e}")
        finally:
            self.is_running = False
            self.logger.log(f"🧵 Thread {thread_id}: Stopped {strategy_name} for {active_symbol}")
    
    def run_once(self, symbol=None):
        """
        Execută o singură iterație de strategie.
        Subclasele trebuie să suprascrie această metodă în locul run().
        """
        raise NotImplementedError("Subclasses must implement run_once method")
    
    def stop(self):
        """
        Oprește strategia în mod ordonat.
        """
        self.logger.log(f"🛑 Stopping {self.__class__.__name__} for {self.symbol}")
        self.stop_event.set()
