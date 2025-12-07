# validators/portofolio_validator_combined.py - PATHS FIXED

import sys
import os

# --- 1. FIX IMPORTURI: Adăugăm rădăcina proiectului în sys.path ---
# Obținem folderul curent (validators)
current_dir = os.path.dirname(os.path.abspath(__file__))
# Obținem rădăcina proiectului (un nivel mai sus: scalping-bot)
PROJECT_ROOT = os.path.dirname(current_dir)
sys.path.append(PROJECT_ROOT)
# ------------------------------------------------------------------

import pandas as pd
import yaml
import numpy as np
from datetime import datetime

# Acum importurile vor funcționa corect
from core.backtest_broker import BacktestBroker
from strategies.ema_rsi_scalper import EMARsiTrendScalper
from strategies.bb_scalper import BollingerReversionScalper

# --- 1. Helper Functions & Pre-processing ---

def calculate_adx_series(df, period=14):
    """Calcul ADX standard (Pandas)."""
    df = df.copy()
    df['h-l'] = df['high'] - df['low']
    df['h-pc'] = (df['high'] - df['close'].shift(1)).abs()
    df['l-pc'] = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)
    
    df['up_move'] = df['high'] - df['high'].shift(1)
    df['down_move'] = df['low'].shift(1) - df['low']
    
    df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0.0)
    df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0.0)
    
    alpha = 1.0 / period
    tr_smooth = df['tr'].ewm(alpha=alpha, adjust=False).mean()
    plus_dm_smooth = df['plus_dm'].ewm(alpha=alpha, adjust=False).mean()
    minus_dm_smooth = df['minus_dm'].ewm(alpha=alpha, adjust=False).mean()
    
    plus_di = 100 * (plus_dm_smooth / tr_smooth)
    minus_di = 100 * (minus_dm_smooth / tr_smooth)
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx

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

# --- ÎNLOCUIEȘTE ACESTE DOUĂ FUNCȚII ÎN portofolio_validator_combined.py ---

def preprocess_data_ema_rsi(data_paths, symbol, ema_period, atr_period, rsi_period):
    print(f"[INFO] Pre-procesare {symbol} (EMA Trend)...")
    df_m5 = load_and_prepare_data(data_paths['M5'])
    df_h1 = load_and_prepare_data(data_paths['H1'])
    if df_m5 is None or df_h1 is None: raise FileNotFoundError(f"Date M5/H1 lipsă {symbol}")

    # 1. Calculăm Indicatorii pe H1
    h1_ema = df_h1['close'].ewm(span=ema_period, adjust=False).mean()
    # Logica Trend: Close > EMA
    # 🛑 FIX LOOKAHEAD H1: Shift(1) - Folosim trendul barei anterioare închise
    df_h1['H1_trend_up'] = (df_h1['close'] > h1_ema).shift(1)
    
    # 2. Calculăm Indicatorii pe M5
    m5_atr = EMARsiTrendScalper._calculate_atr(df_m5, atr_period, 'ema')
    m5_rsi = EMARsiTrendScalper._calculate_rsi(df_m5, rsi_period)
    
    # 🛑 FIX LOOKAHEAD M5: Shift(1) 
    # Decizia la timpul T se bazează pe RSI/ATR de la T-1 (bară închisă)
    df_m5['M5_atr'] = m5_atr.shift(1)
    df_m5['M5_rsi'] = m5_rsi.shift(1)
    
    # Curățăm primele rânduri care devin NaN după shift
    df_h1_to_merge = df_h1[['H1_trend_up']].dropna()
    
    # 3. Merge
    combined_df = pd.merge_asof(df_m5, df_h1_to_merge, left_index=True, right_index=True, direction='backward')
    combined_df.dropna(inplace=True)
    return combined_df

def preprocess_data_bb(data_paths, symbol, bb_period, bb_dev, adx_period):
    print(f"[INFO] Pre-procesare {symbol} (BB Spike)...")
    df_m5 = load_and_prepare_data(data_paths['M5'])
    if df_m5 is None: raise FileNotFoundError(f"Date M5 lipsă {symbol}")

    # 1. Calculăm valorile brute
    sma = df_m5['close'].rolling(bb_period).mean()
    std = df_m5['close'].rolling(bb_period).std()
    upper = sma + (std * bb_dev)
    lower = sma - (std * bb_dev)
    adx = calculate_adx_series(df_m5, adx_period)
    
    # 🛑 FIX LOOKAHEAD BB: Shift(1)
    # Nu putem ști că am atins banda superioară la Close decât DUPĂ ce s-a închis bara.
    # Executăm la deschiderea următoare dacă bara anterioară a închis afară.
    df_m5['bb_sma'] = sma.shift(1)
    df_m5['bb_upper'] = upper.shift(1)
    df_m5['bb_lower'] = lower.shift(1)
    df_m5['adx'] = adx.shift(1)
    
    df_m5.dropna(inplace=True)
    return df_m5

# --- 2. Main Logic ---

def run_combined_backtest(config):
    initial_equity = config.get('general', {}).get('portfolio_initial_equity', 2000.0)
    broker = BacktestBroker(config=config, initial_equity=initial_equity)
    
    all_data = {} 
    active_strategies = {} 
    master_index = None
    
    strategy_map = {
        'ema_rsi_scalper': EMARsiTrendScalper,
        'bb_range_scalper': BollingerReversionScalper
    }

    print(f"\n🚀 START VALIDARE COMBINATĂ (Capital: ${initial_equity})")

    for strat_name, strat_config in config.get('strategies', {}).items():
        if not strat_config.get('enabled', False):
            continue
            
        strat_class = strategy_map.get(strat_name)
        if not strat_class:
            print(f"⚠️ Strategie necunoscută în config: {strat_name}")
            continue

        print(f"\n🔹 Încărcare Strategie: {strat_name}")
        
        symbol_settings = strat_config.get('symbol_settings', {})
        active_symbols = [s for s, p in symbol_settings.items() if p.get('enabled', False)]
        
        for symbol in active_symbols:
            try:
                sym_conf = symbol_settings[symbol]
                final_conf = {**strat_config, **sym_conf}
                
                # --- FIX CĂI DATE ---
                # Folosim os.path.join cu PROJECT_ROOT pentru a găsi datele
                data_paths = {
                    "M5": os.path.join(PROJECT_ROOT, "data", f"{symbol}_M5_9Y.csv"),
                    "H1": os.path.join(PROJECT_ROOT, "data", f"{symbol}_H1_9Y.csv")
                }

                if not os.path.exists(data_paths['M5']):
                    print(f"❌ Lipsă date {symbol} la calea: {data_paths['M5']}")
                    continue

                if strat_name == 'ema_rsi_scalper':
                    df_new = preprocess_data_ema_rsi(data_paths, symbol, 
                                final_conf.get('ema_period',50), 
                                final_conf.get('atr_period',14), 
                                final_conf.get('rsi_period',14))
                elif strat_name == 'bb_range_scalper':
                    df_new = preprocess_data_bb(data_paths, symbol, 
                                final_conf.get('bb_period',20), 
                                final_conf.get('bb_dev',2.0), 
                                final_conf.get('adx_period',14))
                
                if symbol not in all_data:
                    all_data[symbol] = df_new
                else:
                    existing_df = all_data[symbol]
                    cols_to_use = df_new.columns.difference(existing_df.columns)
                    all_data[symbol] = existing_df.join(df_new[cols_to_use], how='outer')
                
                instance_key = f"{strat_name}_{symbol}"
                active_strategies[instance_key] = strat_class(symbol=symbol, config=final_conf, broker_context=broker)
                
                if master_index is None:
                    master_index = all_data[symbol].index
                else:
                    master_index = master_index.union(all_data[symbol].index)
                    
            except Exception as e:
                print(f"❌ Eroare la {symbol}: {e}")

    print(f"\n⏳ Sincronizare și Rulare Simulare ({len(active_strategies)} instanțe active)...")
    
    if master_index is None:
        print("❌ Nu au fost încărcate date. Verifică paths.")
        return

    master_index_unique = master_index.unique().sort_values()
    
    for sym in all_data:
        all_data[sym] = all_data[sym].reindex(master_index_unique, method='ffill')
    
    total_bars = len(master_index_unique)
    
    for i in range(total_bars):
        timestamp = master_index_unique[i]
        
        current_data_map = {}
        for sym, df in all_data.items():
            current_data_map[sym] = df.iloc[i]
            
        broker.set_current_data(timestamp, current_data_map)
        
        for key, strategy in active_strategies.items():
            bar = current_data_map.get(strategy.symbol)
            if bar is not None:
                strategy.run_once(current_bar=bar)
        
        broker.update_all_positions()
        
        if i % 100000 == 0 and i > 0:
            print(f"  [{i}/{total_bars}] {timestamp} | Equity: ${broker.equity:.2f}")

    print("\n✅ Validare Combinată Finalizată.")
    
    # Salvare raport în folderul validators
    report_path = os.path.join(current_dir, "PORTFOLIO_COMBINED_9Y.txt")
    broker.generate_portfolio_report(list(active_strategies.keys()), report_path)

if __name__ == "__main__":
    
    # --- FIX CALE CONFIG ---
    config_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        sys.exit(f"Config error la {config_path}: {e}")

    config['general']['portfolio_initial_equity'] = 2000.0
    
    run_combined_backtest(config)