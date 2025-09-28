import os
import logging
from datetime import datetime
import pandas as pd


class Logger:
    def __init__(self, base_log_dir="bot.log"):
        # directorul de bază
        os.makedirs(base_log_dir, exist_ok=True)

        # directorul zilei
        today = datetime.now().strftime("%Y_%m_%d")
        self.session_dir = os.path.join(base_log_dir, today)
        os.makedirs(self.session_dir, exist_ok=True)

        # fișiere log
        log_file = os.path.join(self.session_dir, "log.txt")
        positions_file = os.path.join(self.session_dir, "positions.xlsx")
        self.positions_file = positions_file

        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.logger = logging.getLogger()

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

    def log(self, message):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")
        self.logger.info(message)

    def log_position(
        self,
        ticket,
        symbol,
        order_type,
        lot_size,
        entry_price,
        sl,
        tp,
        comment="",
        closed=False,
        exit_price=None,
    ):
        """Scrie sau actualizează poziția în positions.xlsx"""
        try:
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

            df.to_excel(self.positions_file, index=False)
        except Exception as e:
            self.log(f"❌ Failed to log position: {e}")
