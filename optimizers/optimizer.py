# optimizers/universal_optimizer.py - PATHS FIXED (Subfolder Version)

import sys
import os

# --- 1. FIX IMPORTURI & CĂI (CRITIC) ---
# Obținem folderul curent (unde e scriptul): .../scalping-bot/optimizers
current_dir = os.path.dirname(os.path.abspath(__file__))
# Obținem rădăcina proiectului: .../scalping-bot
PROJECT_ROOT = os.path.dirname(current_dir)
# Adăugăm rădăcina la Python Path pentru a vedea modulele 'core' și 'strategies'
sys.path.append(PROJECT_ROOT)
# ---------------------------------------

import pandas as pd
import yaml
import optuna
import copy
import numpy as np

# Importurile vor funcționa acum
from core.backtest_broker import BacktestBroker
from strategies.ema_rsi_scalper import EMARsiTrendScalper
from strategies.bb_scalper import BollingerReversionScalper

# ==============================================================================
# 1. HELPERE GENERALE
# ==============================================================================

def load_raw_data(file_path):
    try:
        data = pd.read_csv(file_path, header=0, sep='\t')
        data.rename(columns={'<DATE>': 'date', '<TIME>': 'time', '<OPEN>': 'open', '<HIGH>': 'high', '<LOW>': 'low', '<CLOSE>': 'close'}, inplace=True)
        data['datetime'] = pd.to_datetime(data['date'] + ' ' + data['time'], format='%Y.%m.%d %H:%M:%S')
        data.set_index('datetime', inplace=True)
        data.index = data.index.tz_localize(None)
        return data[['open', 'high', 'low', 'close']].copy()
    except Exception:
        return None

def calculate_adx_series(df, period=14):
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
    
    dx = 100 * (abs(100 * (plus_dm_smooth / tr_smooth) - 100 * (minus_dm_smooth / tr_smooth)) / (100 * (plus_dm_smooth / tr_smooth) + 100 * (minus_dm_smooth / tr_smooth)))
    return dx.ewm(alpha=alpha, adjust=False).mean()

# ==============================================================================
# 2. DEFINIȚII SPECIFICE STRATEGIILOR
# ==============================================================================

# --- A. EMA RSI TREND ---
def prepare_data_ema(symbol, config, trial=None):
    # FIX: Folosim os.path.join cu PROJECT_ROOT
    path_m5 = os.path.join(PROJECT_ROOT, "data", f"{symbol}_M1_2Y.csv")
    path_h1 = os.path.join(PROJECT_ROOT, "data", f"{symbol}_H1_2Y.csv")
    
    if not os.path.exists(path_m5) or not os.path.exists(path_h1): return None
    
    df_m5 = load_raw_data(path_m5)
    df_h1 = load_raw_data(path_h1)
    
    ema_p = config.get('ema_period', 50)
    atr_p = config.get('atr_period', 14)
    rsi_p = config.get('rsi_period', 14)

    # Calcul H1 + Shift
    h1_ema = df_h1['close'].ewm(span=ema_p, adjust=False).mean()
    df_h1['H1_trend_up'] = (df_h1['close'] > h1_ema).shift(1) # Honest
    
    # Calcul M5 + Shift
    m5_atr = EMARsiTrendScalper._calculate_atr(df_m5, atr_p, 'ema')
    m5_rsi = EMARsiTrendScalper._calculate_rsi(df_m5, rsi_p)
    
    df_m5['M5_atr'] = m5_atr.shift(1) # Honest
    df_m5['M5_rsi'] = m5_rsi.shift(1) # Honest
    
    df_h1_merge = df_h1[['H1_trend_up']].dropna()
    combined = pd.merge_asof(df_m5, df_h1_merge, left_index=True, right_index=True, direction='backward')
    combined.dropna(inplace=True)
    return combined

def suggest_params_ema(trial):
    return {
        # ⚡ VITEZĂ: Căutăm indicatori foarte rapizi
        # EMA standard e 50. Noi testăm de la 8 la 40.
        'ema_period': trial.suggest_int('ema_period', 8, 40, step=2),
        
        # RSI standard e 14. Noi testăm RSI "Rapid" (2-9) care țipă imediat.
        'rsi_period': trial.suggest_int('rsi_period', 2, 12, step=1),
        
        # ATR pentru volatilitate
        'atr_period': trial.suggest_int('atr_period', 5, 14, step=1),
        
        # 🎯 RISC: Scalping pur (SL mic, TP mic)
        'sl_atr_multiplier': trial.suggest_float('sl_atr_multiplier', 0.5, 2.0, step=0.1),
        'rr_target': trial.suggest_float('rr_target', 0.8, 2.5, step=0.1),
        
        # 🎚️ SENSIBILITATE: Nu mai așteptăm RSI 20. 
        # Dacă RSI e 40 într-un trend puternic, intrăm!
        'rsi_oversold': trial.suggest_int('rsi_oversold', 15, 45, step=5),
        'rsi_overbought': trial.suggest_int('rsi_overbought', 55, 85, step=5),
        
        # 📏 DISTANȚĂ: Permitem prețului să fie mai aproape sau mai departe
        'ema_distance_pips': trial.suggest_float('ema_distance_pips', 2.0, 15.0, step=1.0)
    }

# --- B. BB RANGE ---
def prepare_data_bb(symbol, config, trial):
    # FIX: Folosim os.path.join cu PROJECT_ROOT
    path_m5 = os.path.join(PROJECT_ROOT, "data", f"{symbol}_M5_2Y.csv")
    
    if not os.path.exists(path_m5): return None
    df = load_raw_data(path_m5)
    
    bb_p = trial.params.get('bb_period', 20)
    bb_d = trial.params.get('bb_dev', 2.0)
    
    sma = df['close'].rolling(bb_p).mean()
    std = df['close'].rolling(bb_p).std()
    
    df['bb_sma'] = sma.shift(1) # Honest
    df['bb_upper'] = (sma + (std * bb_d)).shift(1) # Honest
    df['bb_lower'] = (sma - (std * bb_d)).shift(1) # Honest
    df['adx'] = calculate_adx_series(df, 14).shift(1) # Honest
    
    df.dropna(inplace=True)
    return df

def suggest_params_bb(trial):
    return {
        'bb_period': trial.suggest_int('bb_period', 10, 40, step=2),
        'bb_dev': trial.suggest_float('bb_dev', 2.0, 3.5, step=0.1),
        'adx_max': trial.suggest_float('adx_max', 20.0, 50.0, step=5.0)
    }

# ==============================================================================
# 3. REGISTRUL DE STRATEGII
# ==============================================================================
STRATEGY_REGISTRY = {
    'ema_rsi_scalper': {
        'class': EMARsiTrendScalper,
        'prepare_func': prepare_data_ema,
        'dynamic_data': False,
        'suggest_func': suggest_params_ema
    },
    'bb_range_scalper': {
        'class': BollingerReversionScalper,
        'prepare_func': prepare_data_bb,
        'dynamic_data': True,
        'suggest_func': suggest_params_bb
    }
}

# ==============================================================================
# 4. MOTORUL DE OPTIMIZARE
# ==============================================================================

def objective(trial, base_config, symbol, strategy_key, strategy_meta, df_raw_cache):
    params = strategy_meta['suggest_func'](trial)
    
    temp_config = copy.deepcopy(base_config)
    strat_conf = temp_config['strategies'][strategy_key]
    strat_conf.update(params)
    
    if strategy_meta['dynamic_data']:
        df_proc = strategy_meta['prepare_func'](symbol, strat_conf, trial)
    else:
        df_proc = df_raw_cache
        
    if df_proc is None or df_proc.empty: return -9999

    initial_equity = 1000.0
    broker = BacktestBroker(config=temp_config, initial_equity=initial_equity)
    strategy_class = strategy_meta['class']
    strategy = strategy_class(symbol=symbol, config=strat_conf, broker_context=broker)
    
    for index, row in df_proc.iterrows():
        broker.set_current_data(index, {symbol: row})
        strategy.run_once(current_bar=row)
        broker.update_all_positions()

    net_profit = broker.equity - initial_equity
    
    # FIX: Cale raport temporar în folderul curent
    junk_file = os.path.join(current_dir, "temp_univ_junk.txt")
    report = broker.generate_portfolio_report([symbol], report_filename=junk_file)
    
    pf = report.get("profit_factor", 0.0)
    dd = report.get("max_drawdown_pct", 100.0)
    trades = report.get("total_trades", 0)

    if trades < 40: return -1000.0
    if dd > 30.0: return -dd * 100
    if net_profit <= 0: return net_profit
    
    return net_profit * pf

if __name__ == "__main__":
    
    # --- CONFIGURARE ---
    TARGET_STRATEGY = 'asian_breakout' 
    TOTAL_TRIALS = 250
    # -------------------
    
    # FIX: Cale config
    config_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        sys.exit(f"Err config la {config_path}: {e}")

    settings = config.get('strategies', {}).get(TARGET_STRATEGY, {}).get('symbol_settings', {})
    SYMBOLS = [s for s, p in settings.items() if p.get('enabled', False)]
    
    meta = STRATEGY_REGISTRY.get(TARGET_STRATEGY)
    if not meta: sys.exit("Strategie necunoscută.")

    print(f"🚀 OPTIMIZARE (Root: {PROJECT_ROOT})")
    print(f"🎯 Strategie: {TARGET_STRATEGY}")
    print(f"🎯 Simboluri: {SYMBOLS}")

    all_results = []

    for symbol in SYMBOLS:
        print(f"\n--- {symbol} ---")
        
        data_cache = None
        if not meta['dynamic_data']:
            data_cache = meta['prepare_func'](symbol, config['strategies'][TARGET_STRATEGY])
            if data_cache is None: 
                print("Skip: Lipsă date."); continue
        
        # FIX: Baza de date SQLite în folderul curent
        db_path = os.path.join(current_dir, "universal_opt.db")
        study_name = f"{TARGET_STRATEGY}_{symbol}_v1"
        storage = f"sqlite:///{db_path}"
        
        study = optuna.create_study(direction="maximize", study_name=study_name, storage=storage, load_if_exists=True)
        
        rem = max(0, TOTAL_TRIALS - len(study.trials))
        if rem > 0:
            func = lambda t: objective(t, config, symbol, TARGET_STRATEGY, meta, data_cache)
            study.optimize(func, n_trials=rem)
            
        try:
            best = study.best_trial
            print(f"✅ BEST: Score {best.value:.0f} | {best.params}")
            res = {'symbol': symbol, 'score': best.value}
            res.update(best.params)
            all_results.append(res)
        except:
            print("❌ Fail")

    if all_results:
        # FIX: Salvare CSV în folderul curent
        fname = os.path.join(current_dir, f"opt_results_{TARGET_STRATEGY}.csv")
        pd.DataFrame(all_results).to_csv(fname, index=False)
        print(f"\n💾 Rezultate salvate în {fname}")
        
        junk = os.path.join(current_dir, "temp_univ_junk.txt")
        if os.path.exists(junk): os.remove(junk)