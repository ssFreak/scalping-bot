import threading
import traceback

class BaseStrategy:
    def __init__(self, symbol: str, config: dict, broker_context):
        self.symbol = symbol
        self.config = config
        self.broker = broker_context
        self.logger = self.broker.logger
        self.magic_number = int(self.config.get("magic_number", 0))
        self.stop_event = threading.Event()

        if self.magic_number == 0:
            self.logger.log(f"⚠️ Strategia pentru {self.symbol} nu are un magic_number valid!", "warning")

    def run_threaded(self):
        name = self.__class__.__name__
        self.logger.log(f"🧵 Start {name} {self.symbol}")
        try:
            while not self.stop_event.is_set():
                # === MODIFICARE PENTRU DEBUGGING ===
                # Adăugăm verbose=True pentru a obține loguri de diagnostic la fiecare verificare
                if self.broker.can_trade(verbose=False):
                    self.run_once()
                
                if self.stop_event.wait(timeout=5):
                    break
        except Exception as e:
            trace = traceback.format_exc()
            self.logger.log(f"❌ Eroare fatală în {name}({self.symbol}): {e}", "error")
            self.logger.log(f"🔍 Stack Trace: {trace}", "debug")
        finally:
            self.logger.log(f"🧵 Stop {name} {self.symbol}")

    def run_once(self):
        raise NotImplementedError("Subclasele trebuie să implementeze run_once()")

    def stop(self):
        self.stop_event.set()