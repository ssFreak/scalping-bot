# core/backtest_broker.py - FINAL ROBUST VERSION

import pandas as pd
from datetime import datetime
import traceback 
import numpy as np

class BacktestBroker:
    def __init__(self, config: dict, initial_equity=1000.0, commission_per_lot=0.07, spread_pips=2.0):
        self.logger = self._setup_logger()
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
        self.profit_lock_tracker = {} 

    def _setup_logger(self):
        class SimpleLogger:
            def log(self, message, level="info"):
                # Debugging activat pentru erori
                if level in ["error", "warning"]: print(f"[{level.upper()}] {message}")
        return SimpleLogger()

    def set_current_data(self, timestamp, data_for_all_symbols: dict):
        self.current_timestamp = timestamp
        self.current_tick_data = data_for_all_symbols

    def calculate_lot_size(self, symbol, stop_loss_price):
        """
        Calculează lotul dinamic. Dacă ceva nu merge, returnează 0.01 ca fallback.
        """
        try:
            # 1. Extrage Risc
            risk_pct = self.config.get('general', {}).get('risk_per_trade', 0.01)
            risk_amount = self.equity * float(risk_pct)
            
            # 2. Preț Curent
            tick = self.current_tick_data.get(symbol)
            if tick is None: return 0.01
            
            # Accesare sigură (Pandas Series)
            current_price = tick['close'] if 'close' in tick else 0.0
            if current_price == 0: return 0.01

            # 3. Distanța SL
            sl_dist = abs(current_price - stop_loss_price)
            if sl_dist == 0: return 0.01

            # 4. Valoare Pip (Aproximare standard)
            pip_val = 1000.0 if "JPY" in symbol else 100000.0
            
            # 5. Risc per 1 Lot
            risk_per_lot = sl_dist * pip_val
            
            if risk_per_lot <= 0: return 0.01
            
            # 6. Calcul Final
            lot = risk_amount / risk_per_lot
            
            # Limite
            max_lot = self.config.get('general', {}).get('max_position_lot', 1.0)
            lot = max(0.01, min(lot, max_lot))
            
            return round(lot, 2)
            
        except Exception as e:
            # În caz de eroare matematică, returnăm minimul pentru a nu bloca tranzacția
            # print(f"Err Lot Calc {symbol}: {e}")
            return 0.01

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
        
        # Verificare date
        if tick is None: return None
        try:
            close_price = tick['close']
        except:
            return None
            
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
        
        # DEBUG: Confirmare tranzacție
        # print(f"✅ OPEN {symbol} {lot} lots @ {entry_price}")
        
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
        self.apply_trailing(position)

    def apply_trailing(self, position):
        symbol = position['symbol']
        tick = self.current_tick_data.get(symbol)
        if tick is None: return

        current_price = tick['close']
        entry_price = position['entry_price']
        tp = position['tp']
        order_type = position['type']
        ticket = position['ticket']
        
        digits = self.get_digits(symbol)
        trailing_cfg = self.config.get('trailing', {})
        profit_lock_pct = float(trailing_cfg.get('profit_lock_percent', 0.85))

        if profit_lock_pct > 0 and tp > 0:
            total_dist = abs(tp - entry_price)
            current_dist = abs(current_price - entry_price)
            
            if total_dist > 0:
                pct_reached = current_dist / total_dist
                
                if ticket in self.profit_lock_tracker:
                    recorded_lock = self.profit_lock_tracker[ticket]
                    triggered = False
                    if order_type == 0 and current_price < recorded_lock: triggered = True
                    elif order_type == 1 and current_price > recorded_lock: triggered = True
                        
                    if triggered:
                        self._close_position(position, current_price)
                        del self.profit_lock_tracker[ticket]
                        return 

                if pct_reached >= profit_lock_pct:
                    if order_type == 0:
                        lock_price = entry_price + (total_dist * profit_lock_pct)
                    else:
                        lock_price = entry_price - (total_dist * profit_lock_pct)
                    self.profit_lock_tracker[ticket] = round(lock_price, digits)

    def update_all_positions(self):
        if self.current_timestamp is None: return
        
        positions_to_close = []
        
        for pos in self.open_positions:
            symbol = pos['symbol']
            tick = self.current_tick_data.get(symbol)
            if tick is None: continue
            
            curr = tick['close']
            high = tick['high']
            low = tick['low']

            # Verificare SL/TP pe High/Low pentru acuratețe
            if pos['type'] == 0: # BUY
                if pos['sl'] > 0 and low <= pos['sl']: 
                    positions_to_close.append((pos, pos['sl'])) 
                elif pos['tp'] > 0 and high >= pos['tp']: 
                    positions_to_close.append((pos, pos['tp'])) 
            else: # SELL
                if pos['sl'] > 0 and high >= pos['sl']: 
                    positions_to_close.append((pos, pos['sl'])) 
                elif pos['tp'] > 0 and low <= pos['tp']: 
                    positions_to_close.append((pos, pos['tp'])) 
            
            pip_val = 1000.0 if "JPY" in symbol else 100000.0
            if pos['type'] == 0:
                pnl = (curr - pos['entry_price']) * pos['lot'] * pip_val
            else:
                pnl = (pos['entry_price'] - curr) * pos['lot'] * pip_val
            pos['pnl'] = pnl

        for pos, close_price in positions_to_close:
            self._close_position(pos, close_price)
            
        floating_pnl = sum(p['pnl'] for p in self.open_positions)
        self.equity = self.balance + floating_pnl
        self.equity_history.append((self.current_timestamp, self.equity))
        
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