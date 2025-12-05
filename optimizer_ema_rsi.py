# optimizer_ema_rsi.py - CLEAN & FULL SYMBOLS (M5/H1)

import pandas as pd
import yaml
import optuna
import copy
import sys
import traceback
import os
from core.backtest_broker import BacktestBroker
from strategies.ema_rsi_scalper import EMARsiTrendScalper

# --- 1. Funcții Helper pentru Date ---

def load_and_prepare_data(file_path):
    """Încarcă datele din CSV (Format MT5 Export)."""
    try:
        data = pd.read_csv(file_path, header=0, sep='\t')
        # Renumire coloane standard
        data.rename(columns={'<DATE>': 'date', '<TIME>': 'time', '<OPEN>': 'open', '<HIGH>': 'high', '<LOW>': 'low', '<CLOSE>': 'close'}, inplace=True)
        # Parsare datetime
        data['datetime'] = pd.to_datetime(data['date'] + ' ' + data['time'], format='%Y.%m.%d %H:%M:%S')
        data.set_index('datetime', inplace=True)
        # Eliminare timezone pentru compatibilitate
        data.index = data.index.tz_localize(None)
        return data[['open', 'high', 'low', 'close']].copy()
    except Exception as e:
        print(f"EROARE la încărcarea {file_path}: {e}")
        return None

def preprocess_data_static(data_paths, symbol, config):
    """
    Calculează indicatorii o singură dată (Perioade FIXE).
    Optimizarea se face pe PRAGURI, nu pe perioade, pentru eficiență maximă.
    """
    print(f"[INFO] Pre-procesare date {symbol} (M5/H1)...")
    
    # 1. Extragem perioadele FIXE din config
    ema_period = config.get('ema_period', 50)
    atr_period = config.get('atr_period', 14)
    rsi_period = config.get('rsi_period', 14)
    
    df_base = load_and_prepare_data(data_paths['M5']) # Timeframe BAZĂ (Execuție)
    df_trend = load_and_prepare_data(data_paths['H1']) # Timeframe TREND (Filtru)
    
    if df_base is None or df_trend is None:
        raise FileNotFoundError(f"Date lipsă pentru {symbol}")

    # 2. Calculăm Indicatorii
    # Trend (H1) -> EMA 50
    df_trend['H1_ema_trend'] = df_trend['close'].ewm(span=ema_period, adjust=False).mean()
    df_trend['H1_trend_up'] = df_trend['close'] > df_trend['H1_ema_trend']
    
    # Semnal (M5) -> RSI, ATR
    df_base['M5_atr'] = EMARsiTrendScalper._calculate_atr(df_base, atr_period, 'ema')
    df_base['M5_rsi'] = EMARsiTrendScalper._calculate_rsi(df_base, rsi_period)
    
    # 3. Merge (Aliniere date H1 pe M5)
    df_trend_merge = df_trend[['H1_trend_up']]
    combined_df = pd.merge_asof(
        df_base, df_trend_merge,
        left_index=True, right_index=True,
        direction='backward'
    )
    combined_df.dropna(inplace=True)
    return combined_df

# --- 2. Funcția Obiectiv (Optuna) ---

def objective(trial, base_config, symbol, max_allowed_drawdown, processed_data):
    # 1. Configurare Mediu
    # Deepcopy pentru a nu altera config-ul original între trial-uri
    temp_config = copy.deepcopy(base_config)
    strategy_conf = temp_config['strategies']['ema_rsi_scalper']

    # === 2. SPAȚIUL DE CĂUTARE (Parametri Dinamici) ===
    
    # A. Management Risc (Adaptare la volatilitate)
    # Cât "spațiu" dăm tranzacției (SL) și cât cerem (TP)
    strategy_conf['sl_atr_multiplier'] = trial.suggest_float('sl_atr_multiplier', 1.0, 3.0, step=0.1)
    strategy_conf['rr_target'] = trial.suggest_float('rr_target', 1.0, 3.0, step=0.1)
    
    # B. Intrare (Sensibilitate și Filtre)
    # Praguri RSI: Cât de extrem trebuie să fie semnalul?
    strategy_conf['rsi_oversold'] = trial.suggest_int('rsi_oversold', 20, 35, step=5)
    strategy_conf['rsi_overbought'] = trial.suggest_int('rsi_overbought', 65, 80, step=5)
    
    # Filtru Proximitate EMA: Critic pentru diferența dintre perechi calme vs. volatile
    # EURUSD poate vrea 4-6 pips, GBPJPY poate vrea 10-12 pips
    strategy_conf['ema_distance_pips'] = trial.suggest_float('ema_distance_pips', 3.0, 12.0, step=1.0)
    
    # C. Ieșire (Profit Lock / Trailing)
    # Cât de agresiv securizăm profitul pe parcurs?
    temp_config['trailing']['profit_lock_percent'] = trial.suggest_float('profit_lock_percent', 0.6, 0.9, step=0.05)
    
    # === 3. PARAMETRI STATICI (Fixați) ===
    # Asigurăm timeframe-urile corecte (M5/H1) conform strategiei
    strategy_conf['timeframe'] = 'M5'
    strategy_conf['timeframe_trend'] = 'H1'
    # Perioadele (50, 14, 14) sunt deja "arse" în processed_data, deci nu le modificăm aici.

    # === 4. RULARE BACKTEST ===
    broker = BacktestBroker(config=temp_config, initial_equity=1000.0)
    strategy = EMARsiTrendScalper(symbol=symbol, config=strategy_conf, broker_context=broker)
    
    # Iterăm prin datele pre-procesate (Execuție Rapidă)
    for index, row in processed_data.iterrows():
        # Setăm datele curente în broker
        broker.set_current_data(index, {symbol: row})
        # Rulăm logica strategiei (care citește H1_trend_up, M5_rsi etc. din row)
        strategy.run_once(current_bar=row)
        # Actualizăm pozițiile
        broker.update_all_positions()

    # === 5. EVALUARE & PENALIZĂRI ===
    # Scriem raportul într-un fișier "junk" care se suprascrie la fiecare iterație
    junk_file = "temp_opt_junk.txt"
    report = broker.generate_portfolio_report([symbol], report_filename=junk_file)
    
    pf = report.get("profit_factor", 0.0)
    dd = report.get("max_drawdown_pct", 100.0)
    trades = report.get("total_trades", 0)

    # Penalizări pentru rezultate invalide statistic
    if trades < 30: 
        return 0.0      # Prea puține tranzacții pentru a fi relevant
    if dd > max_allowed_drawdown: 
        return -dd      # Penalizare directă cu valoarea Drawdown-ului dacă depășește limita
    
    # Obiectivul este maximizarea Profit Factor-ului
    return pf

# --- 3. Main Loop ---

if __name__ == "__main__":
    
    # LISTA COMPLETĂ DE SIMBOLURI
    SYMBOLS_TO_OPTIMIZE = [
        "EURUSD", "GBPUSD", "USDCAD", "USDCHF", "USDJPY",
        "AUDUSD", "NZDUSD", "AUDJPY", "EURJPY", "EURGBP", "GBPJPY"
    ]
    
    MAX_DRAWDOWN_LIMIT = 25.0 # Maxim acceptat 25% DD
    TOTAL_TRIALS = 250        # Număr teste per simbol
    
    # Încărcare Config de Bază
    try:
        with open("config/config.yaml", 'r', encoding='utf-8') as f:
            base_config = yaml.safe_load(f)
    except FileNotFoundError:
        print("❌ EROARE: config/config.yaml nu a fost găsit.")
        sys.exit(1)

    all_best_results = []
    
    print(f"🚀 Pornire Optimizare pentru {len(SYMBOLS_TO_OPTIMIZE)} simboluri.")
    
    for symbol in SYMBOLS_TO_OPTIMIZE:
        print("\n" + "="*60)
        print(f"📊 Optimizare: {symbol}")
        print("="*60)
        
        # Căi fișiere (Asigură-te că ai datele _9Y.csv sau _2Y.csv în folder)
        # Aici presupunem _2Y pentru optimizare (mai rapid) sau poți schimba în _9Y
        paths = {
            "M5": f"data/{symbol}_M5_2Y.csv",
            "H1": f"data/{symbol}_H1_2Y.csv"
        }
        
        # 1. Pre-procesare (O singură dată per simbol)
        try:
            processed_data = preprocess_data_static(paths, symbol, base_config['strategies']['ema_rsi_scalper'])
            print(f"✅ Date încărcate: {len(processed_data)} bare.")
        except FileNotFoundError:
            print(f"⚠️ Skip {symbol}: Fișierele de date lipsesc.")
            continue
        except Exception as e:
            print(f"❌ Skip {symbol}: Eroare la procesare ({e})")
            continue

        # 2. Optuna Study
        study_name = f"study_{symbol}_m5h1"
        storage_url = f"sqlite:///opt_results.db" # Bază de date unică
        
        study = optuna.create_study(
            study_name=study_name,
            storage=storage_url,
            direction="maximize",
            load_if_exists=True
        )
        
        # Calculăm câte trial-uri mai avem de făcut
        remaining_trials = max(0, TOTAL_TRIALS - len(study.trials))
        
        if remaining_trials > 0:
            print(f"Running {remaining_trials} trials...")
            # Lambda pentru a pasa argumentele statice
            optimize_func = lambda t: objective(t, base_config, symbol, MAX_DRAWDOWN_LIMIT, processed_data)
            study.optimize(optimize_func, n_trials=remaining_trials, show_progress_bar=True)
        else:
            print("Toate trial-urile au fost deja executate.")

        # 3. Rezultate
        try:
            best = study.best_trial
            print(f"🏆 BEST {symbol}: PF={best.value:.2f} | Params: {best.params}")
            
            # Salvăm rezultatul
            res = {"symbol": symbol, "profit_factor": best.value}
            res.update(best.params)
            all_best_results.append(res)
            
        except ValueError:
            print(f"❌ Niciun rezultat valid găsit pentru {symbol}.")

    # --- Raport Final ---
    if all_best_results:
        df_res = pd.DataFrame(all_best_results)
        df_res.to_csv("optimization_summary_final.csv", index=False)
        print("\n✅ Optimizare Finalizată! Rezultate salvate în 'optimization_summary_final.csv'.")
        
        # Curățenie fișier temporar
        if os.path.exists("temp_opt_junk.txt"):
            os.remove("temp_opt_junk.txt")