# optimizer_ema_rsi.py
import pandas as pd
import yaml
import optuna
import copy
import sys

from core.backtest_broker import BacktestBroker
from strategies.ema_rsi_scalper import EMARsiTrendScalper

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

def preprocess_data_ema(data_paths, symbol, h1_ema_period, m5_atr_period, m5_rsi_period):
    print("[INFO] Început pre-procesare EMA...")
    df_m5 = load_and_prepare_data(data_paths['M5'])
    df_h1 = load_and_prepare_data(data_paths['H1'])
    if df_m5 is None or df_h1 is None:
        raise FileNotFoundError("Datele M5 sau H1 nu au putut fi încărcate.")

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

def objective(trial, base_config, symbol, max_allowed_drawdown, processed_data):
    temp_config = copy.deepcopy(base_config)
    strategy_config = temp_config['strategies']['ema_rsi_scalper']

    strategy_config['rr_target'] = trial.suggest_float('rr_target', 1.0, 4.0)
    strategy_config['sl_atr_multiplier'] = trial.suggest_float('sl_atr_multiplier', 1.0, 5.0)
    strategy_config['rsi_oversold'] = trial.suggest_int('rsi_oversold', 15, 35)
    strategy_config['rsi_overbought'] = trial.suggest_int('rsi_overbought', 65, 85)
    
    broker = BacktestBroker(processed_data=processed_data, config=temp_config, initial_equity=200.0)
    strategy = EMARsiTrendScalper(symbol=symbol, config=strategy_config, broker_context=broker)

    while broker.advance_time():
        strategy.run_once()

    results = broker.generate_report()
    
    profit_factor = results["profit_factor"]
    max_drawdown = results["max_drawdown"]
    total_trades = results["total_trades"]

    if total_trades < 50 or max_drawdown > max_allowed_drawdown:
        if trial.number % 10 == 0:
            print(f"[WARN] Trial {trial.number} eșuat: Trades={total_trades}, DD={max_drawdown:.2f}%")
        return - (max_drawdown / max_allowed_drawdown) if max_drawdown > 0 else -1.0
    
    print(f"[INFO] Trial {trial.number} OK: PF={profit_factor:.2f}, DD={max_drawdown:.2f}%, Trades={total_trades}")
    return profit_factor

if __name__ == "__main__":
    with open("config/config.yaml", 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # --- MODIFICARE AICI: Trecem la USDJPY ---
    SYMBOL_TO_TEST = "NZDUSD"
    STRATEGY_TO_TEST = EMARsiTrendScalper
    MAX_DRAWDOWN_LIMIT = 30.0
    
    DATA_PATHS = {
        "M5": f"data/{SYMBOL_TO_TEST}_M5_1Y.csv",
        "H1": f"data/{SYMBOL_TO_TEST}_H1_1Y.csv"
    }
    
    study_name = f"ema_rsi_m5_{SYMBOL_TO_TEST}_v1" # Nume nou pentru noul studiu
    storage_name = f"sqlite:///optimization_studies_{SYMBOL_TO_TEST}.db"
    # --- SFÂRȘIT MODIFICARE ---

    study = optuna.create_study(
        study_name=study_name,
        storage=storage_name,
        direction="maximize",
        load_if_exists=True
    )

    print(f"--- Început pre-procesare o singură dată pentru {STRATEGY_TO_TEST.__name__} ({SYMBOL_TO_TEST}) ---")
    
    h1_ema_period = config.get('strategies',{}).get('ema_rsi_scalper',{}).get('h1_ema_period', 50)
    m5_atr_period = config.get('strategies',{}).get('ema_rsi_scalper',{}).get('m5_atr_period', 14)
    m5_rsi_period = config.get('strategies',{}).get('ema_rsi_scalper',{}).get('m5_rsi_period', 14)

    try:
        processed_data = preprocess_data_ema(DATA_PATHS, SYMBOL_TO_TEST, h1_ema_period, m5_atr_period, m5_rsi_period)
        print("--- Pre-procesare finalizată. Se pornește optimizarea... ---")
    except Exception as e:
        print(f"EROARE FATALĂ la pre-procesarea datelor: {e}")
        sys.exit()

    completed_trials = len(study.trials)
    total_trials = 250
    trials_to_run = max(0, total_trials - completed_trials)
    
    print(f"Stocare: {storage_name}")
    print(f"Obiectiv: Maximizare Profit Factor, cu Drawdown Maxim <= {MAX_DRAWDOWN_LIMIT}%")
    print(f"Teste finalizate: {completed_trials}")
    print(f"Teste rămase de rulat: {trials_to_run}")

    if trials_to_run > 0:
        objective_func = lambda trial: objective(trial, config, SYMBOL_TO_TEST, MAX_DRAWDOWN_LIMIT, processed_data)
        study.optimize(objective_func, n_trials=trials_to_run, show_progress_bar=True)

    print("\n" + "="*50)
    print(f"--- OPTIMIZARE ({study_name}) FINALIZATĂ ---")

    try:
        best_trial = study.best_trial
        if best_trial.value < 0:
            print(f"AVERTISMENT: Nu s-a găsit nicio combinație care să respecte limita de Drawdown ({MAX_DRAWDOWN_LIMIT}%).")
        else:
            print("Cel mai bun rezultat (Profit Factor, respectând Drawdown):", best_trial.value)
            print(f"Cei mai buni parametri găsiți (pentru {STRATEGY_TO_TEST.__name__} pe M5):")
            print(f"  (Perioade fixe: H1_EMA={h1_ema_period}, M5_ATR={m5_atr_period}, M5_RSI={m5_rsi_period})")
            for key, value in best_trial.params.items():
                print(f"  {key}: {value}")
    except ValueError:
        print("Studiul s-a încheiat fără niciun trial completat cu succes.")
        
    print("="*50)