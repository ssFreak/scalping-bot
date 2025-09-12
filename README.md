# Scalping Bot – MetaTrader 5

Acesta este un bot de scalping pentru **MetaTrader 5**, scris în Python, care implementează mai multe strategii (Pivot, Moving Average Ribbon etc.) și folosește un **sistem de risk management dinamic**.

---

## 🚀 Funcționalități

- **Conexiune unică la MT5** prin `MT5Connector` (singura interfață cu MetaTrader5).
- **Risk Management**:
  - `risk_per_trade` (% din equity la risc per tranzacție).
  - `max_position_lot` (limită maximă de lot).
  - `min_free_margin_ratio` (marjă liberă minimă).
  - limite zilnice de **profit** și **pierdere**.
- **Strategii incluse**:
  - **Pivot Strategy** (intrare pe nivele pivot, SL/TP pe ATR).
  - **Moving Average Ribbon Strategy** (alinierea SMA pentru trend).
- **Trade Manager**:
  - deschidere/închidere tranzacții.
  - trailing stop.
- **Config YAML** – toate setările sunt în `config.yaml`.

---

## 📂 Structură proiect

scalping-bot/
│── bot_manager.py # orchestratorul botului
│── config.yaml # fișierul de configurare
│── utils.py # indicatori auxiliari (ex: ATR)
│── README.md # acest fișier
│
├── core/
│ └── mt5_connector.py # singura interfață cu MetaTrader 5
│
├── managers/
│ ├── risk_manager.py # gestionează riscul și lot sizing-ul
│ └── trade_manager.py # gestionează ordine și trailing stop
│
└── strategies/
├── base_strategy.py
├── pivot_strategy.py
└── ma_ribbon_strategy.py

---

## ⚙️ Instalare

1. Instalează Python 3.10+  
2. Instalează dependențele:
   bash
   pip install MetaTrader5 pandas pyyaml
3. Asigură-te că MetaTrader 5 este instalat și conectat la un cont (demo sau real).

---

▶️ Rulare

Din directorul proiectului:

python bot_manager.py


Botul va:

1. Inițializa conexiunea cu MT5 (MT5Connector).
2. Încarcă simbolurile și strategiile din config.yaml.
3. Rulează strategiile și trimite ordine conform semnalelor.
   
---

🛠️ Configurare (config.yaml)

Exemplu:

general:
  symbols_forex:
    - EURUSD
    - GBPUSD
  daily_profit: 750.0
  daily_loss: 250.0
  risk_per_trade: 0.02
  max_position_lot: 0.3
  min_free_margin_ratio: 0.6
  trailing_profit_threshold: 15.0

strategies:
  pivot:
    enabled: true
    timeframe: M1
    atr_multiplier: 2.5
    atr_period: 14

  moving_average_ribbon:
    enabled: true
    timeframe: M5
    sma_periods: [5, 8, 13]
    atr_period: 14
    tp_atr_multiplier: 1.5
    sl_atr_multiplier: 2.5
	
---

📊 Roadmap

 Adăugare RSI + MACD ca filtre.

 Backtester integrat.

 Dashboard de monitorizare (Flask/Streamlit).

 Multi-timeframe confluence.
 

⚠️ DISCLAIMER

Acest bot este destinat testării pe conturi demo.
Utilizarea pe conturi reale se face pe propria răspundere.
Autorul nu își asumă responsabilitatea pentru eventuale pierderi financiare.
   