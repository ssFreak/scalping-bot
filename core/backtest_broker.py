# core/backtest_broker.py
import pandas as pd
from datetime import datetime
import traceback 

class BacktestBroker:
    def __init__(self, processed_data: pd.DataFrame, config: dict, initial_equity=200.0, commission_per_lot=0.07, spread_pips=0.2):
        self.logger = self._setup_logger()
        self.config = config
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.balance = initial_equity
        self.equity_history = [initial_equity]
        self.processed_data = processed_data 
        self.current_tick_index = 0
        self.open_positions = []
        self.trade_history = []
        self.commission_per_lot = commission_per_lot
        self.spread_pips = spread_pips
        # Am scos logul de inițializare

    def _setup_logger(self):
        class SimpleLogger:
            def log(self, message, level="info"):
                if "optuna" not in "".join(traceback.format_stack()):
                    print(f"[{level.upper()}] {message}")
        return SimpleLogger()

    def calculate_lot_size(self, symbol, stop_loss_pips):
        risk_per_trade = self.config.get('general', {}).get('risk_per_trade', 0.01)
        risk_amount = self.equity * risk_per_trade
        # Calcul dinamic al valorii pip-ului
        pip_value_per_lot = 10.0 # Default pentru XXXUSD
        if "JPY" in symbol: 
            pip_value_per_lot = 1000.0 / self.get_current_bar_data().close # Aproximare mult mai bună
        elif "CHF" in symbol:
             pip_value_per_lot = 10.0 / (self.get_current_bar_data().close if "USDCHF" in symbol else 1.0)
        
        if stop_loss_pips <= 0: return 0.0
        risk_per_lot = stop_loss_pips * pip_value_per_lot
        if risk_per_lot <= 0: return 0.0
        raw_lot = risk_amount / risk_per_lot
        lot = round(raw_lot, 2)
        min_lot = 0.01
        max_lot = self.config.get('general', {}).get('max_position_lot', 1.0)
        return max(min_lot, min(lot, max_lot))

    def has_open_position(self):
        return len(self.open_positions) > 0

    def get_current_bar_data(self):
        if self.current_tick_index < len(self.processed_data):
            return self.processed_data.iloc[self.current_tick_index]
        return None

    def advance_time(self):
        if self.current_tick_index < len(self.processed_data) - 1:
            self.current_tick_index += 1
            self._update_equity_and_positions()
            return True
        return False

    def get_pip_size(self, symbol: str) -> float:
        return 0.01 if "JPY" in symbol else 0.0001 # Valoarea unui pip

    # --- METODA NOUĂ ---
    def get_digits(self, symbol: str) -> int:
        """Returnează numărul de zecimale pentru rotunjirea prețului."""
        return 3 if "JPY" in symbol else 5
    # -------------------

    def get_timeframe(self, tf_str: str):
        return tf_str

    def open_market_order(self, symbol, order_type, lot, sl, tp, magic_number, comment=""):
        tick = self.get_current_bar_data()
        pip_size = self.get_pip_size(symbol)
        spread_cost = self.spread_pips * pip_size
        entry_price = tick.close + (spread_cost / 2 if order_type == 0 else -spread_cost / 2)
        commission = self.commission_per_lot * lot
        self.balance -= commission
        position = {"ticket": len(self.trade_history) + 1, "symbol": symbol, "type": order_type,"lot": lot, "entry_price": entry_price, "entry_time": tick.name,"sl": sl, "tp": tp, "magic": magic_number, "comment": comment, "pnl": 0.0}
        self.open_positions.append(position)
        return position

    def _update_equity_and_positions(self):
        tick = self.get_current_bar_data()
        if tick is None: return
        floating_pnl = 0
        positions_to_close = []
        for pos in self.open_positions:
            pip_value = 100000
            if "JPY" in pos['symbol']:
                pip_value = 1000 # Corecție: JPY are 1000 unități per 0.01
            
            if pos['type'] == 0: # BUY
                pnl = (tick.close - pos['entry_price']) * (pos['lot'] * pip_value)
                if pos['sl'] and tick.low <= pos['sl']: positions_to_close.append((pos, pos['sl']))
                elif pos['tp'] and tick.high >= pos['tp']: positions_to_close.append((pos, pos['tp']))
            else: # SELL
                pnl = (pos['entry_price'] - tick.close) * (pos['lot'] * pip_value)
                if pos['sl'] and tick.high >= pos['sl']: positions_to_close.append((pos, pos['sl']))
                elif pos['tp'] and tick.low <= pos['tp']: positions_to_close.append((pos, pos['tp']))
            pos['pnl'] = pnl
            floating_pnl += pnl
        
        for pos, close_price in positions_to_close:
            self._close_position(pos, close_price)
            
        self.equity = self.balance + floating_pnl
        self.equity_history.append(self.equity)
        
    def _close_position(self, position, close_price):
        if position in self.open_positions:
            pip_value = 100000
            if "JPY" in position['symbol']:
                pip_value = 1000
                
            if position['type'] == 0: pnl_final = (close_price - position['entry_price']) * (position['lot'] * pip_value)
            else: pnl_final = (position['entry_price'] - close_price) * (position['lot'] * pip_value)
            position['exit_price'] = close_price; position['exit_time'] = self.get_current_bar_data().name; position['pnl'] = pnl_final
            self.balance += pnl_final
            self.trade_history.append(position)
            self.open_positions.remove(position)

    def generate_report(self, report_filename="backtest_report.txt"):
        # Logica de generare a raportului rămâne neschimbată
        if not self.trade_history:
            report_lines = ["\n--- RAPORT BACKTEST ---", "Nicio tranzacție nu a fost executată."]
            if "optuna" not in "".join(traceback.format_stack()):
                 for line in report_lines: print(line)
            try:
                with open(report_filename, "a", encoding="utf-8") as f: f.write("\n" + "\n".join(report_lines))
            except Exception: pass
            return {"profit_factor": 0, "net_profit": 0, "total_trades": 0, "win_rate": 0, "max_drawdown": 0}

        total_trades = len(self.trade_history); wins = [t for t in self.trade_history if t['pnl'] > 0]; losses = [t for t in self.trade_history if t['pnl'] <= 0]
        win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
        total_pnl = sum(t['pnl'] for t in self.trade_history); sum_wins = sum(t['pnl'] for t in wins); sum_losses = abs(sum(t['pnl'] for t in losses))
        profit_factor = sum_wins / sum_losses if sum_losses > 0 else float('inf')

        equity_series = pd.Series(self.equity_history); peak_series = equity_series.cummax(); drawdown_series = (equity_series - peak_series) / peak_series
        max_drawdown_pct = abs(drawdown_series.min()) * 100 if not drawdown_series.empty else 0
        
        report_lines = []; separator = "="*50
        report_lines.append("\n" + separator); report_lines.append("--- RAPORT FINAL BACKTEST ---"); report_lines.append(separator)
        if hasattr(self, 'processed_data') and not self.processed_data.empty: report_lines.append(f"Perioada testată: {self.processed_data.index[0]} -> {self.processed_data.index[-1]}")
        report_lines.append(f"Capital inițial: ${self.initial_equity:.2f}"); report_lines.append(f"Capital final: ${self.balance:.2f}"); report_lines.append(f"Profit/Pierdere Net: ${total_pnl:.2f}")
        report_lines.append("-"*20)
        report_lines.append(f"Total tranzacții: {total_trades}"); report_lines.append(f"Tranzacții câștigătoare: {len(wins)}"); report_lines.append(f"Tranzacții pierzătoare: {len(losses)}")
        report_lines.append(f"Rata de succes (Win Rate): {win_rate:.2f}%"); report_lines.append(f"Profit Factor: {profit_factor:.2f}"); report_lines.append(f"Drawdown Maxim: {max_drawdown_pct:.2f}%")
        report_lines.append(separator)

        is_optimizing = "optuna" in "".join(traceback.format_stack())
        if not is_optimizing:
            for line in report_lines: print(line)
        
        try:
            mode = "a" if is_optimizing else "w"
            with open(report_filename, mode, encoding="utf-8") as f:
                f.write(f"\nRaport generat la: {datetime.now()}\n"); f.write("\n".join(report_lines))
            if not is_optimizing:
                self.logger.log(f"ℹ️ Raportul a fost salvat în fișierul: {report_filename}")
        except Exception as e: self.logger.log(f"❌ Eroare la salvarea raportului în fișier: {e}", "error")

        return {"profit_factor": profit_factor, "net_profit": total_pnl, "total_trades": total_trades, "win_rate": win_rate, "max_drawdown": max_drawdown_pct}