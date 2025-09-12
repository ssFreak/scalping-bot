import openpyxl
from datetime import datetime


class ExcelLogger:
    def __init__(self, file_path, logger=None):
        self.file_path = file_path
        self.logger = logger

        try:
            self.wb = openpyxl.load_workbook(self.file_path)
            self.ws = self.wb.active
        except FileNotFoundError:
            raise RuntimeError(f"⚠️ Fișierul jurnalului nu a fost găsit: {self.file_path}")

    def log_day(
        self,
        day,
        equity_start,
        equity_end,
        pnl,
        nr_trades,
        wins,
        losses,
        winrate,
        drawdown,
        rsi_blocks,
        macd_blocks,
        notes="",
    ):
        """Scrie rezultatele unei zile în jurnal."""
        try:
            row = [
                datetime.now().strftime("%Y-%m-%d"),
                equity_start,
                equity_end,
                pnl,
                nr_trades,
                wins,
                losses,
                winrate,
                drawdown,
                rsi_blocks,
                macd_blocks,
                notes,
            ]
            self.ws.append(row)
            self.wb.save(self.file_path)
            if self.logger:
                self.logger.log(f"📊 Rezultatele zilei {day} salvate în jurnal Excel.")
        except Exception as e:
            if self.logger:
                self.logger.log(f"❌ Eroare la scrierea în jurnal Excel: {e}")
