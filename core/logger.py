# core/logger.py
import os
import logging
from datetime import datetime
import pandas as pd
import sys 

class Logger:
    _console_handler_attached = False
    
    # Coloanele pe care le colectăm
    ML_COLUMNS = ['H1_trend_up', 'M5_rsi', 'M5_atr']
    BASE_COLUMNS = [
        "ticket", "symbol", "order_type", "lot_size", "entry_price", "sl", "tp",
        "comment", "entry_time", "exit_price", "exit_time", "closed"
    ]

    def __init__(self, base_log_dir="bot.log"):
        self.base_log_dir = base_log_dir
        os.makedirs(self.base_log_dir, exist_ok=True)
        
        # --- MODIFICARE ---
        # 1. Fișierul de poziții este acum fix și în directorul de bază
        self.positions_file = os.path.join(self.base_log_dir, "all_positions.xlsx")
        # ------------------
        
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
        
        # Inițializăm fișierul Excel O SINGURĂ DATĂ
        self._initialize_positions_file()

    def _initialize_positions_file(self):
        """
        Verifică și inițializează fișierul 'all_positions.xlsx' o singură dată la pornire.
        """
        if not os.path.exists(self.positions_file):
            df = pd.DataFrame(columns=self.BASE_COLUMNS + self.ML_COLUMNS)
            df.to_excel(self.positions_file, index=False)
        else:
            # Verificăm dacă fișierul existent are noile coloane
            try:
                df = pd.read_excel(self.positions_file)
                needs_update = False
                for col in self.ML_COLUMNS:
                    if col not in df.columns:
                        df[col] = None 
                        needs_update = True
                if needs_update:
                    self.log(f"Actualizare 'all_positions.xlsx' cu noile coloane ML...", "info")
                    df.to_excel(self.positions_file, index=False)
            except Exception as e:
                self.log(f"Eroare la verificarea coloanelor ML în {self.positions_file}: {e}", "error")

    def _check_and_rotate_log_file(self):
        """
        Verifică data curentă. Dacă este o zi nouă, creează un nou
        folder și un nou FileHandler DOAR PENTRU log.txt.
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

            # --- MODIFICARE: Am scos logica positions.xlsx de aici ---
            
            if old_handler: # Nu logăm la prima rulare
                self.log(f"--- S-a creat un nou fișier log.txt pentru ziua {today_str} ---", "info")

        except Exception as e:
            print(f"EROARE CRITICĂ ÎN LOGGER (rotire fișier): {e}")

    def log(self, message, level="info"):
        """Wrapper pentru a loga mesajul, verificând mai întâi data."""
        self._check_and_rotate_log_file() # Verificăm la fiecare apel
        
        if level == "info": self.logger.info(message)
        elif level == "warning": self.logger.warning(message)
        elif level == "error": self.logger.error(message)
        elif level == "debug": self.logger.debug(message)

    def log_position(
        self,
        ticket: int,
        symbol: str,
        order_type: int,
        lot_size: float,
        entry_price: float,
        sl: float,
        tp: float,
        comment="",
        closed=False,
        exit_price=None,
        ml_features: dict = None
    ):
        """Scrie în fișierul unic all_positions.xlsx."""
        
        # Asigurăm că logul text este corect (pentru erori)
        self._check_and_rotate_log_file() 
        
        try:
            # Citim fișierul unic
            df = pd.read_excel(self.positions_file)

            if not closed:
                new_row = {
                    "ticket": ticket, "symbol": symbol, "order_type": order_type,
                    "lot_size": lot_size, "entry_price": entry_price, "sl": sl, "tp": tp,
                    "comment": comment, "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_price": None, "exit_time": None, "closed": False,
                }
                if ml_features:
                    for col in self.ML_COLUMNS:
                        new_row[col] = ml_features.get(col)
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                
            else:
                # Actualizăm linia existentă (la închidere)
                mask = (df["ticket"] == ticket) & (df["closed"] == False)
                if mask.any():
                    df.loc[mask, "exit_price"] = exit_price
                    df.loc[mask, "exit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    df.loc[mask, "closed"] = True
                else:
                    # Dacă tranzacția nu e găsită (poate a fost deschisă în altă zi), o adăugăm ca închisă
                    if not df[df['ticket'] == ticket].empty:
                        pass # Ticketul a fost deja închis (ex: la un restart)
                    else:
                        self.log(f"Ticketul {ticket} (închidere) nu a fost găsit în log. Se adaugă ca închis.", "warning")
                        new_row = {
                            "ticket": ticket, "symbol": symbol, "order_type": order_type,
                            "lot_size": lot_size, "entry_price": "UNKNOWN", "sl": sl, "tp": tp,
                            "comment": f"Closed ticket {ticket}", "entry_time": "UNKNOWN",
                            "exit_price": exit_price, "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "closed": True,
                        }
                        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

            df.to_excel(self.positions_file, index=False)
        except Exception as e:
            self.log(f"❌ Eroare la scrierea în {self.positions_file}: {e}", level="error")