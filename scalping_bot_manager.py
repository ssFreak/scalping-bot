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
from core.utils import is_forex_market_open

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
    def __init__(self, config_path='config/config_scalping_bot.yaml'):
        self.config = self.load_config(config_path)
        self.symbols = self.config["general"]["symbols_forex"]
        self.logger = Logger()
        # Trade Manager
        self.trade_manager = TradeManager(logger=self.logger, magic_number=13930, trade_deviation=10, bot_manager=self)
        # Risk Manager
        self.risk_manager = RiskManager(self.config["general"], logger=self.logger, trade_manager=self.trade_manager)

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
    
        for symbol in self.symbols:
            if cfg.get("pivot", {}).get("enabled", False):
                self.strategies.append(PivotStrategy(
                    symbol,
                    cfg["pivot"],
                    self.logger,
                    self.risk_manager,
                    self.trade_manager,
                    self
                ))
    
            if cfg.get("moving_average_ribbon", {}).get("enabled", False):
                self.strategies.append(MARibbonStrategy(
                    symbol,
                    cfg["moving_average_ribbon"],
                    self.logger,
                    self.risk_manager,
                    self.trade_manager,
                    self
                ))
    
            if cfg.get("momentum_scalping", {}).get("enabled", False):
                self.strategies.append(MomentumStrategy(
                    symbol,
                    cfg["momentum_scalping"],
                    self.logger,
                    self.risk_manager,
                    self.trade_manager,
                    self
                ))
    
        self.logger.log(f"Strategiile au fost încărcate.")

    def run(self):
        logger.info("Bot pornit...")

        while True:
            if not self.risk_manager.can_trade():
                total_profit = self.trade_manager.get_today_total_profit()
                is_market_open = is_forex_market_open()
                if not is_market_open:
                    logger.warning("⏸ Piața Forex este închisă, botul nu poate deschide ordine.")
                elif total_profit <= self.risk_manager.max_daily_loss:
                    logger.warning(
                        f"🚫 Max daily loss atins ({total_profit:.2f} <= {self.risk_manager.max_daily_loss:.2f}) - botul continuă să ruleze fără a mai deschide ordine."
                    )
                elif total_profit >= self.risk_manager.max_daily_profit:
                    logger.warning(
                        f"🎯 Daily profit target atins ({total_profit:.2f} >= {self.risk_manager.max_daily_profit:.2f}) - botul continuă să ruleze fără a mai deschide ordine."
                    )
                else:
                    logger.warning("❓ Botul nu poate deschide ordine dintr-un motiv necunoscut.")
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
