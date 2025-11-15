# optimizer_ema_rsi.py
# VERSIUNE BATCH PENTRU PORTOFOLIU
# 
# Acest script va rula optimizarea secvențial pentru FIECARE 
# simbol din lista 'SYMBOLS_TO_OPTIMIZE'.
# 

import pandas as pd
import yaml
import optuna
import copy
import sys
import traceback # Importăm pentru a afișa erori

# --- Importăm clasele necesare ---
from core.backtest_broker import BacktestBroker
from strategies.ema_rsi_scalper import EMARsiTrendScalper

# --- Funcțiile de pre-procesare (neschimbate) ---

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
    """Funcție de pre-procesare specifică pentru EMARsiTrendScalper."""
    print(f"[INFO] Început pre-procesare EMA pentru {symbol}...")
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
    
    print(f"[INFO] Unire date M5 și H1 pentru {symbol}...")
    combined_df = pd.merge_asof(
        df_m5,
        df_h1_to_merge,
        left_index=True,
        right_index=True,
        direction='backward'
    )
    combined_df.dropna(inplace=True)
    print(f"[INFO] Pre-procesare finalizată pentru {symbol}.")
    return combined_df

# --- Funcția Objective (neschimbată) ---
def objective(trial, base_config, symbol, max_allowed_drawdown, processed_data):
    temp_config = copy.deepcopy(base_config)
    strategy_config = temp_config['strategies']['ema_rsi_scalper']

    strategy_config['rr_target'] = trial.suggest_float('rr_target', 1.0, 4.0)
    strategy_config['sl_atr_multiplier'] = trial.suggest_float('sl_atr_multiplier', 1.0, 5.0)
    strategy_config['rsi_oversold'] = trial.suggest_int('rsi_oversold', 15, 35)
    strategy_config['rsi_overbought'] = trial.suggest_int('rsi_overbought', 65, 85)
    strategy_config['timeframe'] = 'M5'

    broker = BacktestBroker(processed_data=processed_data, config=temp_config, initial_equity=200.0)
    strategy = EMARsiTrendScalper(symbol=symbol, config=strategy_config, broker_context=broker)

    while broker.advance_time():
        strategy.run_once(current_bar=broker.get_current_bar_data())

    results = broker.generate_report()
    
    profit_factor = results["profit_factor"]
    max_drawdown = results["max_drawdown"]
    total_trades = results["total_trades"]

    if total_trades < 50 or max_drawdown > max_allowed_drawdown:
        if trial.number % 10 == 0:
            print(f"[WARN] Trial {trial.number} eșuat: Trades={total_trades}, DD={max_drawdown:.2f}%")
        return - (max_drawdown / max_allowed_drawdown) if max_drawdown > 0 else -1.0
    
    # Afișăm un log la fiecare 10 trial-uri reușite
    if trial.number % 10 == 0:
        print(f"[INFO] Trial {trial.number} OK: PF={profit_factor:.2f}, DD={max_drawdown:.2f}%, Trades={total_trades}")
    return profit_factor


# --- BLOCUL PRINCIPAL (MODIFICAT PENTRU A RULA ÎN BUCLĂ) ---
if __name__ == "__main__":
    
    # --- Setări Globale pentru Optimizare ---
    SYMBOLS_TO_OPTIMIZE = [
        "EURUSD",
        "GBPUSD",
        "USDCAD",
        "USDCHF",
        "USDJPY",
        "AUDUSD",
        "NZDUSD",
        "AUDJPY",
        "EURJPY",
        "EURGBP",
        "GBPJPY" 
    ]
    MAX_DRAWDOWN_LIMIT = 30.0
    TOTAL_TRIALS_PER_SYMBOL = 250 # Numărul de teste pentru fiecare simbol
    DATA_SUFFIX = "_1Y" # Folosim datele de 1 An pentru optimizare
    # ----------------------------------------
    
    with open("config/config.yaml", 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # Listă pentru a stoca rezultatele finale
    all_best_results = []
    
    print("="*50)
    print(f"Început Optimizare Portofoliu (Batch) pentru {len(SYMBOLS_TO_OPTIMIZE)} simboluri.")
    print(f"Total Teste per Simbol: {TOTAL_TRIALS_PER_SYMBOL}")
    print("="*50)

    # --- BUCLA PRINCIPALĂ ---
    for symbol in SYMBOLS_TO_OPTIMIZE:
        
        print("\n" + "="*50)
        print(f"--- Procesare Simbol: {symbol} ---")
        print("="*50)

        DATA_PATHS = {
            "M5": f"data/{symbol}_M5{DATA_SUFFIX}.csv",
            "H1": f"data/{symbol}_H1{DATA_SUFFIX}.csv"
        }
        
        study_name = f"ema_rsi_m5_{symbol}_v1" # Nume unic pentru studiu
        storage_name = "sqlite:///optimization_studies.db"

        study = optuna.create_study(
            study_name=study_name,
            storage=storage_name,
            direction="maximize",
            load_if_exists=True # Permite reluarea dacă crapă
        )

        print(f"--- Început pre-procesare o singură dată pentru {symbol} ---")
        
        h1_ema_period = config.get('strategies',{}).get('ema_rsi_scalper',{}).get('h1_ema_period', 50)
        m5_atr_period = config.get('strategies',{}).get('ema_rsi_scalper',{}).get('m5_atr_period', 14)
        m5_rsi_period = config.get('strategies',{}).get('ema_rsi_scalper',{}).get('m5_rsi_period', 14)

        try:
            processed_data = preprocess_data_ema(DATA_PATHS, symbol, h1_ema_period, m5_atr_period, m5_rsi_period)
            print("--- Pre-procesare finalizată. Se pornește optimizarea... ---")
        except FileNotFoundError:
            print(f"EROARE: Datele pentru {symbol} ({DATA_SUFFIX}) nu au fost găsite. Se sare la următorul simbol.")
            all_best_results.append({"symbol": symbol, "status": "EȘUAT (Date Lipsă)", "profit_factor": 0})
            continue
        except Exception as e:
            print(f"EROARE FATALĂ la pre-procesarea datelor pentru {symbol}: {e}")
            traceback.print_exc()
            all_best_results.append({"symbol": symbol, "status": f"EȘUAT ({e})", "profit_factor": 0})
            continue

        completed_trials = len(study.trials)
        trials_to_run = max(0, TOTAL_TRIALS_PER_SYMBOL - completed_trials)
        
        print(f"Stocare: {storage_name} (Studiu: {study_name})")
        print(f"Obiectiv: Maximizare Profit Factor, cu Drawdown Maxim <= {MAX_DRAWDOWN_LIMIT}%")
        print(f"Teste finalizate anterior: {completed_trials}")
        print(f"Teste rămase de rulat: {trials_to_run}")

        if trials_to_run > 0:
            objective_func = lambda trial: objective(trial, config, symbol, MAX_DRAWDOWN_LIMIT, processed_data)
            study.optimize(objective_func, n_trials=trials_to_run, show_progress_bar=True)

        print(f"--- Optimizare finalizată pentru {symbol} ---")

        try:
            best_trial = study.best_trial
            if best_trial.value < 0:
                print(f"Rezultat {symbol}: Nicio combinație validă găsită (DD > {MAX_DRAWDOWN_LIMIT}%).")
                all_best_results.append({"symbol": symbol, "status": "EȘUAT (DD Prea Mare)", "profit_factor": best_trial.value})
            else:
                print(f"Rezultat {symbol}: PF={best_trial.value:.4f}")
                result_summary = {"symbol": symbol, "status": "Succes", "profit_factor": best_trial.value}
                result_summary.update(best_trial.params) # Adaugă parametrii găsiți
                all_best_results.append(result_summary)
        except ValueError:
            print(f"Rezultat {symbol}: Niciun trial nu s-a completat cu succes.")
            all_best_results.append({"symbol": symbol, "status": "EȘUAT (Niciun trial)", "profit_factor": 0})
    
    # --- RAPORTUL FINAL DE PORTOFOLIU ---
    print("\n" + "="*80)
    print("--- REZUMATUL OPTIMIZĂRII PORTOFOLIULUI (1 AN) ---")
    print("="*80)
    
    # Formatăm și afișăm rezultatele
    results_df = pd.DataFrame(all_best_results)
    print(results_df.to_string(index=False, float_format="%.4f"))
    
    # Salvăm rezultatele într-un CSV
    results_df.to_csv("portfolio_optimization_summary.csv", index=False)
    print("\n" + "="*80)
    print("Rezultatele complete au fost salvate în 'portfolio_optimization_summary.csv'")
    print("="*80)