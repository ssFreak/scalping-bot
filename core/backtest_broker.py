# core/backtest_broker.py - BE & PROFIT LOCK ENABLED

import pandas as pd
from datetime import datetime
import numpy as np

class BacktestBroker:
    def __init__(self, config: dict, initial_equity=1000.0, commission_per_lot=0.07, spread_pips=2.0):
        self.config = config
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.balance = initial_equity
        self.equity_history = [] 
        
        self.current_tick_data = {} 
        self.current_timestamp = None
            
        self.open_positions = [] 
        self.trade_history = []
        
        self._next_ticket_id = 1
        self.commission_per_lot = commission_per_lot
        self.spread_pips = self.config.get('general', {}).get('spread_pips', spread_pips)
        
        # Dicționar pentru a urmări Profit Lock (ca să nu mutăm SL înapoi)
        self.profit_lock_tracker = {} 

        # Setup Logger simplu
        self.logger = self._setup_logger()

    def _setup_logger(self):
        class SimpleLogger:
            def log(self, message, level="info"):
                pass # Silentios în backtest
        return SimpleLogger()

    def set_current_data(self, timestamp, data_for_all_symbols: dict):
        self.current_timestamp = timestamp
        self.current_tick_data = data_for_all_symbols

    # --- CALCUL LOT DINAMIC (CRUCIAL PENTRU COMPOUNDING) ---
    def calculate_lot_size(self, symbol, stop_loss_price):
        try:
            risk_pct = self.config.get('general', {}).get('risk_per_trade', 0.01)
            risk_amount = self.equity * float(risk_pct)
            
            tick = self.current_tick_data.get(symbol)
            if tick is None: return 0.01
            
            current_price = tick['close'] if 'close' in tick else 0.0
            if current_price == 0: return 0.01

            sl_dist = abs(current_price - stop_loss_price)
            if sl_dist == 0: return 0.01

            pip_val = 1000.0 if "JPY" in symbol else 100000.0
            risk_per_lot = sl_dist * pip_val
            
            if risk_per_lot <= 0: return 0.01
            
            lot = risk_amount / risk_per_lot
            
            max_lot = self.config.get('general', {}).get('max_position_lot', 2.0)
            lot = max(0.01, min(lot, max_lot))
            
            return round(lot, 2)
        except:
            return 0.01

    # --- SIMULARE TRAILING, BE & PROFIT LOCK ---
    def _apply_trailing_logic(self, pos, current_price):
        """
        Simulează logica de TradeManager pe baza prețului curent (Close).
        """
        trailing_cfg = self.config.get('trailing', {})
        if not trailing_cfg.get('enabled', False): return

        symbol = pos['symbol']
        ticket = pos['ticket']
        entry_price = pos['entry_price']
        sl = pos['sl']
        tp = pos['tp']
        p_type = pos['type'] # 0=BUY, 1=SELL
        
        pip = 0.01 if "JPY" in symbol else 0.0001
        
        # 1. Break Even Logic
        be_points = float(trailing_cfg.get("be_min_profit_points", 0))
        be_secure = float(trailing_cfg.get("be_secured_points", 0))
        
        if be_points > 0:
            if p_type == 0: # BUY
                profit_pts = (current_price - entry_price) / pip
                if profit_pts >= be_points:
                    new_sl = entry_price + (be_secure * pip)
                    if new_sl > sl: # Mutăm doar în profit
                        pos['sl'] = round(new_sl, 5)
            else: # SELL
                profit_pts = (entry_price - current_price) / pip
                if profit_pts >= be_points:
                    new_sl = entry_price - (be_secure * pip)
                    if sl == 0 or new_sl < sl:
                        pos['sl'] = round(new_sl, 5)

        # 2. Profit Lock Logic (% din TP)
        profit_lock_pct = float(trailing_cfg.get("profit_lock_percent", 0.0))
        
        if profit_lock_pct > 0 and tp > 0:
            total_dist = abs(tp - entry_price)
            curr_dist = abs(current_price - entry_price)
            
            if total_dist > 0:
                pct_reached = curr_dist / total_dist
                
                # Verificăm dacă am atins % necesar (ex: 85%)
                if pct_reached >= profit_lock_pct:
                    # Calculăm noul SL "Locked"
                    # Lăsăm un mic buffer (0.02) ca pe live
                    lock_dist = total_dist * (profit_lock_pct - 0.02)
                    
                    if p_type == 0: # BUY
                        target_sl = entry_price + lock_dist
                        # Update doar dacă e mai bun
                        if target_sl > pos['sl']:
                            pos['sl'] = round(target_sl, 5)
                    else: # SELL
                        target_sl = entry_price - lock_dist
                        # Update doar dacă e mai bun
                        if pos['sl'] == 0 or target_sl < pos['sl']:
                            pos['sl'] = round(target_sl, 5)

    def update_all_positions(self):
        if self.current_timestamp is None: return
        
        positions_to_close = []
        
        for pos in self.open_positions:
            symbol = pos['symbol']
            tick = self.current_tick_data.get(symbol)
            if tick is None: continue
            
            # Datele barei curente
            curr = tick['close']
            high = tick['high']
            low = tick['low']

            # A. Verificare SL/TP (Hit Check)
            # Verificăm High/Low pentru a vedea dacă prețul a atins limitele în timpul barei
            closed = False
            
            if pos['type'] == 0: # BUY
                if pos['sl'] > 0 and low <= pos['sl']: 
                    positions_to_close.append((pos, pos['sl'])) 
                    closed = True
                elif pos['tp'] > 0 and high >= pos['tp']: 
                    positions_to_close.append((pos, pos['tp'])) 
                    closed = True
            else: # SELL
                if pos['sl'] > 0 and high >= pos['sl']: 
                    positions_to_close.append((pos, pos['sl'])) 
                    closed = True
                elif pos['tp'] > 0 and low <= pos['tp']: 
                    positions_to_close.append((pos, pos['tp'])) 
                    closed = True
            
            # B. Actualizare PnL Floating
            if not closed:
                pip_val = 1000.0 if "JPY" in symbol else 100000.0
                if pos['type'] == 0:
                    pnl = (curr - pos['entry_price']) * pos['lot'] * pip_val
                else:
                    pnl = (pos['entry_price'] - curr) * pos['lot'] * pip_val
                pos['pnl'] = pnl
                
                # C. APLICARE TRAILING (BE / LOCK) PENTRU BARA URMĂTOARE
                # Folosim prețul de 'close' al barei curente pentru a decide mutarea SL
                # Asta este metoda "Honest" (fără lookahead pe high/low)
                self._apply_trailing_logic(pos, curr)

        # Executare închideri
        for pos, close_price in positions_to_close:
            self._close_position(pos, close_price)
            
        # Actualizare Equity
        floating_pnl = sum(p['pnl'] for p in self.open_positions)
        self.equity = self.balance + floating_pnl
        self.equity_history.append((self.current_timestamp, self.equity))

    # --- RESTUL METODELOR STANDARD ---
    def get_open_positions(self, symbol: str, magic_number: int = None):
        filtered = []
        for pos in self.open_positions:
            if pos['symbol'] == symbol:
                if magic_number is None or pos['magic'] == magic_number:
                    filtered.append(pos)
        return filtered

    def get_pip_size(self, symbol: str) -> float:
        return 0.01 if "JPY" in symbol else 0.0001
    
    def get_digits(self, symbol: str) -> int:
        return 3 if "JPY" in symbol else 5
    
    def get_historical_data(self, symbol, timeframe, count):
        return None 

    def open_market_order(self, symbol, order_type, lot, sl, tp, magic_number, comment="", **kwargs):
        tick = self.current_tick_data.get(symbol)
        if tick is None: return None
        try:
            close_price = tick['close']
        except: return None
            
        pip_size = self.get_pip_size(symbol)
        spread_cost = self.spread_pips * pip_size
        
        if order_type == 0: # BUY
            entry_price = close_price + (spread_cost/2)
        else: # SELL
            entry_price = close_price - (spread_cost/2)
            
        entry_price = round(entry_price, self.get_digits(symbol))
        
        commission = self.commission_per_lot * lot
        self.balance -= commission
        
        position = {
            "ticket": self._next_ticket_id, 
            "symbol": symbol, 
            "type": order_type,
            "lot": lot, 
            "entry_price": entry_price, 
            "entry_time": self.current_timestamp,
            "sl": sl, 
            "tp": tp, 
            "magic": magic_number, 
            "comment": comment, 
            "pnl": -commission
        }
        self.open_positions.append(position)
        self._next_ticket_id += 1
        return position['ticket']

    def close_position(self, symbol: str, ticket: int, magic_number: int):
        pos_to_close = None
        for pos in self.open_positions:
            if pos['ticket'] == ticket:
                pos_to_close = pos
                break
        
        if pos_to_close:
            tick = self.current_tick_data.get(symbol)
            if tick is None: return False
            close_price_raw = tick['close']
            pip_size = self.get_pip_size(symbol)
            spread_cost = self.spread_pips * pip_size
            if pos_to_close['type'] == 0: 
                final_price = close_price_raw - (spread_cost/2)
            else: 
                final_price = close_price_raw + (spread_cost/2)
            self._close_position(pos_to_close, final_price)
            return True
        return False
        
    def apply_trailing_stop(self, symbol, position, atr_price, pip, params):
        # Compatibilitate cu apelurile vechi, dar logica e acum internă în _apply_trailing_logic
        pass 

    def _close_position(self, position, close_price):
        if position in self.open_positions:
            pip_val = 1000.0 if "JPY" in position['symbol'] else 100000.0
            if position['type'] == 0: 
                pnl_final = (close_price - position['entry_price']) * position['lot'] * pip_val
            else: 
                pnl_final = (position['entry_price'] - close_price) * position['lot'] * pip_val
            
            position['exit_price'] = close_price
            position['exit_time'] = self.current_timestamp
            position['pnl'] = pnl_final
            self.balance += pnl_final
            self.trade_history.append(position)
            self.open_positions.remove(position)

    def generate_portfolio_report(self, symbols_tested, report_filename="portfolio_report.txt"):
        # ... (Codul de raportare rămâne neschimbat, e doar afișare) ...
        # Poți folosi implementarea din mesajele anterioare
        if not self.trade_history:
            print("--- RAPORT: Nicio tranzacție executată. ---")
            return {"total_trades": 0, "profit_factor": 0.0, "max_drawdown_pct": 0.0}

        total_trades = len(self.trade_history)
        total_pnl = sum(t['pnl'] for t in self.trade_history)
        wins = [t for t in self.trade_history if t['pnl'] > 0]
        losses = [t for t in self.trade_history if t['pnl'] <= 0]
        
        gross_profit = sum(t['pnl'] for t in wins)
        gross_loss = abs(sum(t['pnl'] for t in losses))
        
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.9
        win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
        
        df_eq = pd.DataFrame(self.equity_history, columns=['datetime', 'equity'])
        if not df_eq.empty:
            df_eq.set_index('datetime', inplace=True)
            peak = df_eq['equity'].cummax()
            dd = (df_eq['equity'] - peak) / peak
            max_dd = abs(dd.min()) * 100
        else:
            max_dd = 0.0

        lines = [
            f"=== RAPORT VALIDARE GLOBAL ===",
            f"Total Tranzactii: {total_trades}",
            f"Profit Net: ${total_pnl:.2f}",
            f"Profit Factor: {profit_factor:.2f}",
            f"Win Rate: {win_rate:.2f}%",
            f"Max Drawdown: {max_dd:.2f}%",
            "=============================",
            "=== PERFORMANȚĂ PER SIMBOL ==="
        ]

        symbol_stats = {}
        for t in self.trade_history:
            sym = t['symbol']
            if sym not in symbol_stats:
                symbol_stats[sym] = {'pnl': 0.0, 'wins': 0, 'loss_val': 0.0, 'win_val': 0.0, 'count': 0}
            stats = symbol_stats[sym]
            stats['pnl'] += t['pnl']
            stats['count'] += 1
            if t['pnl'] > 0:
                stats['wins'] += 1
                stats['win_val'] += t['pnl']
            else:
                stats['loss_val'] += abs(t['pnl'])

        sorted_syms = sorted(symbol_stats.items(), key=lambda item: item[1]['pnl'], reverse=True)

        for sym, stats in sorted_syms:
            pf = stats['win_val'] / stats['loss_val'] if stats['loss_val'] > 0 else 99.9
            dd_warning = "✅" if pf >= 1.5 else ("⚠️" if pf >= 1.0 else "❌")
            lines.append(f"{dd_warning} {sym}: PF {pf:.2f} | PnL ${stats['pnl']:.2f} | Trades: {stats['count']}")

        lines.append("=============================")
        print("\n".join(lines))
        
        with open(report_filename, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            
        return {"total_trades": total_trades, "profit_factor": profit_factor, "max_drawdown_pct": max_dd}