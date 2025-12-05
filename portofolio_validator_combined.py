# portofolio_validator_combined.py - MULTI-STRATEGY BACKTEST

import pandas as pd
import yaml
import sys
import os
import numpy as np
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

def preprocess_data_ema_rsi(data_paths, symbol, ema_period, atr_period, rsi_period):
    print(f"[INFO] Pre-procesare {symbol} (EMA Trend)...")
    df_m5 = load_and_prepare_data(data_paths['M5'])
    df_h1 = load_and_prepare_data(data_paths['H1'])
    if df_m5 is None or df_h1 is None: raise FileNotFoundError(f"Date M5/H1 lipsă {symbol}")

    df_h1['H1_ema_trend'] = df_h1['close'].ewm(span=ema_period, adjust=False).mean()
    df_h1['H1_trend_up'] = df_h1['close'] > df_h1['H1_ema_trend']
    df_h1_to_merge = df_h1[['H1_trend_up']] 
    
    df_m5['M5_atr'] = EMARsiTrendScalper._calculate_atr(df_m5, atr_period, 'ema')
    df_m5['M5_rsi'] = EMARsiTrendScalper._calculate_rsi(df_m5, rsi_period)
    
    combined_df = pd.merge_asof(df_m5, df_h1_to_merge, left_index=True, right_index=True, direction='backward')
    combined_df.dropna(inplace=True)
    return combined_df

def preprocess_data_bb(data_paths, symbol, bb_period, bb_dev, adx_period):
    print(f"[INFO] Pre-procesare {symbol} (BB Spike)...")
    df_m5 = load_and_prepare_data(data_paths['M5'])
    if df_m5 is None: raise FileNotFoundError(f"Date M5 lipsă {symbol}")

    df_m5['bb_sma'] = df_m5['close'].rolling(bb_period).mean()
    std = df_m5['close'].rolling(bb_period).std()
    df_m5['bb_upper'] = df_m5['bb_sma'] + (std * bb_dev)
    df_m5['bb_lower'] = df_m5['bb_sma'] - (std * bb_dev)
    df_m5['adx'] = calculate_adx_series(df_m5, adx_period)
    df_m5.dropna(inplace=True)
    return df_m5

# --- 2. Main Logic ---

def run_combined_backtest(config):
    initial_equity = config.get('general', {}).get('portfolio_initial_equity', 2000.0)
    broker = BacktestBroker(config=config, initial_equity=initial_equity)
    
    all_data = {} 
    # Structură: { "Strategie_Simbol": strategy_instance }
    # Ex: "EMA_EURUSD": instance, "BB_EURUSD": instance
    active_strategies = {} 
    master_index = None
    
    strategy_map = {
        'ema_rsi_scalper': EMARsiTrendScalper,
        'bb_range_scalper': BollingerReversionScalper
    }

    print(f"\n🚀 START VALIDARE COMBINATĂ (Capital: ${initial_equity})")

    # --- A. ÎNCĂRCARE STRATEGII & DATE ---
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
                # 1. Configurare specifică
                sym_conf = symbol_settings[symbol]
                final_conf = {**strat_config, **sym_conf}
                
                # 2. Pre-procesare (doar dacă nu am încărcat deja datele pentru acest simbol)
                # Avem nevoie de date specifice per strategie. 
                # EMA vrea H1+M5+RSI+ATR. BB vrea M5+Bands+ADX.
                # Soluție: Dacă simbolul există deja în all_data (de la cealaltă strategie), 
                # trebuie să facem MERGE la coloane sau să re-procesăm.
                # Pentru simplitate și siguranță, vom crea seturi de date separate în memorie dacă e nevoie,
                # dar BacktestBroker acceptă un singur feed per simbol.
                # FIX: Vom face un "Super DataFrame" per simbol care conține toți indicatorii necesari.
                
                data_paths = {
                    "M5": f"data/{symbol}_M5_9Y.csv",
                    "H1": f"data/{symbol}_H1_9Y.csv"
                }

                # Verificăm fișierele
                if not os.path.exists(data_paths['M5']):
                    print(f"❌ Lipsă date {symbol}")
                    continue

                # Calculăm indicatorii specifici
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
                
                # MERGE în all_data
                if symbol not in all_data:
                    all_data[symbol] = df_new
                else:
                    # Dacă simbolul există deja, adăugăm coloanele noi
                    # Ex: all_data[symbol] are deja EMA, acum adăugăm BB
                    existing_df = all_data[symbol]
                    # Facem join pe index (timp)
                    # Folosim combine_first sau join. Join e mai sigur.
                    # Trebuie să ne asigurăm că nu duplicăm coloane (open, close etc)
                    cols_to_use = df_new.columns.difference(existing_df.columns)
                    all_data[symbol] = existing_df.join(df_new[cols_to_use], how='outer')
                    # După join, s-ar putea să avem goluri (NaN) dacă M5/H1 diferă ușor, facem ffill după
                
                # Instanțiere Strategie
                # Cheie unică: NumeStrategie_Simbol (ex: ema_rsi_scalper_EURUSD)
                instance_key = f"{strat_name}_{symbol}"
                active_strategies[instance_key] = strat_class(symbol=symbol, config=final_conf, broker_context=broker)
                
                # Index comun
                if master_index is None:
                    master_index = all_data[symbol].index
                else:
                    master_index = master_index.union(all_data[symbol].index)
                    
            except Exception as e:
                print(f"❌ Eroare la {symbol}: {e}")

    # --- B. SIMULARE ---
    print(f"\n⏳ Sincronizare și Rulare Simulare ({len(active_strategies)} instanțe active)...")
    
    master_index_unique = master_index.unique().sort_values()
    
    # Reindexare și umplere goluri
    for sym in all_data:
        all_data[sym] = all_data[sym].reindex(master_index_unique, method='ffill')
    
    total_bars = len(master_index_unique)
    
    for i in range(total_bars):
        timestamp = master_index_unique[i]
        
        # 1. Feed date curent
        current_data_map = {}
        for sym, df in all_data.items():
            current_data_map[sym] = df.iloc[i]
            
        broker.set_current_data(timestamp, current_data_map)
        
        # 2. Execuție Strategii
        for key, strategy in active_strategies.items():
            # Strategia știe ce simbol are (self.symbol)
            # Extragem datele pentru simbolul ei
            bar = current_data_map.get(strategy.symbol)
            if bar is not None:
                strategy.run_once(current_bar=bar)
        
        # 3. Update Poziții
        broker.update_all_positions()
        
        if i % 100000 == 0 and i > 0:
            print(f"  [{i}/{total_bars}] {timestamp} | Equity: ${broker.equity:.2f}")

    # --- C. RAPORT FINAL ---
    print("\n✅ Validare Combinată Finalizată.")
    broker.generate_portfolio_report(list(active_strategies.keys()), "PORTFOLIO_COMBINED_9Y.txt")

if __name__ == "__main__":
    try:
        with open("config/config.yaml", 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        sys.exit(f"Config error: {e}")

    # Capital Test
    config['general']['portfolio_initial_equity'] = 2000.0
    
    run_combined_backtest(config)