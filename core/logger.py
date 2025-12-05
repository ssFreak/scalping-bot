# core/logger.py - IMPLEMENTARE COMPLETĂ (FIX: Schimbat la CSV pentru performanță I/O)

import os
import logging
from datetime import datetime
import pandas as pd
import sys 
import traceback # Adăugat pentru logging detaliat

class Logger:
    _console_handler_attached = False
    
    # Coloanele pe care le colectăm
    ML_COLUMNS = ['H1_trend_up', 'M5_rsi', 'M5_atr']
    BASE_COLUMNS = [
        "ticket", "symbol", "order_type", "lot_size", "entry_price", "sl", "tp",
        "comment", "entry_time", "exit_price", "exit_time", "closed"
    ]
    ALL_COLUMNS = BASE_COLUMNS + ML_COLUMNS # NOU: Definim coloanele totale

    def __init__(self, base_log_dir="bot.log"):
        self.base_log_dir = base_log_dir
        os.makedirs(self.base_log_dir, exist_ok=True)
        
        # ‼️ FIX 1.1: Schimbăm de la Excel la CSV pentru performanță ‼️
        self.positions_file = os.path.join(self.base_log_dir, "all_positions.csv")
        
        self.current_log_date = None
        self.session_dir = ""
        self.current_log_file = ""
        
        self.logger = logging.getLogger() 
        self.logger.setLevel(logging.INFO)
        
        # Configurare Handler Consolă (o singură dată)
        if not Logger._console_handler_attached:
            formatter = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            stream_handler = logging.StreamHandler(sys.stdout) 
            stream_handler.setFormatter(formatter)
            self.logger.addHandler(stream_handler)
            Logger._console_handler_attached = True 

        # Inițializăm fișierul de log text (care se va roti)
        self._check_and_rotate_log_file()
        
        # Inițializăm fișierul de poziții O SINGURĂ DATĂ
        self._initialize_positions_file()

    def _initialize_positions_file(self):
        """
        Verifică și inițializează fișierul 'all_positions.csv' o singură dată la pornire.
        """
        if not os.path.exists(self.positions_file):
            # ‼️ FIX 1.2: Inițializare ca CSV ‼️
            df = pd.DataFrame(columns=self.ALL_COLUMNS)
            df.to_csv(self.positions_file, index=False)
        else:
            # Verificăm dacă fișierul existent are noile coloane
            try:
                # ‼️ FIX 1.3: Citire ca CSV ‼️
                df = pd.read_csv(self.positions_file)
                needs_update = False
                for col in self.ML_COLUMNS:
                    if col not in df.columns:
                        df[col] = None 
                        needs_update = True
                if needs_update:
                    self.log(f"Actualizare 'all_positions.csv' cu noile coloane ML...", "info")
                    # ‼️ FIX 1.4: Scrierea ca CSV ‼️
                    df.to_csv(self.positions_file, index=False)
            except Exception as e:
                self.log(f"Eroare la verificarea coloanelor ML în {self.positions_file}: {e}", "error")

    def _check_and_rotate_log_file(self):
        """
        Verifică data curentă și rotește fișierul log.txt zilnic (Logică neschimbată).
        """
        today_str = datetime.now().strftime("%Y_%m_%d")
        
        if today_str == self.current_log_date:
            return

        try:
            # --- DATA S-A SCHIMBAT ---
            self.current_log_date = today_str
            self.session_dir = os.path.join(self.base_log_dir, today_str)
            os.makedirs(self.session_dir, exist_ok=True)
            
            new_log_file = os.path.join(self.session_dir, "log.txt")
            self.current_log_file = new_log_file

            old_handler = None
            for handler in self.logger.handlers:
                if isinstance(handler, logging.FileHandler):
                    old_handler = handler
                    break
            
            if old_handler:
                old_handler.close()
                self.logger.removeHandler(old_handler)

            formatter = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            file_handler = logging.FileHandler(new_log_file, encoding='utf-8', errors='replace') 
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            
            if old_handler: # Nu logăm la prima rulare
                self.log(f"--- S-a creat un nou fișier log.txt pentru ziua {today_str} ---", "info")

        except Exception as e:
            print(f"EROARE CRITICĂ ÎN LOGGER (rotire fișier): {e}")

    def log(self, message, level="info"):
        """Wrapper pentru a loga mesajul, verificând mai întâi data (Logică neschimbată)."""
        self._check_and_rotate_log_file() # Verificăm la fiecare apel
        
        if level == "info": self.logger.info(message)
        elif level == "warning": self.logger.warning(message)
        elif level == "error": self.logger.error(message)
        elif level == "debug": self.logger.debug(message)

    def log_position(
        self,
        ticket: int,
        symbol: str,
        order_type: str, # BUY/SELL/CLOSE
        lot_size: float = 0.0,
        entry_price: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        comment="",
        closed: bool = False,
        exit_price: float = None,
        ml_features: dict = None
    ):
        """
        Scrie în fișierul unic all_positions.csv. 
        ‼️ NOU: Folosim logica Fazei 1 (Open) / Fazei 2 (Close) pentru append rapid ‼️
        """
        if ml_features is None: ml_features = {}
        self._check_and_rotate_log_file() 
        
        try:
            # ‼️ FIX: Logare optimizată (doar append) ‼️
            
            if not closed:
                # FAZA 1: DESCHIDERE (Append rapid)
                new_row = {
                    "ticket": ticket, "symbol": symbol, "order_type": order_type,
                    "lot_size": lot_size, "entry_price": entry_price, "sl": sl, "tp": tp,
                    "comment": comment, 
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_price": None, "exit_time": None, 
                    "closed": False, # Explicit False pentru deschidere
                }
            else:
                # FAZA 2: ÎNCHIDERE (Append rapid, referință la ticket-ul original)
                new_row = {
                    # Copiază informațiile cheie de referință
                    "ticket": ticket, "symbol": symbol, "order_type": "CLOSE", 
                    "lot_size": lot_size, # Loghează lotul închis
                    
                    # Detalii de închidere
                    "entry_price": None, "sl": None, "tp": None, "comment": f"Close for {ticket}",
                    "entry_time": None, 
                    
                    "exit_price": exit_price, 
                    "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                    "closed": True, # Explicit True pentru închidere
                }
                
                # Setează ML Features la None pentru rândul de închidere
                for col in self.ML_COLUMNS:
                    new_row[col] = None 
                
            # Adaugă ML Features doar la rândul de deschidere
            if not closed:
                for col in self.ML_COLUMNS:
                    new_row[col] = ml_features.get(col, None)
            
            df = pd.DataFrame([new_row], columns=self.ALL_COLUMNS)
            
            # Scriere cu append (mult mai rapid decât rescrierea)
            header = not os.path.exists(self.positions_file)
            df.to_csv(self.positions_file, mode='a', header=header, index=False)
                
            if closed:
                 self.log(f"✅ Poziția {ticket} ({symbol}) închisă cu succes (Log append).", "info")

        except Exception as e:
            self.log(f"❌ Eroare la scrierea în {self.positions_file}: {e}", level="error")
            self.log(traceback.format_exc(), "debug")