import threading
import time
import signal
import sys
import yaml
from datetime import datetime

from core.mt5_connector import MT5Connector
from managers.trade_manager import TradeManager
from managers.risk_manager import RiskManager
from core.logger import Logger

# Strategii
from strategies.pivot_strategy import PivotStrategy
from strategies.ma_ribbon_strategy import MARibbonStrategy
from strategies.ema_breakout_strategy import EMABreakoutStrategy


class BotManager:
    def __init__(self, config_path="config/config.yaml"):
        self.logger = Logger("bot.log")
        self.logger.log("🚀 Initializare Scalping Bot...")

        # Load config
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        # === Conexiune MT5 ===
        self.mt5 = MT5Connector(self.logger)
        if not self.mt5.initialize():
            self.logger.log("❌ Nu s-a putut inițializa conexiunea MT5.")
            sys.exit(1)
        
        # === Manageri ===
        deviation = self.config.get("general").get("deviation", 5)
        self.trade_manager = TradeManager(self.config, self.logger, deviation, self.mt5)
        self.risk_manager = RiskManager(self.config, self.logger, self.trade_manager, self.mt5)
        
        # Injectăm risk_manager înapoi în trade_manager
        self.trade_manager.risk_manager = self.risk_manager

        # Threads
        self.threads = {}
        self.stop_event = threading.Event()

        # Strategii
        self.strategies = self._load_strategies()

    def _load_strategies(self):
        strategies = []
        forex_symbols = self.config["general"]["symbols_forex"]

        for symbol in forex_symbols:
            # Pivot Strategy
            if self.config["strategies"]["pivot"].get("enabled", False):
                strategies.append(
                    PivotStrategy(
                        symbol,
                        self.config["strategies"]["pivot"],
                        self.logger,
                        self.risk_manager,
                        self.trade_manager,
                        self.mt5,
                    )
                )

            # MA Ribbon Strategy
            if self.config["strategies"]["moving_average_ribbon"].get("enabled", False):
                strategies.append(
                    MARibbonStrategy(
                        symbol,
                        self.config["strategies"]["moving_average_ribbon"],
                        self.logger,
                        self.risk_manager,
                        self.trade_manager,
                        self.mt5,
                    )
                )

            # EMA Breakout Strategy (nouă)
            if self.config["strategies"].get("ema_breakout", {}).get("enabled", False):
                strategies.append(
                    EMABreakoutStrategy(
                        symbol,
                        self.config["strategies"]["ema_breakout"],
                        self.logger,
                        self.risk_manager,
                        self.trade_manager,
                        self.mt5,
                    )
                )

        return strategies

    def _strategy_thread(self, strategy):
        """Rulează o strategie într-un thread separat."""
        sym = strategy.symbol
        name = strategy.__class__.__name__

        self.logger.log(f"▶️ Start {name} pentru {sym}")

        while not self.stop_event.is_set():
            try:
                if self.risk_manager.can_trade(verbose=False):
                    strategy.run_once()
                else:
                    time.sleep(10)  # Dacă nu putem tranzacționa, luăm o pauză
            except Exception as e:
                self.logger.log(f"❌ Eroare în {name} {sym}: {e}")
            time.sleep(5)  # Delay între iterații

        self.logger.log(f"🛑 Oprit {name} pentru {sym}")

    def start(self):
        """Pornește toate strategiile."""
        for strat in self.strategies:
            t = threading.Thread(target=self._strategy_thread, args=(strat,), daemon=True)
            self.threads[strat.symbol + "_" + strat.__class__.__name__] = t
            t.start()
            time.sleep(1)

        self.logger.log("✅ Toate strategiile au fost pornite.")

        # Thread monitor
        monitor = threading.Thread(target=self._monitor_threads, daemon=True)
        monitor.start()

        # Signal handling
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        while not self.stop_event.is_set():
            time.sleep(1)

    def _monitor_threads(self):
        """Monitorizează thread-urile și le restartează dacă se opresc."""
        while not self.stop_event.is_set():
            for key, t in list(self.threads.items()):
                if not t.is_alive():
                    self.logger.log(f"🔄 Restart thread: {key}")
                    parts = key.split("_")
                    sym = parts[0]
                    strat_name = parts[1]
                    for strat in self.strategies:
                        if strat.symbol == sym and strat.__class__.__name__ == strat_name:
                            nt = threading.Thread(target=self._strategy_thread, args=(strat,), daemon=True)
                            self.threads[key] = nt
                            nt.start()
                            break
            time.sleep(30)

    def stop(self, *args):
        """Oprire bot."""
        self.logger.log("🛑 Oprire Scalping Bot...")
        self.stop_event.set()
        time.sleep(2)
        sys.exit(0)


if __name__ == "__main__":
    bot = BotManager("config/config.yaml")
    bot.start()
