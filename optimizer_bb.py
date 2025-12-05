# optimizer_bb.py - OPTIMIZARE RANGE (Simboluri preluate din Config)

import pandas as pd
import yaml
import optuna
import copy
import sys
import numpy as np
import os
from core.backtest_broker import BacktestBroker
from strategies.bb_scalper import BollingerReversionScalper

# --- 1. Helper Functions (ADX & Data) ---

def calculate_adx_series(df, period=14):
    """Calcul ADX standard."""
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

# --- 2. Funcția Obiectiv ---

# În optimizer_bb.py, înlocuiește funcția 'objective' cu aceasta:

def objective(trial, base_config, symbol, max_allowed_drawdown, df_raw):
    
    # 1. PARAMETRI DE OPTIMIZAT
    bb_period = trial.suggest_int('bb_period', 15, 60, step=5)
    bb_dev = trial.suggest_float('bb_dev', 1.8, 3.5, step=0.1)
    adx_max = trial.suggest_float('adx_max', 15.0, 40.0, step=5.0)
    
    # Configurare
    temp_config = copy.deepcopy(base_config)
    strat_conf = temp_config['strategies']['bb_range_scalper']
    strat_conf['bb_period'] = bb_period
    strat_conf['bb_dev'] = bb_dev
    strat_conf['adx_max'] = adx_max
    strat_conf['timeframe'] = 'M5'
    
    # 2. CALCUL INDICATORI
    df_proc = df_raw.copy()
    df_proc['bb_sma'] = df_proc['close'].rolling(bb_period).mean()
    std = df_proc['close'].rolling(bb_period).std()
    df_proc['bb_upper'] = df_proc['bb_sma'] + (std * bb_dev)
    df_proc['bb_lower'] = df_proc['bb_sma'] - (std * bb_dev)
    # Presupunem adx_period fix 14 pentru viteză, sau îl poți optimiza
    df_proc['adx'] = calculate_adx_series(df_proc, 14)
    df_proc.dropna(inplace=True)
    
    # 3. RULARE BACKTEST
    initial_cash = 1000.0
    broker = BacktestBroker(config=temp_config, initial_equity=initial_cash)
    strategy = BollingerReversionScalper(symbol=symbol, config=strat_conf, broker_context=broker)
    
    for index, row in df_proc.iterrows():
        broker.set_current_data(index, {symbol: row})
        strategy.run_once(current_bar=row)
        broker.update_all_positions()

    # 4. EVALUARE FINANCIARĂ
    # Calculăm Profitul Net Real
    net_profit = broker.equity - initial_cash
    
    junk_file = "temp_bb_opt_junk.txt"
    report = broker.generate_portfolio_report([symbol], report_filename=junk_file)
    
    pf = report.get("profit_factor", 0.0)
    dd = report.get("max_drawdown_pct", 100.0)
    trades = report.get("total_trades", 0)

    # --- FILTRE DURE (PENALIZĂRI) ---
    
    # A. Prea puține tranzacții = Irelevant statistic
    if trades < 30: 
        return -1000.0 
        
    # B. Drawdown inacceptabil = Descalificare instantă
    if dd > max_allowed_drawdown: 
        return -dd * 10 # Penalizare mare proporțională cu riscul
        
    # C. Pierdere netă de bani
    if net_profit <= 0:
        return net_profit # Returnăm pierderea ca scor negativ
    
    # --- FORMULA MAGICĂ ---
    # Maximizăm: Bani în buzunar (Net Profit) x Siguranță (Profit Factor)
    score = net_profit * pf
    
    return score
    
# --- 3. Main Loop ---

if __name__ == "__main__":
    
    MAX_DD = 25.0 
    TOTAL_TRIALS = 250 
    STRATEGY_KEY = 'bb_range_scalper'

    # 1. Încărcare Config
    try:
        with open("config/config.yaml", 'r', encoding='utf-8') as f:
            base_config = yaml.safe_load(f)
    except Exception as e:
        sys.exit(f"❌ Lipsă sau eroare config.yaml: {e}")

    # 2. Extragere Dinamică Simboluri ENABLED
    print(f"🔍 Citire simboluri active pentru '{STRATEGY_KEY}'...")
    settings = base_config.get('strategies', {}).get(STRATEGY_KEY, {}).get('symbol_settings', {})
    
    SYMBOLS_TO_OPTIMIZE = [s for s, p in settings.items() if p.get('enabled', False)]

    if not SYMBOLS_TO_OPTIMIZE:
        print(f"⚠️ Niciun simbol nu este activat (enabled: true) pentru {STRATEGY_KEY} în config.yaml!")
        print("ℹ️  Activează perechile de range (ex: EURGBP, AUDCAD) în config și încearcă din nou.")
        sys.exit(0)

    print(f"🌀 Pornire Optimizare BB RANGE pentru {len(SYMBOLS_TO_OPTIMIZE)} simboluri: {SYMBOLS_TO_OPTIMIZE}")
    
    all_best = []

    for symbol in SYMBOLS_TO_OPTIMIZE:
        print(f"\n--- Optimizare {symbol} ---")
        
        # Căutăm date M5 (2 Ani pentru viteză, sau 9 Ani pentru robustețe - schimbă aici după preferință)
        # Recomand _2Y pentru optimizare și apoi validare pe _9Y
        path = f"data/{symbol}_M5_2Y.csv"
        
        if not os.path.exists(path):
            print(f"⚠️ Lipsă date {symbol} ({path}) - Skipping.")
            continue
            
        # Încărcare Raw Data
        df_raw = load_and_prepare_data(path)
        if df_raw is None or df_raw.empty:
            print(f"❌ Date invalide pentru {symbol}")
            continue
        
        study_name = f"bb_opt_{symbol}_v2"
        storage_url = "sqlite:///bb_opt_results.db"

        study = optuna.create_study(
            direction="maximize", 
            study_name=study_name,
            storage=storage_url,
            load_if_exists=True
        )
        
        remaining = max(0, TOTAL_TRIALS - len(study.trials))
        
        if remaining > 0:
            func = lambda t: objective(t, base_config, symbol, MAX_DD, df_raw)
            study.optimize(func, n_trials=remaining, show_progress_bar=True)
        
        try:
            best = study.best_trial
            print(f"✅ BEST {symbol}: Score={best.value:.2f}")
            print(f"   Params: {best.params}")
            
            res = {"symbol": symbol, "score": best.value}
            res.update(best.params)
            all_best.append(res)
        except ValueError:
            print(f"❌ Eșec {symbol}: Niciun trial valid.")

    if all_best:
        pd.DataFrame(all_best).to_csv("bb_optimization_results_dynamic.csv", index=False)
        print("\n✅ Rezultate salvate în 'bb_optimization_results_dynamic.csv'")
        
        # Curățenie
        if os.path.exists("temp_bb_opt_junk.txt"):
            os.remove("temp_bb_opt_junk.txt")