import time
import logging
import yaml
import os
import threading
from datetime import datetime

from core.mt5_connector import MT5Connector
from managers.risk_manager import RiskManager
from managers.trade_manager import TradeManager
from core.logger import Logger

from strategies.pivot_strategy import PivotStrategy
from strategies.ma_ribbon_strategy import MARibbonStrategy


# === Setup logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class BotManager:
    def __init__(self, config_path="config/config.yaml"):
        self.config = self.load_config(config_path)
        self.symbols = self.config["general"]["symbols_forex"]
        self.logger = Logger()

        # MT5 Connector
        self.mt5 = MT5Connector(self.logger)
        if not self.mt5.initialize():
            raise RuntimeError("❌ MT5 init failed")

        # Managers
        self.risk_manager = RiskManager(self.config, self.logger, self.mt5)
        self.trade_manager = TradeManager(
            logger=self.logger,
            mt5_connector=self.mt5,
            magic_number=13930,
            trade_deviation=10,
            risk_manager=self.risk_manager
        )

        # Strategies
        self.strategies = self._load_strategies()

        # Threading
        self.strategy_threads = {}
        self.shutdown_event = threading.Event()
        self.thread_monitor_interval = 10
        self.thread_monitor_logging = self.config.get("general", {}).get("thread_monitor_logging", True)

    def load_config(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def _load_strategies(self):
        cfg = self.config["strategies"]
        strategies = []
        for symbol in self.symbols:
            # ensure symbol is selected
            self.mt5.symbol_select(symbol, True)
            if cfg.get("pivot", {}).get("enabled", False):
                strategies.append(PivotStrategy(symbol, cfg["pivot"], self.logger, self.risk_manager, self.trade_manager, self.mt5))
            if cfg.get("moving_average_ribbon", {}).get("enabled", False):
                strategies.append(MARibbonStrategy(symbol, cfg["moving_average_ribbon"], self.logger, self.risk_manager, self.trade_manager, self.mt5))
        return strategies

    def _create_strategy_thread(self, strategy):
        thread_id = f"{strategy.__class__.__name__}_{strategy.symbol}_{id(strategy)}"
        stop_event = threading.Event()
        strategy.stop_event = stop_event

        t = threading.Thread(target=strategy.run_threaded, name=thread_id, daemon=True)
        self.strategy_threads[thread_id] = {
            "thread": t,
            "strategy": strategy,
            "symbol": strategy.symbol,
            "stop_event": stop_event,
            "start_time": datetime.now(),
            "restart_count": 0,
        }
        t.start()
        self.logger.log(f"🚀 Thread started: {thread_id} (id={t.ident})")
        return thread_id

    def _start_all_strategy_threads(self):
        for s in self.strategies:
            self._create_strategy_thread(s)
        self.logger.log(f"✅ Started {len(self.strategy_threads)} strategy threads")

    def _monitor_threads(self, enable_logging=True):
        active, dead = [], []
        for thread_id, info in list(self.strategy_threads.items()):
            t = info["thread"]
            if t.is_alive():
                active.append(thread_id)
            else:
                dead.append(thread_id)
                if enable_logging:
                    self.logger.log(f"💀 Thread died: {thread_id}")
                del self.strategy_threads[thread_id]
                if not self.shutdown_event.is_set():
                    # restart
                    s = info["strategy"]
                    new_id = self._create_strategy_thread(s)
                    self.strategy_threads[new_id]["restart_count"] = info["restart_count"] + 1
        return active, dead

    def _stop_all_threads(self):
        self.logger.log(f"🛑 Stopping {len(self.strategy_threads)} threads...")
        for info in self.strategy_threads.values():
            info["stop_event"].set()
        for thread_id, info in self.strategy_threads.items():
            info["thread"].join(timeout=15)
            self.logger.log(f"✅ Stopped {thread_id}")
        self.strategy_threads.clear()

    def run(self):
        logger.info("🚀 Bot starting...")
        try:
            self._start_all_strategy_threads()
            cycle = 0
            while not self.shutdown_event.is_set():
                cycle += 1
                logger.info(f"🔄 === Monitor Cycle {cycle} ===")

                _ = self.risk_manager.can_trade(verbose=True)
                active, dead = self._monitor_threads(self.thread_monitor_logging)
                logger.info(f"🧵 Active={len(active)}, Dead={len(dead)}")
                if dead:
                    for d in dead:
                        logger.info(f"   - restarted {d}")

                if self.shutdown_event.wait(timeout=self.thread_monitor_interval):
                    break
        except KeyboardInterrupt:
            logger.info("🛑 KeyboardInterrupt")
        finally:
            logger.info("🏁 Shutting down...")
            self.shutdown_event.set()
            self._stop_all_threads()
            self.mt5.shutdown()
            logger.info("✅ Bot stopped.")


if __name__ == "__main__":
    bot = BotManager("config/config.yaml")
    bot.run()
