import logging
from datetime import datetime
import os

class Logger:
    def __init__(self, filename="trading_log.txt"):
        self.filename = os.path.join(os.getcwd(), filename) # Salvează log-ul în directorul curent
        
        # Configurează logger-ul de bază, doar dacă nu este deja configurat
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(message)s',
                handlers=[
                    logging.FileHandler(self.filename, encoding='utf-8'),
                    logging.StreamHandler()
                ]
            )
        self.logger = logging.getLogger()

    def log(self, message):
        """
        Înregistrează un mesaj. Va fi afișat în consolă și scris în fișier.
        """
        self.logger.info(message)
