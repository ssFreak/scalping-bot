# validate.py
# 
# ACESTA ESTE SCRIPTUL UNIVERSAL DE VALIDARE (Backtest Unic)
# 
# Cum se folosește:
# 1. Asigură-te că fișierul config.yaml conține parametrii optimi pentru simbolul dorit.
# 2. Asigură-te că ai datele (ex: EURUSD_M5_5Y.csv) în folderul /data.
# 3. Modifică variabila 'SYMBOL_TO_TEST' de mai jos.
# 4. Rulează: python validate.py
#
import pandas as pd
import yaml
import sys

from core.backtest_broker import BacktestBroker
from strategies.ema_rsi_scalper import EMARsiTrendScalper
# Adaugă aici importuri pentru alte strategii pe măsură ce le validezi
# from strategies.pivot_strategy import PivotStrategy

# --- Funcțiile de pre-procesare (Copiate din optimizatoare) ---

def load_and_prepare_data(file_path):
    """Încarcă și pregătește un fișier de date (format MT5 Tab)."""
    try:
        data = pd.read_csv(file_path, header=0, sep='\t')
        data.rename(columns={'<DATE>': 'date', '<TIME>': 'time', '<OPEN>': 'open', '<HIGH>': 'high', '<LOW>': 'low', '<CLOSE>': 'close'}, inplace=True)
        data['datetime'] = pd.to_datetime(data['date'] + ' ' + data['time'], format='%Y.%m.%d %H:%M:%S')
        data.set_index('datetime', inplace=True)
        data.index = data.index.tz_localize(None)
        return data[['open', 'high', 'low', 'close']].copy()
    except Exception as e:
        print(f"EROARE la încărcarea {file_path}: {e}")
        return None

def preprocess_data_ema_rsi(data_paths, symbol, h1_ema_period, m5_atr_period, m5_rsi_period):
    """Funcție de pre-procesare specifică pentru EMARsiTrendScalper."""
    print("[INFO] Început pre-procesare pentru EMARsiTrendScalper...")
    df_m5 = load_and_prepare_data(data_paths['M5'])
    df_h1 = load_and_prepare_data(data_paths['H1'])
    if df_m5 is None or df_h1 is None:
        raise FileNotFoundError(f"Datele M5 sau H1 nu au putut fi încărcate pentru {symbol}")

    print(f"[INFO] Calculare indicatori H1 (EMA {h1_ema_period})...")
    df_h1['H1_ema_trend'] = df_h1['close'].ewm(span=h1_ema_period, adjust=False).mean()
    df_h1['H1_trend_up'] = df_h1['close'] > df_h1['H1_ema_trend']
    df_h1_to_merge = df_h1[['H1_trend_up']] 

    print(f"[INFO] Calculare indicatori M5 (RSI {m5_rsi_period}, ATR {m5_atr_period})...")
    df_m5['M5_atr'] = EMARsiTrendScalper._calculate_atr(df_m5, m5_atr_period, 'ema')
    df_m5['M5_rsi'] = EMARsiTrendScalper._calculate_rsi(df_m5, m5_rsi_period)
    
    print("[INFO] Unire date M5 și H1...")
    combined_df = pd.merge_asof(
        df_m5,
        df_h1_to_merge,
        left_index=True,
        right_index=True,
        direction='backward'
    )
    combined_df.dropna(inplace=True)
    print("[INFO] Pre-procesare finalizată.")
    return combined_df

# --- Funcția principală de rulare a backtest-ului ---
def run_validation_backtest(config, data_paths, symbol, strategy_class, strategy_name_key):
    
    # Extragem configurația corectă
    try:
        base_strategy_config = config['strategies'][strategy_name_key]
        symbol_config = base_strategy_config.get('symbol_settings', {}).get(symbol, {})
        
        # Combinăm configurația de bază cu cea specifică simbolului
        final_config = {**base_strategy_config, **symbol_config}
    except KeyError:
        print(f"EROARE: Nu s-au găsit setări în config.yaml pentru strategia '{strategy_name_key}' sau simbolul '{symbol}'")
        sys.exit()
    
    # 1. Pre-procesăm datele (logica trebuie să fie specifică strategiei)
    print(f"--- Se rulează backtest-ul de validare pentru {strategy_name_key} pe {symbol} ---")
    
    try:
        if strategy_name_key == 'ema_rsi_scalper':
            h1_ema_period = final_config.get('h1_ema_period', 50)
            m5_atr_period = final_config.get('m5_atr_period', 14)
            m5_rsi_period = final_config.get('m5_rsi_period', 14)
            processed_data = preprocess_data_ema_rsi(data_paths, symbol, h1_ema_period, m5_atr_period, m5_rsi_period)
        
        # --- (Poți adăuga 'elif' pentru alte strategii în viitor) ---
        # elif strategy_name_key == 'pivot':
        #     processed_data = preprocess_data_pivot(...) 
            
        else:
            print(f"EROARE: Nu există o funcție de pre-procesare definită pentru '{strategy_name_key}'")
            sys.exit()
            
    except Exception as e:
        print(f"EROARE FATALĂ la pre-procesarea datelor: {e}")
        sys.exit()

    # 2. Creăm brokerul
    broker = BacktestBroker(
        processed_data=processed_data, 
        config=config, 
        initial_equity=200.0
    )
    
    # 3. Creăm strategia
    strategy = strategy_class(symbol=symbol, config=final_config, broker_context=broker)
    
    # 4. Rulăm simularea
    print(f"Se rulează simularea rapidă pe {len(processed_data)} bare...")
    while broker.advance_time():
        strategy.run_once()

    # 5. Generăm raportul final
    broker.generate_report(report_filename=f"validation_report_{strategy_name_key}_{symbol}.txt")


if __name__ == "__main__":
    
    # --- EDITEAZĂ AICI ---
    SYMBOL_TO_TEST = "NZDUSD"
    STRATEGY_TO_TEST = EMARsiTrendScalper
    STRATEGY_NAME_KEY = "ema_rsi_scalper" # Numele exact din config.yaml
    # ---------------------
    
    with open("config/config.yaml", 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    print(f"Se folosesc parametrii din 'config/config.yaml' pentru {STRATEGY_NAME_KEY} -> {SYMBOL_TO_TEST}")

    # Folosim noua ta convenție de nume de fișiere
    DATA_PATHS = {
        "M5": f"data/{SYMBOL_TO_TEST}_M5_5Y.csv",
        "H1": f"data/{SYMBOL_TO_TEST}_H1_5Y.csv"
    }

    run_validation_backtest(
        config=config,
        data_paths=DATA_PATHS,
        symbol=SYMBOL_TO_TEST,
        strategy_class=STRATEGY_TO_TEST,
        strategy_name_key=STRATEGY_NAME_KEY
    )