import MetaTrader5 as mt5
import time
import logging
import yaml
import os
import threading
import queue
from datetime import datetime

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
        
        # Threading management
        self.strategy_threads = {}  # Dict to store thread info: {thread_id: {thread, strategy, symbol, stop_event}}
        self.shutdown_event = threading.Event()
        self.thread_monitor_interval = 10  # seconds
        self.thread_monitor_logging = self.config.get("general", {}).get("thread_monitor_logging", True)  # Default to True for backward compatibility
        
        self._load_strategies()

    def load_config(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def _load_strategies(self):
        cfg = self.config["strategies"]
        strategy_count = 0
    
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
                strategy_count += 1

    def _create_strategy_thread(self, strategy, symbol):
        """
        Creează și pornește un thread pentru o strategie+simbol specific.
        """
        strategy_name = strategy.__class__.__name__
        thread_id = f"{strategy_name}_{symbol}_{id(strategy)}"
        stop_event = threading.Event()
        
        # Update strategy's stop event
        strategy.stop_event = stop_event
        
        # Create thread
        thread = threading.Thread(
            target=strategy.run_threaded,
            args=(symbol,),
            name=thread_id,
            daemon=True  # Allow main program to exit even if threads are running
        )
        
        # Store thread info
        self.strategy_threads[thread_id] = {
            'thread': thread,
            'strategy': strategy,
            'symbol': symbol,
            'stop_event': stop_event,
            'strategy_name': strategy_name,
            'start_time': datetime.now(),
            'restart_count': 0
        }
        
        # Start thread
        thread.start()
        self.logger.log(f"🚀 Thread started: {thread_id} (Thread ID: {thread.ident})")
        
        return thread_id

    def _monitor_threads(self, enable_logging=True):
        """
        Monitorizează thread-urile active și repornește cele care au eșuat.
        
        Args:
            enable_logging (bool): Whether to enable detailed logging of thread monitoring activities
        """
        active_threads = []
        failed_threads = []
        
        for thread_id, thread_info in list(self.strategy_threads.items()):
            thread = thread_info['thread']
            strategy = thread_info['strategy']
            symbol = thread_info['symbol']
            
            if thread.is_alive():
                active_threads.append(f"{thread_id}(✅)")
            else:
                # Thread has died
                failed_threads.append(thread_id)
                if enable_logging:
                    self.logger.log(f"💀 Thread died: {thread_id}")
                
                # Remove dead thread
                del self.strategy_threads[thread_id]
                
                # Restart thread if not shutting down
                if not self.shutdown_event.is_set():
                    if enable_logging:
                        self.logger.log(f"🔄 Restarting thread: {thread_id}")
                    new_thread_id = self._create_strategy_thread(strategy, symbol)
                    if new_thread_id in self.strategy_threads:
                        self.strategy_threads[new_thread_id]['restart_count'] = thread_info['restart_count'] + 1
        
        return active_threads, failed_threads

    def _start_all_strategy_threads(self):
        """
        Pornește thread-uri pentru toate combinațiile strategie+simbol.
        """
        self.logger.log(f"🚀 Starting threads for all strategy+symbol combinations...")
        
        for strategy in self.strategies:
            symbol = strategy.symbol
            thread_id = self._create_strategy_thread(strategy, symbol)
            
        self.logger.log(f"✅ Started {len(self.strategy_threads)} strategy threads")

    def _stop_all_threads(self):
        """
        Oprește toate thread-urile în mod ordonat.
        """
        self.logger.log(f"🛑 Stopping all {len(self.strategy_threads)} strategy threads...")
        
        # Signal all threads to stop
        for thread_info in self.strategy_threads.values():
            thread_info['stop_event'].set()
            thread_info['strategy'].stop()
        
        # Wait for threads to finish
        timeout = 30  # seconds
        for thread_id, thread_info in self.strategy_threads.items():
            thread = thread_info['thread']
            self.logger.log(f"⏳ Waiting for thread {thread_id} to stop...")
            thread.join(timeout=timeout)
            
            if thread.is_alive():
                self.logger.log(f"⚠️ Thread {thread_id} did not stop gracefully within {timeout}s")
            else:
                self.logger.log(f"✅ Thread {thread_id} stopped successfully")
        
        self.strategy_threads.clear()
        self.logger.log(f"🏁 All strategy threads stopped")

    def _get_thread_status_report(self):
        """
        Generează un raport cu statusul tuturor thread-urilor (doar count-uri).
        """
        if not self.strategy_threads:
            return "No strategy threads running"
        
        active_count = 0
        failed_count = 0
        
        for thread_id, thread_info in self.strategy_threads.items():
            thread = thread_info['thread']
            
            if thread.is_alive():
                active_count += 1
            else:
                failed_count += 1
        
        return f"Active threads: {active_count}, Failed threads: {failed_count}, Total: {len(self.strategy_threads)}"

    def run(self):
        logger.info("🚀 Bot starting with threading support...")
        
        try:
            # Start all strategy threads
            self._start_all_strategy_threads()
            
            monitor_cycle_count = 0
            
            # Main monitoring loop
            while not self.shutdown_event.is_set():
                monitor_cycle_count += 1
                logger.info(f"🔄 === Monitor Cycle {monitor_cycle_count} Start ===")
                

                
                # Check trading status
                can_trade_status = self.risk_manager.can_trade(verbose=True)
                
                if not can_trade_status:
                    total_profit = self.trade_manager.get_today_total_profit()
                    is_market_open = is_forex_market_open()
                    logger.warning(f"⛔ Trading blocked - can_trade() returned False")
                    if not is_market_open:
                        logger.warning("⏸ Piața Forex este închisă, thread-urile continuă să ruleze dar nu vor deschide ordine.")
                    elif total_profit <= self.risk_manager.max_daily_loss:
                        logger.warning(
                            f"🚫 Max daily loss atins ({total_profit:.2f} <= {self.risk_manager.max_daily_loss:.2f}) - thread-urile continuă să ruleze fără a mai deschide ordine."
                        )
                    elif total_profit >= self.risk_manager.max_daily_profit:
                        logger.warning(
                            f"🎯 Daily profit target atins ({total_profit:.2f} >= {self.risk_manager.max_daily_profit:.2f}) - thread-urile continuă să ruleze fără a mai deschide ordine."
                        )
                    else:
                        logger.warning("❓ Trading blocked dintr-un motiv necunoscut.")
                else:
                    logger.info(f"✅ Trading allowed - {len(self.strategy_threads)} strategy threads active")

                # Monitor and manage threads
                active_threads, failed_threads = self._monitor_threads(self.thread_monitor_logging)
                
                if failed_threads:
                    logger.warning(f"💀 Failed threads in this cycle: {len(failed_threads)}")
                    for thread_id in failed_threads:
                        logger.warning(f"   - {thread_id}")
                
                # Thread status report
                thread_status = self._get_thread_status_report()
                logger.info(f"🧵 {thread_status}")
                
                # Wait before next monitoring cycle
                logger.info(f"⏳ Monitor cycle {monitor_cycle_count} complete - waiting {self.thread_monitor_interval} seconds...")
                
                # Use shutdown_event.wait() instead of time.sleep() for graceful shutdown
                if self.shutdown_event.wait(timeout=self.thread_monitor_interval):
                    logger.info("🛑 Shutdown signal received during wait")
                    break
                    
        except KeyboardInterrupt:
            logger.info("🛑 Keyboard interrupt received")
        except Exception as e:
            logger.error(f"❌ Fatal error in main loop: {e}")
        finally:
            logger.info("🏁 Bot shutdown initiated...")
            self.shutdown_event.set()
            self._stop_all_threads()
            logger.info("🏁 Bot shutdown complete")

# === MAIN ===
if __name__ == "__main__":
    bot = BotManager('config/config_scalping_bot.yaml')
    bot.run()
