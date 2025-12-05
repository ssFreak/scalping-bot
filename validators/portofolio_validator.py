# portofolio_validator.py - FULL IMPLEMENTATION (EMA Trend + BB Range)

import pandas as pd
import yaml
import sys
import os
import numpy as np
from datetime import datetime

# Importăm brokerul și ambele strategii
from core.backtest_broker import BacktestBroker
from strategies.ema_rsi_scalper import EMARsiTrendScalper
from strategies.bb_scalper import BollingerReversionScalper

# --- 1. Helper Functions (Calcul Indicatori) ---

def calculate_adx_series(df, period=14):
    """
    Calcul ADX folosind Pandas (replică logica standard Wilder).
    Necesită coloanele: high, low, close.
    """
    df = df.copy()
    
    # True Range
    df['h-l'] = df['high'] - df['low']
    df['h-pc'] = (df['high'] - df['close'].shift(1)).abs()
    df['l-pc'] = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)
    
    # Directional Movement
    df['up_move'] = df['high'] - df['high'].shift(1)
    df['down_move'] = df['low'].shift(1) - df['low']
    
    df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0.0)
    df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0.0)
    
    # Smoothing (Wilder's Smoothing: alpha = 1/period)
    alpha = 1.0 / period
    
    # Folosim EWM pentru smoothing rapid
    tr_smooth = df['tr'].ewm(alpha=alpha, adjust=False).mean()
    plus_dm_smooth = df['plus_dm'].ewm(alpha=alpha, adjust=False).mean()
    minus_dm_smooth = df['minus_dm'].ewm(alpha=alpha, adjust=False).mean()
    
    # DI+ și DI-
    plus_di = 100 * (plus_dm_smooth / tr_smooth)
    minus_di = 100 * (minus_dm_smooth / tr_smooth)
    
    # DX și ADX
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    
    return adx

def load_and_prepare_data(file_path):
    """Încarcă datele din CSV Export MT5."""
    try:
        data = pd.read_csv(file_path, header=0, sep='\t')
        data.rename(columns={
            '<DATE>': 'date', '<TIME>': 'time', 
            '<OPEN>': 'open', '<HIGH>': 'high', 
            '<LOW>': 'low', '<CLOSE>': 'close'
        }, inplace=True)
        
        data['datetime'] = pd.to_datetime(data['date'] + ' ' + data['time'], format='%Y.%m.%d %H:%M:%S')
        data.set_index('datetime', inplace=True)
        data.index = data.index.tz_localize(None)
        
        return data[['open', 'high', 'low', 'close']].copy()
    except Exception as e:
        print(f"EROARE la încărcarea {file_path}: {e}")
        return None

# --- 2. Funcții de Pre-procesare Specifice ---

def preprocess_data_ema_rsi(data_paths, symbol, ema_period, atr_period, rsi_period):
    """Pregătește datele pentru EMA RSI Scalper (M5 + H1)."""
    print(f"[INFO] Pre-procesare EMA/RSI pentru {symbol}...")
    
    df_m5 = load_and_prepare_data(data_paths['M5'])
    df_h1 = load_and_prepare_data(data_paths['H1'])
    
    if df_m5 is None or df_h1 is None:
        raise FileNotFoundError(f"Date M5 sau H1 lipsă pentru {symbol}")

    # Trend H1
    df_h1['H1_ema_trend'] = df_h1['close'].ewm(span=ema_period, adjust=False).mean()
    df_h1['H1_trend_up'] = df_h1['close'] > df_h1['H1_ema_trend']
    df_h1_to_merge = df_h1[['H1_trend_up']] 
    
    # Semnale M5
    df_m5['M5_atr'] = EMARsiTrendScalper._calculate_atr(df_m5, atr_period, 'ema')
    df_m5['M5_rsi'] = EMARsiTrendScalper._calculate_rsi(df_m5, rsi_period)
    
    # Merge (Aliniere H1 pe M5)
    combined_df = pd.merge_asof(df_m5, df_h1_to_merge, left_index=True, right_index=True, direction='backward')
    combined_df.dropna(inplace=True)
    return combined_df

def preprocess_data_bb(data_paths, symbol, bb_period, bb_dev, adx_period):
    """Pregătește datele pentru BB Range Scalper (Doar M5)."""
    print(f"[INFO] Pre-procesare BB Range pentru {symbol}...")
    
    df_m5 = load_and_prepare_data(data_paths['M1'])
    if df_m5 is None: 
        raise FileNotFoundError(f"Date M5 lipsă pentru {symbol}")

    # Bollinger Bands
    df_m5['bb_sma'] = df_m5['close'].rolling(bb_period).mean()
    std = df_m5['close'].rolling(bb_period).std()
    df_m5['bb_upper'] = df_m5['bb_sma'] + (std * bb_dev)
    df_m5['bb_lower'] = df_m5['bb_sma'] - (std * bb_dev)
    
    # ADX
    df_m5['adx'] = calculate_adx_series(df_m5, adx_period)
    
    df_m5.dropna(inplace=True)
    return df_m5

# --- 3. Engine-ul de Validare ---

def run_portfolio_backtest(config, symbols_to_test, strategy_class, strategy_name_key):
    
    # Configurare inițială cont
    initial_equity = config.get('general', {}).get('portfolio_initial_equity', 2000.0)
    broker = BacktestBroker(config=config, initial_equity=initial_equity)
    
    all_data = {} 
    all_strategies = {} 
    master_index = None
    
    print(f"\n🚀 START Validare Portofoliu: {strategy_name_key}")
    print(f"💰 Capital Start: ${initial_equity}")
    print(f"📈 Simboluri ({len(symbols_to_test)}): {symbols_to_test}\n")
    
    # --- A. Încărcare și Procesare Date ---
    for symbol in symbols_to_test:
        try:
            # Extragere Configurație Specifică
            base_strategy_config = config['strategies'][strategy_name_key]
            symbol_config = base_strategy_config.get('symbol_settings', {}).get(symbol, {})
            final_config = {**base_strategy_config, **symbol_config}

            if not final_config.get('enabled', True):
                print(f"⚠️ Skip {symbol}: Disabled.")
                continue

            # Căi fișiere (Default 9 Ani)
            data_paths = {
                "M1": f"data/{symbol}_M1_9Y.csv",
                "H1": f"data/{symbol}_H1_9Y.csv"
            }
            if not os.path.exists(data_paths['M1']):
                print(f"❌ EROARE: Fișier lipsă {data_paths['M1']}")
                continue

            # --- SELECTOR STRATEGIE ---
            if strategy_name_key == "ema_rsi_scalper":
                ema_p = final_config.get('ema_period', 50)
                atr_p = final_config.get('atr_period', 14)
                rsi_p = final_config.get('rsi_period', 14)
                processed_data = preprocess_data_ema_rsi(data_paths, symbol, ema_p, atr_p, rsi_p)
                
            elif strategy_name_key == "bb_range_scalper":
                bb_p = final_config.get('bb_period', 20)
                bb_d = final_config.get('bb_dev', 2.0)
                adx_p = final_config.get('adx_period', 14)
                processed_data = preprocess_data_bb(data_paths, symbol, bb_p, bb_d, adx_p)
            else:
                print("Strategie necunoscută!")
                return
            # --------------------------
            
            all_data[symbol] = processed_data
            all_strategies[symbol] = strategy_class(symbol=symbol, config=final_config, broker_context=broker)
            
            # Construire Index Comun
            if master_index is None:
                master_index = processed_data.index
            else:
                master_index = master_index.union(processed_data.index)
                
            print(f"✅ Loaded {symbol}: {len(processed_data)} bare")
            
        except Exception as e:
            print(f"❌ CRITICAL ERROR {symbol}: {e}")
            continue

    if not all_strategies:
        print("Oprire: Nicio strategie nu a fost încărcată corect.")
        return

    # --- B. Simulare Sincronizată ---
    print("\n⏳ Sincronizare date și rulare simulare...")
    
    # Aliniere date (ffill pentru a umple golurile de lichiditate)
    master_index_unique = master_index.unique().sort_values()
    for symbol in all_data:
        all_data[symbol] = all_data[symbol].reindex(master_index_unique, method='ffill')

    total_bars = len(master_index_unique)
    
    for i in range(total_bars):
        timestamp = master_index_unique[i]
        
        # 1. Feed date curente
        current_data_map = {}
        for symbol, data in all_data.items():
            current_data_map[symbol] = data.iloc[i] 
        
        broker.set_current_data(timestamp, current_data_map)
        
        # 2. Execuție Strategii
        for symbol, strategy in all_strategies.items():
            strategy.run_once(current_bar=current_data_map[symbol])
            
        # 3. Update Poziții
        broker.update_all_positions()
        
        # Log Progres
        if i % 100000 == 0 and i > 0:
            print(f"  [{i}/{total_bars}] {timestamp} | Equity: ${broker.equity:.2f}")

    # --- C. Raportare ---
    print("\n✅ Simulare Completă.")
    report_name = f"REPORT_{strategy_name_key}_9Y.txt"
    broker.generate_portfolio_report(list(all_strategies.keys()), report_filename=report_name)

# --- 4. Configurare Rulare ---

if __name__ == "__main__":
    
    # Citire Config
    try:
        with open("config/config.yaml", 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Eroare config: {e}")
        sys.exit(1)

    # Parametru Test
    config['general']['portfolio_initial_equity'] = 1000.0

    # ==========================================
    # 🎛️ SELECTOR MOD TEST (Modifică Aici!)
    # ==========================================
    
    # MOD 1: Testare Trend (Sniper 6)
    # STRATEGY_CLASS = EMARsiTrendScalper
    # STRATEGY_KEY = "ema_rsi_scalper"

    # MOD 2: Testare Range (BB Scalper)
    STRATEGY_CLASS = BollingerReversionScalper
    STRATEGY_KEY = "bb_range_scalper"
    
    # ==========================================

    # Extragere simboluri active pentru strategia selectată
    settings = config.get('strategies', {}).get(STRATEGY_KEY, {}).get('symbol_settings', {})
    # Pentru test, luăm toate simbolurile definite, chiar dacă sunt false în config,
    # dar în run_portfolio_backtest avem check de 'enabled'. 
    # Aici luăm tot ce e definit ca să le trecem prin filtru.
    SYMBOLS_TO_TEST = [s for s, p in settings.items() if p.get('enabled', False)]

    run_portfolio_backtest(config, SYMBOLS_TO_TEST, STRATEGY_CLASS, STRATEGY_KEY)