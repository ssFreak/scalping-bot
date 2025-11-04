# Fișier: logger.py

import os
import logging
from datetime import datetime
import pandas as pd
import sys 
import os.path

class Logger:
    # 📌 FLAG la nivel de clasă: Asigură că handlerii sunt atașați doar o singură dată
    _handlers_attached = False

    def __init__(self, base_log_dir="bot.log"):
        # Directorul de bază și al zilei
        os.makedirs(base_log_dir, exist_ok=True)
        today = datetime.now().strftime("%Y_%m_%d")
        self.session_dir = os.path.join(base_log_dir, today)
        os.makedirs(self.session_dir, exist_ok=True)
        
        # Fișiere log
        log_file = os.path.join(self.session_dir, "log.txt")
        positions_file = os.path.join(self.session_dir, "positions.xlsx")
        self.positions_file = positions_file

        # Obținem logger-ul rădăcină (root logger)
        self.logger = logging.getLogger() 
        self.logger.setLevel(logging.INFO)
        
        # Configurare Atomică a Handler-ilor
        if not Logger._handlers_attached:
            
            # 1. Eliminăm toți Handlerii existenți 
            for handler in list(self.logger.handlers):
                self.logger.removeHandler(handler)

            # 2. Format
            formatter = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            
            # 3. Handler Fișier (cu corecțiile de encoding)
            file_handler = logging.FileHandler(log_file, encoding='utf-8', errors='replace') 
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            
            # 4. Handler Consolă (StreamHandler simplu)
            stream_handler = logging.StreamHandler(sys.stdout) 
            stream_handler.setFormatter(formatter)
            self.logger.addHandler(stream_handler)
            
            # Setăm flag-ul
            Logger._handlers_attached = True 
        else:
            # Logger-ul a fost deja configurat. Verificăm și actualizăm doar FileHandler-ul.
            # Convertim calea la absolut pentru comparație sigură
            abs_log_file = os.path.abspath(log_file)
            
            file_handler_exists = False
            for handler in list(self.logger.handlers):
                if isinstance(handler, logging.FileHandler):
                    file_handler_exists = True
                    # Verificăm dacă fișierul de log este cel corect pentru sesiunea curentă
                    if handler.baseFilename != abs_log_file:
                         # Este o sesiune nouă (sau fișier diferit). Închidem și înlocuim.
                         handler.close() 
                         self.logger.removeHandler(handler)
                         
                         # Re-adăugăm handler-ul cu noul fișier de log
                         formatter = logging.Formatter(
                            fmt="%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S",
                         )
                         new_file_handler = logging.FileHandler(abs_log_file, encoding='utf-8', errors='replace') 
                         new_file_handler.setFormatter(formatter)
                         self.logger.addHandler(new_file_handler)
                         break # Am actualizat FileHandler-ul.

            # Dacă dintr-un motiv FileHandler-ul nu mai există, îl adăugăm
            if not file_handler_exists:
                formatter = logging.Formatter(
                    fmt="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
                file_handler = logging.FileHandler(abs_log_file, encoding='utf-8', errors='replace') 
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)


        # inițializăm fișierul Excel dacă nu există
        if not os.path.exists(self.positions_file):
            df = pd.DataFrame(
                columns=[
                    "ticket",
                    "symbol",
                    "order_type",
                    "lot_size",
                    "entry_price",
                    "sl",
                    "tp",
                    "comment",
                    "entry_time",
                    "exit_price",
                    "exit_time",
                    "closed",
                ]
            )
            df.to_excel(self.positions_file, index=False)


    def log(self, message, level="info"):
        """Wrapper pentru a loga mesajul la nivelul specificat."""
        if level == "info":
            self.logger.info(message)
        elif level == "warning":
            self.logger.warning(message)
        elif level == "error":
            self.logger.error(message)
        elif level == "debug":
            self.logger.debug(message)


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
    ):
        """Scrie sau actualizează poziția în positions.xlsx"""
        try:
            # Citim fișierul (necesar pentru a evita problemele de scriere concurentă)
            df = pd.read_excel(self.positions_file)

            if not closed:
                # ✅ adăugăm o linie nouă
                new_row = {
                    "ticket": ticket,
                    "symbol": symbol,
                    "order_type": order_type,
                    "lot_size": lot_size,
                    "entry_price": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "comment": comment,
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_price": None,
                    "exit_time": None,
                    "closed": False,
                }
                # Folosim pd.concat pentru a adăuga rândul nou
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                
            else:
                # 🔄 actualizăm linia existentă
                mask = df["ticket"] == ticket
                if mask.any():
                    df.loc[mask, "exit_price"] = exit_price
                    df.loc[mask, "exit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    df.loc[mask, "closed"] = True
                else:
                    self.log(f"⚠️ Tried to close ticket {ticket}, but not found in Excel")

            # Scriem întregul DataFrame înapoi în fișier
            df.to_excel(self.positions_file, index=False)
        except Exception as e:
            self.log(f"❌ Error writing to positions.xlsx: {e}", level="error")

