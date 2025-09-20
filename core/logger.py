import os
import logging
from datetime import datetime

class Logger:
    def __init__(self, log_file="bot.log"):
        log_dir = os.path.dirname(log_file)
        if log_dir:  # doar dacă avem director
            os.makedirs(log_dir, exist_ok=True)

        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.logger = logging.getLogger()

    def log(self, message):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")
        self.logger.info(message)
