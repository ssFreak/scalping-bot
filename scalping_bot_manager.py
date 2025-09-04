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
        strategy_count = 0
        
        self.logger.log(f"🔄 Loading strategies for symbols: {self.symbols}")
    
        for symbol in self.symbols:
            symbol_strategies = []
            
            if cfg.get("pivot", {}).get("enabled", False):
                self.strategies.append(PivotStrategy(
                    symbol,
                    cfg["pivot"],
                    self.logger,
                    self.risk_manager,
                    self.trade_manager,
                    self
                ))
                symbol_strategies.append("PivotStrategy")
                strategy_count += 1
    
            if cfg.get("moving_average_ribbon", {}).get("enabled", False):
                self.strategies.append(MARibbonStrategy(
                    symbol,
                    cfg["moving_average_ribbon"],
                    self.logger,
                    self.risk_manager,
                    self.trade_manager,
                    self
                ))
                symbol_strategies.append("MARibbonStrategy")
                strategy_count += 1
    
            if cfg.get("momentum_scalping", {}).get("enabled", False):
                self.strategies.append(MomentumStrategy(
                    symbol,
                    cfg["momentum_scalping"],
                    self.logger,
                    self.risk_manager,
                    self.trade_manager,
                    self
                ))
                symbol_strategies.append("MomentumStrategy")
                strategy_count += 1
            
            self.logger.log(f"📈 {symbol}: Loaded {len(symbol_strategies)} strategies: {', '.join(symbol_strategies)}")
    
        self.logger.log(f"✅ Total strategies loaded: {strategy_count} ({len(self.strategies)} strategy instances)")
        
        # Log strategy configuration status
        enabled_strategies = []
        for strategy_name, strategy_config in cfg.items():
            if isinstance(strategy_config, dict) and strategy_config.get("enabled", False):
                enabled_strategies.append(strategy_name)
        
        self.logger.log(f"⚙️ Enabled strategy types in config: {', '.join(enabled_strategies) if enabled_strategies else 'None'}")

    def run(self):
        logger.info("Bot pornit...")
        cycle_count = 0

        while True:
            cycle_count += 1
            logger.info(f"🔄 === Cycle {cycle_count} Start ===")
            
            # Detailed logging of loaded strategies
            strategy_summary = {}
            for strategy in self.strategies:
                strategy_name = strategy.__class__.__name__
                strategy_symbol = getattr(strategy, 'symbol', 'Unknown')
                if strategy_name not in strategy_summary:
                    strategy_summary[strategy_name] = []
                strategy_summary[strategy_name].append(strategy_symbol)
            
            logger.info(f"📊 Loaded strategies: {dict(strategy_summary)}")
            logger.info(f"🎯 Target symbols: {self.symbols}")
            
            # Check can_trade with detailed logging
            can_trade_status = self.risk_manager.can_trade(verbose=True)
            
            if not can_trade_status:
                total_profit = self.trade_manager.get_today_total_profit()
                is_market_open = is_forex_market_open()
                logger.warning(f"⛔ Trading blocked - can_trade() returned False")
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
                logger.info(f"⏳ Waiting 60 seconds before next cycle...")
                time.sleep(60)
                continue

            # Strategy execution with detailed logging
            logger.info(f"✅ Trading allowed - executing strategies for {len(self.symbols)} symbols")
            execution_summary = []
            
            for symbol in self.symbols:
                symbol_strategies = [s for s in self.strategies if getattr(s, 'symbol', None) == symbol]
                logger.info(f"🔧 Processing symbol {symbol} with {len(symbol_strategies)} strategies: {[s.__class__.__name__ for s in symbol_strategies]}")
                
                for strategy in self.strategies:
                    strategy_name = strategy.__class__.__name__
                    strategy_symbol = getattr(strategy, 'symbol', 'Unknown')
                    
                    try:
                        logger.info(f"▶️ Calling {strategy_name}.run({symbol}) (strategy configured for {strategy_symbol})")
                        
                        # Check if this strategy has infinite loop behavior
                        import inspect
                        strategy_run_source = inspect.getsource(strategy.run)
                        has_while_loop = "while True" in strategy_run_source
                        
                        if has_while_loop:
                            logger.warning(f"⚠️ {strategy_name} contains 'while True' loop - may block other strategies!")
                        
                        strategy.run(symbol)
                        execution_summary.append(f"{strategy_name}({symbol}): ✅")
                        logger.info(f"✅ {strategy_name}.run({symbol}) returned normally")
                    except Exception as e:
                        execution_summary.append(f"{strategy_name}({symbol}): ❌ {str(e)[:50]}")
                        logger.error(f"❌ Eroare la strategia {strategy_name} pentru {symbol}: {e}")
                        logger.error(f"🔍 Strategy {strategy_name} failed, continuing with next strategy...")

            # Cycle summary
            logger.info(f"📋 Cycle {cycle_count} Summary: {'; '.join(execution_summary)}")
            logger.info(f"⏳ Cycle {cycle_count} complete - waiting 5 seconds...")
            time.sleep(5)  # Delay între cicluri

# === MAIN ===
if __name__ == "__main__":
    bot = BotManager('config/config_scalping_bot.yaml')
    bot.run()
