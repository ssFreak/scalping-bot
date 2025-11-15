# portfolio_validator.py
import pandas as pd
import yaml
import sys
from datetime import datetime

from core.backtest_broker import BacktestBroker
from strategies.ema_rsi_scalper import EMARsiTrendScalper
# Adaugă aici și alte clase de strategii dacă vrei să le incluzi

# --- Funcțiile de pre-procesare (Copiate) ---
def load_and_prepare_data(file_path):
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
    print(f"[INFO] Pre-procesare EMARsiTrendScalper pentru {symbol}...")
    df_m5 = load_and_prepare_data(data_paths['M5'])
    df_h1 = load_and_prepare_data(data_paths['H1'])
    if df_m5 is None or df_h1 is None:
        raise FileNotFoundError(f"Datele M5 sau H1 nu au putut fi încărcate pentru {symbol}")

    df_h1['H1_ema_trend'] = df_h1['close'].ewm(span=h1_ema_period, adjust=False).mean()
    df_h1['H1_trend_up'] = df_h1['close'] > df_h1['H1_ema_trend']
    df_h1_to_merge = df_h1[['H1_trend_up']] 
    df_m5['M5_atr'] = EMARsiTrendScalper._calculate_atr(df_m5, m5_atr_period, 'ema')
    df_m5['M5_rsi'] = EMARsiTrendScalper._calculate_rsi(df_m5, m5_rsi_period)
    combined_df = pd.merge_asof(df_m5, df_h1_to_merge, left_index=True, right_index=True, direction='backward')
    combined_df.dropna(inplace=True)
    return combined_df
# --- Sfârșit pre-procesare ---

def run_portfolio_backtest(config, symbols_to_test, strategy_class, strategy_name_key):
    
    # Capitalul total al portofoliului
    initial_equity = config.get('general', {}).get('portfolio_initial_equity', 1000.0)
    
    all_data = {} # Stocăm toate datele pre-procesate
    all_strategies = {} # Stocăm instanțele strategiilor
    
    # 1. Creăm un singur broker pentru portofoliu
    broker = BacktestBroker(config=config, initial_equity=initial_equity)
    
    # 2. Încărcăm datele și creăm strategiile
    master_index = None
    
    for symbol in symbols_to_test:
        print("\n" + "="*30)
        print(f"Procesare simbol: {symbol}")
        print("="*30)
        
        try:
            base_strategy_config = config['strategies'][strategy_name_key]
            symbol_config = base_strategy_config.get('symbol_settings', {}).get(symbol, {})
            final_config = {**base_strategy_config, **symbol_config}
        except KeyError:
            print(f"EROARE: Nu s-au găsit setări în config pentru '{strategy_name_key}' -> '{symbol}'")
            continue

        data_paths = {
            "M5": f"data/{symbol}_M5_5Y.csv",
            "H1": f"data/{symbol}_H1_5Y.csv"
        }
        
        try:
            # Obținem datele pre-procesate pentru acest simbol
            h1_ema_period = final_config.get('h1_ema_period', 50)
            m5_atr_period = final_config.get('m5_atr_period', 14)
            m5_rsi_period = final_config.get('m5_rsi_period', 14)
            processed_data = preprocess_data_ema_rsi(data_paths, symbol, h1_ema_period, m5_atr_period, m5_rsi_period)
            
            all_data[symbol] = processed_data
            
            # Creăm și stocăm instanța strategiei
            all_strategies[symbol] = strategy_class(symbol=symbol, config=final_config, broker_context=broker)
            
            # Combinăm indexul de timp
            if master_index is None:
                master_index = processed_data.index
            else:
                master_index = master_index.union(processed_data.index)
                
            print(f"Datele și strategia pentru {symbol} au fost încărcate.")
            
        except Exception as e:
            print(f"EROARE FATALĂ la pre-procesarea {symbol}: {e}")
            continue

    if not all_strategies:
        print("Nicio strategie nu a fost încărcată. Oprire.")
        return

    # 3. Rulăm simularea de portofoliu
    print("\n" + "="*50)
    print("Început simulare portofoliu... (poate dura câteva minute)")
    
    # Re-eșantionăm datele pentru a ne asigura că avem o bară la fiecare 5 min (umple golurile)
    for symbol in all_data:
        all_data[symbol] = all_data[symbol].reindex(master_index, method='ffill')

    total_bars = len(master_index)
    
    for i in range(total_bars):
        timestamp = master_index[i]
        
        # 1. Obținem datele curente pentru TOATE simbolurile
        current_data_for_all_symbols = {}
        for symbol, data in all_data.items():
            # Folosim .iloc[i] pentru viteză maximă, deoarece indexul este acum aliniat
            current_data_for_all_symbols[symbol] = data.iloc[i] 
        
        # 2. Setăm datele în broker
        broker.set_current_data(timestamp, current_data_for_all_symbols)
        
        # 3. Rulăm logica fiecărei strategii
        for symbol, strategy in all_strategies.items():
            strategy.run_once(current_bar=current_data_for_all_symbols[symbol])
            
        # 4. Actualizăm P/L-ul tuturor pozițiilor deschise
        broker.update_all_positions()
        
        if i % 10000 == 0 and i > 0: # Log de progres
            print(f"  Progres simulare: {i / total_bars * 100:.2f}%")

    print("Simulare finalizată.")
    
    # 4. Generăm raportul final de portofoliu
    broker.generate_portfolio_report(symbols_tested=list(symbols_to_test))


if __name__ == "__main__":
    
    with open("config/config.yaml", 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # Adaugă capitalul total în config (dacă nu există)
    if 'portfolio_initial_equity' not in config.get('general', {}):
        config['general']['portfolio_initial_equity'] = 1000.0 # Valoare implicită
        print("Notă: Capitalul inițial al portofoliului a fost setat la $1000.0 (implicit).")

    # --- CONFIGUREAZĂ AICI ---
    # Adaugă toate simbolurile pe care vrei să le testezi simultan
    SYMBOLS_TO_TEST = [
        "EURUSD",
        "USDJPY",
        "USDCHF",
        "GBPUSD",
        "USDCAD",
        "AUDUSD",
        "NZDUSD",
        "AUDJPY",
        "EURJPY",
        "EURGBP",
        "GBPJPY"
    ]
    
    STRATEGY_TO_TEST = EMARsiTrendScalper
    STRATEGY_NAME_KEY = "ema_rsi_scalper" 
    # -------------------------

    run_portfolio_backtest(
        config=config,
        symbols_to_test=SYMBOLS_TO_TEST,
        strategy_class=STRATEGY_TO_TEST,
        strategy_name_key=STRATEGY_NAME_KEY
    )