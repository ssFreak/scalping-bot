import threading
import time
import yaml
import os
import sys
import traceback
import signal

from core.logger import Logger
from core.mt5_connector import MT5Connector
from managers.risk_manager import RiskManager
from managers.trade_manager import TradeManager
from strategies.pivot_strategy import PivotStrategy
from strategies.ma_ribbon_strategy import MARibbonStrategy

class BotManager:
    def __init__(self, config_path="config/config.yaml"):
        self.config_path = config_path
        self.logger = Logger("logs/bot.log")

        self.logger.log("🚀 Initializare Scalping Bot...")

        # încarcă config
        self.config = self._load_config()

        # MT5Connector
        self.mt5 = MT5Connector(self.logger)
        if not self.mt5.initialize():
            self.logger.log("❌ Nu s-a putut inițializa MT5. Ieșire.")
            sys.exit(1)

        # TradeManager & RiskManager
        self.trade_manager = TradeManager(
            self.logger,
            magic_number=123456,
            trade_deviation=20,
            mt5=self.mt5,
        )
        self.risk_manager = RiskManager(self.config["general"], self.logger, self.trade_manager, self.mt5)

        # Strategii încărcate
        self.strategies = self._load_strategies()

        # Thread management
        self.threads = {}
        self.running = True
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _load_config(self):
        if not os.path.exists(self.config_path):
            print(f"Config file not found: {self.config_path}")
            sys.exit(1)
        with open(self.config_path, "r") as f:
            return yaml.safe_load(f)

    def _load_strategies(self):
        strategies_cfg = self.config.get("strategies", {})
        strategies = []

        for sym in self.config["general"]["symbols_forex"]:
            # selectăm simbolul în MT5
            self.mt5.symbol_select(sym, True)

            # Pivot strategy
            if strategies_cfg.get("pivot", {}).get("enabled", False):
                strategies.append(
                    PivotStrategy(
                        sym,
                        strategies_cfg["pivot"],
                        self.logger,
                        self.risk_manager,
                        self.trade_manager,
                        self.mt5
                    )
                )

            # MA Ribbon strategy
            if strategies_cfg.get("moving_average_ribbon", {}).get("enabled", False):
                strategies.append(
                    MARibbonStrategy(
                        sym,
                        strategies_cfg["moving_average_ribbon"],
                        self.logger,
                        self.risk_manager,
                        self.trade_manager,
                        self.mt5
                    )
                )

        return strategies

    def _strategy_thread(self, strategy, name):
        self.logger.log(f"▶️ Start thread {name}")
        while self.running:
            try:
                if self.risk_manager.can_trade(verbose=True):
                    strategy.run_once()
                else:
                    time.sleep(10)
                time.sleep(2)
            except Exception as e:
                trace = traceback.format_exc()
                self.logger.log(f"❌ Error in {name}: {e}")
                self.logger.log(f"🔍 {trace}")
                time.sleep(5)
        self.logger.log(f"🛑 Thread stop {name}")

    def start(self):
        self.logger.log("📈 Pornim strategiile...")
        for strat in self.strategies:
            name = f"{strat.__class__.__name__}_{strat.symbol}_{threading.get_ident()}"
            t = threading.Thread(target=self._strategy_thread, args=(strat, name), daemon=True)
            self.threads[name] = t
            t.start()

        self._monitor_threads()

    def _monitor_threads(self):
        self.logger.log("👀 Monitorizare thread-uri activată")
        while self.running:
            for name, thread in list(self.threads.items()):
                if not thread.is_alive() and self.running:
                    self.logger.log(f"🔄 Restarting thread: {name}")
                    parts = name.split("_")
                    class_name = parts[0]
                    symbol = parts[1]
                    # reconstruim strategia
                    strat = None
                    if class_name == "PivotStrategy":
                        strat = PivotStrategy(
                            symbol,
                            self.config["strategies"]["pivot"],
                            self.logger,
                            self.risk_manager,
                            self.trade_manager,
                            self.mt5
                        )
                    elif class_name == "MARibbonStrategy":
                        strat = MARibbonStrategy(
                            symbol,
                            self.config["strategies"]["moving_average_ribbon"],
                            self.logger,
                            self.risk_manager,
                            self.trade_manager,
                            self.mt5
                        )

                    if strat:
                        new_name = f"{class_name}_{symbol}_{threading.get_ident()}"
                        t = threading.Thread(target=self._strategy_thread, args=(strat, new_name), daemon=True)
                        self.threads[new_name] = t
                        t.start()
                        del self.threads[name]
            time.sleep(10)

    def _signal_handler(self, sig, frame):
        self.logger.log("🛑 Stop primit, închidem bot-ul...")
        self.running = False
        for name, thread in self.threads.items():
            thread.join(timeout=2)
        self.mt5.shutdown()
        sys.exit(0)

if __name__ == "__main__":
    bot = BotManager("config/config.yaml")
    bot.start()
