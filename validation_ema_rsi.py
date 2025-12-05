# validation_ema_rsi.py - IMPLEMENTARE COMPLETĂ (FIX: Nume fișier și Buclă Backtest)

import pandas as pd
import yaml
import sys

# Asigură-te că aceste importuri sunt corecte
from core.backtest_broker import BacktestBroker
from strategies.ema_rsi_scalper import EMARsiTrendScalper

# --- Funcțiile de pre-procesare (Rămân neschimbate) ---

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

# --- Funcția principală de rulare a backtest-ului (CORECTATĂ) ---
def run_validation_backtest(config, data_paths, symbol, strategy_class, strategy_name_key):
    
    # Extragem configurația corectă
    try:
        base_strategy_config = config['strategies'][strategy_name_key]
        symbol_config = base_strategy_config.get('symbol_settings', {}).get(symbol, {})
        
        final_config = {**base_strategy_config, **symbol_config}
    except KeyError:
        print(f"EROARE: Nu s-au găsit setări în config.yaml pentru strategia '{strategy_name_key}' sau simbolul '{symbol}'")
        sys.exit()
    
    # 1. Pre-procesăm datele
    print(f"--- Se rulează backtest-ul de validare pentru {strategy_name_key} pe {symbol} ---")
    
    try:
        if strategy_name_key == 'ema_rsi_scalper':
            h1_ema_period = final_config.get('h1_ema_period', 50)
            m5_atr_period = final_config.get('m5_atr_period', 14)
            m5_rsi_period = final_config.get('m5_rsi_period', 14)
            processed_data = preprocess_data_ema_rsi(data_paths, symbol, h1_ema_period, m5_atr_period, m5_rsi_period)
        else:
            print(f"EROARE: Nu există o funcție de pre-procesare definită pentru '{strategy_name_key}'")
            sys.exit()
            
    except Exception as e:
        print(f"EROARE FATALĂ la pre-procesarea datelor: {e}")
        sys.exit()

    # 2. Creăm brokerul (Portofoliu)
    # ‼️ FIX 1: Inițializare Broker fără 'processed_data' ‼️
    broker = BacktestBroker(config=config, initial_equity=200.0)
    
    # 3. Creăm strategia
    strategy = strategy_class(symbol=symbol, config=final_config, broker_context=broker)
    
    # 4. Rulăm simularea (Buclă Portofoliu)
    print(f"Se rulează simularea rapidă pe {len(processed_data)} bare...")
    
    # ‼️ FIX 2: Buclă de backtest corectă (simulare ticker) ‼️
    for index, bar_data in processed_data.iterrows():
        timestamp = index
        
        # 4a. Setăm datele curente în broker
        broker.set_current_data(timestamp, {symbol: bar_data})
        
        # 4b. Rulăm logica strategiei
        strategy.run_once(current_bar=bar_data) 
        
        # 4c. Actualizăm P/L-ul
        broker.update_all_positions()

    # 5. Generăm raportul final
    # ‼️ FIX 3: Apelarea funcției corecte generate_portfolio_report ‼️
    broker.generate_portfolio_report(
        symbols_tested=[symbol], 
        report_filename=f"validation_report_{strategy_name_key}_{symbol}_9Y.txt"
    )


if __name__ == "__main__":
    
    # 1. Încărcăm Configurația
    with open("config/config.yaml", 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    STRATEGY_TO_TEST = EMARsiTrendScalper
    STRATEGY_NAME_KEY = "ema_rsi_scalper"
    
    # 2. Extragem automat simbolurile ENABLED din config
    settings = config.get('strategies', {}).get(STRATEGY_NAME_KEY, {}).get('symbol_settings', {})
    active_symbols = [s for s, p in settings.items() if p.get('enabled', False)]
    
    print(f"🚀 Se pornește validarea extinsă (9 Ani) pentru {len(active_symbols)} simboluri: {active_symbols}")

    for symbol in active_symbols:
        print("\n" + "="*60)
        print(f"📊 Validare: {symbol}")
        print("="*60)
        
        # Construim căile pentru fișierele de 9 ani
        DATA_PATHS = {
            "M5": f"data/{symbol}_M5_9Y.csv",
            "H1": f"data/{symbol}_H1_9Y.csv"
        }
        
        # Verificăm dacă fișierele există înainte de a rula
        import os
        if not os.path.exists(DATA_PATHS['M5']) or not os.path.exists(DATA_PATHS['H1']):
            print(f"❌ SKIP {symbol}: Fișierele _9Y.csv lipsesc din folderul data/")
            continue

        try:
            run_validation_backtest(
                config=config,
                data_paths=DATA_PATHS,
                symbol=symbol,
                strategy_class=STRATEGY_TO_TEST,
                strategy_name_key=STRATEGY_NAME_KEY
            )
        except Exception as e:
            print(f"❌ Eroare la validarea {symbol}: {e}")

    print("\n✅ Validare completă pentru toate simbolurile active.")