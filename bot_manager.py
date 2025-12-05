# managers/bot_manager.py - IMPLEMENTARE COMPLETĂ (Apel Trailing/Profit Lock)

import threading
import time
import signal
import sys
import yaml
import os

from core.mt5_connector import MT5Connector
from managers.trade_manager import TradeManager
from managers.risk_manager import RiskManager
from core.logger import Logger
from core.broker_context import LiveBrokerContext

from strategies.bb_scalper import BollingerReversionScalper
from strategies.ema_rsi_scalper import EMARsiTrendScalper
from strategies.base_strategy import BaseStrategy 

class BotManager:
    def __init__(self, config_path="config/config.yaml"):
        self.logger = Logger("bot.log")
        self.logger.log("🚀 Initializare Scalping Bot...")
        
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f: 
                self.config = yaml.safe_load(f)
        except FileNotFoundError: 
            self.logger.log(f"❌ Fișierul de configurare nu a fost găsit la '{config_path}'", "error"); sys.exit(1)
        except Exception as e: 
            self.logger.log(f"EROARE la citirea config.yaml: {e}"); sys.exit(1)

        self.mt5 = MT5Connector(self.logger)
        if not self.mt5.initialize(): 
            self.logger.log("❌ Nu s-a putut inițializa conexiunea MT5.", "error"); sys.exit(1)
        
        deviation = self.config.get("general", {}).get("deviation", 5)
        # Atenție: RiskManager și TradeManager se inițializează reciproc
        self.risk_manager = RiskManager(self.config, self.logger, None, self.mt5)
        self.trade_manager = TradeManager(self.logger, deviation, self.mt5, risk_manager=self.risk_manager) 
        self.risk_manager.trade_manager = self.trade_manager # Conexiune circulară finalizată
        
        self.live_broker_context = LiveBrokerContext(
            self.logger, self.risk_manager, self.trade_manager, self.mt5
        )

        self.strategy_instances = self._load_strategies()
        self.strategy_threads = []
        self.stop_event = threading.Event()

    def _load_strategies(self):
        """Încarcă strategiile (Logică neschimbată)."""
        instances = []
        strategy_configs = self.config.get("strategies", {})

        strategy_map = {
            "ema_rsi_scalper": EMARsiTrendScalper,
            "bb_range_scalper": BollingerReversionScalper
        }

        for strategy_name, base_config in strategy_configs.items():
            if not base_config.get("enabled", False) or strategy_name not in strategy_map:
                continue
                
            strategy_class = strategy_map[strategy_name]
            
            for symbol, symbol_specific_config in base_config.get("symbol_settings", {}).items():
                
                if not symbol_specific_config.get("enabled", False):
                    continue
                    
                self.logger.log(f"[*] Încărcare strategie '{strategy_name}' pentru simbolul {symbol}")
                
                final_symbol_config = base_config.copy()
                final_symbol_config.update(symbol_specific_config)
                
                base_magic = final_symbol_config.get("magic_number_base", 3000)
                offset_magic = final_symbol_config.get("magic_number_offset", 1)
                final_symbol_config['magic_number'] = base_magic + offset_magic
                
                if 'symbol_settings' in final_symbol_config:
                    del final_symbol_config['symbol_settings'] 
                
                instance = strategy_class(
                    symbol=symbol, 
                    config=final_symbol_config, 
                    broker_context=self.live_broker_context
                )
                instances.append(instance)
        
        return instances

    def start(self):
        """Pornește toate thread-urile strategiei și monitorul."""
        self.logger.log(f"▶️ Pornire {len(self.strategy_instances)} thread-uri pentru strategii...")
        for strategy in self.strategy_instances:
            thread = threading.Thread(target=strategy.run_threaded, daemon=True)
            self.strategy_threads.append(thread)
            thread.start()
            time.sleep(0.1)

        monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        monitor_thread.start()
        
        self.stop_event.wait()
        
        self.logger.log("Curățare finală...")
        for thread in self.strategy_threads: 
            thread.join(timeout=5)
        self.mt5.shutdown()
        self.logger.log("✅ Bot oprit complet.")

    def _monitor(self):
        """Thread separat pentru verificări globale și Sync."""
        while not self.stop_event.is_set():
            
            # 1. Verifică permisiunile de trading
            self.risk_manager.can_trade(verbose=True)
            
            # 2. Sincronizează tracker-ul intern cu realitatea (CRITIC pentru shadow accounting)
            # Asta rezolvă problema când pozițiile sunt închise de TP/SL pe server
            self.trade_manager.sync_internal_tracker()
            
            # 3. Trailing & Profit Lock
            open_positions = self.mt5.positions_get()
            if open_positions:
                for pos in open_positions:
                    self.trade_manager.apply_trailing(pos)
            
            # 4. Verificări de risc major
            if self.risk_manager.check_drawdown_breach():
                self.logger.log("‼️ LIMITĂ DRAWDOWN ATINSĂ! Oprire de urgență!", "error")
                self.trade_manager.close_all_trades()
                self.stop()
                break
            
            if self.risk_manager.check_for_rollover_closure():
                self.logger.log("🛑 WEEKEND! Se închid pozițiile și se oprește botul.")
                self.trade_manager.close_all_trades()
                self.stop()
                break

            if self.stop_event.wait(timeout=5): # Verificare la 5 secunde e suficient
                break

    def stop(self, *args):
        """Oprește toate strategiile și thread-ul principal."""
        if self.stop_event.is_set(): return
        self.logger.log("🛑 Oprire bot... Se trimite semnalul de stop către strategii.")
        for strategy in self.strategy_instances: 
            strategy.stop()
        self.stop_event.set()

if __name__ == "__main__":
    try:
        bot = BotManager("config/config.yaml") 
        signal.signal(signal.SIGINT, bot.stop)
        signal.signal(signal.SIGTERM, bot.stop)
        bot.start()
    except Exception as e:
        print(f"Eroare fatală la inițializarea BotManager: {e}")
        sys.exit(1)