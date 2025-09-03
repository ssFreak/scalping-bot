import MetaTrader5 as mt5
import time
import logging
import yaml
import os

from strategies.pivot_strategy import PivotStrategy
from strategies.ma_ribbon_strategy import MARibbonStrategy
from strategies.momentum_strategy import MomentumStrategy
from core.risk_manager import RiskManager
from core.trade_manager import TradeManager
from core.logger import Logger

# === Setup logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class BotManager:
    def __init__(self, config_path='config/config_scalping_bot.yaml'):
        self.config = self.load_config(config_path)
        self.symbols = self.config["general"]["symbols_forex"]
        self.logger = Logger()

        # Risk Manager
        self.risk_manager = RiskManager(self.config["general"], logger=self.logger)
        self.trade_manager = TradeManager(logger=self.logger, magic_number=13930, trade_deviation=10, bot_manager=self)

        # Strategii active
        self.strategies = []
        self._load_strategies()

    def load_config(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def _load_strategies(self):
        cfg = self.config["strategies"]

        if cfg.get("pivot", {}).get("enabled", False):
            self.strategies.append(PivotStrategy(cfg["pivot"], risk_manager=self.risk_manager, tra))

        if cfg.get("moving_average_ribbon", {}).get("enabled", False):
            self.strategies.append(MARibbonStrategy(cfg["moving_average_ribbon"], self.risk_manager))

        if cfg.get("momentum_scalping", {}).get("enabled", False):
            self.strategies.append(MomentumScalpingStrategy(cfg["momentum_scalping"], self.risk_manager))

        logger.info(f"Strategii încărcate: {[s.__class__.__name__ for s in self.strategies]}")

    def run(self):
        logger.info("Bot pornit...")

        while True:
            if not self.risk_manager.can_trade():
                logger.warning("🚫 Max daily loss atins - botul continuă să ruleze fără a mai deschide ordine.")
                time.sleep(60)
                continue

            for symbol in self.symbols:
                for strategy in self.strategies:
                    try:
                        strategy.run(symbol)
                    except Exception as e:
                        logger.error(f"Eroare la strategia {strategy.__class__.__name__} pentru {symbol}: {e}")

            time.sleep(5)  # Delay între cicluri

# === MAIN ===
if __name__ == "__main__":
    bot = BotManager('config/config_scalping_bot.yaml')
    bot.run()
