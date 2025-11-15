# core/backtest_broker.py
import pandas as pd
from datetime import datetime
import traceback 
import numpy as np

class BacktestBroker:
    """
    MODIFICAT: Acesta este acum un simulator de CONT DE PORTOFOLIU.
    Gestionează un singur capital și o listă de poziții pentru mai multe simboluri.
    Nu mai deține date, ci le primește la cerere.
    """
    def __init__(self, config: dict, initial_equity=1000.0, commission_per_lot=0.07, spread_pips=0.2):
        self.logger = self._setup_logger()
        self.config = config
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.balance = initial_equity
        self.equity_history = [] # Va stoca (timestamp, equity)
        
        self.current_tick_data = {} # Va ține datele barei curente (ex: {'EURUSD': bar, 'USDJPY': bar})
        self.current_timestamp = None
            
        self.open_positions = [] # O singură listă pentru toate pozițiile
        self.pending_orders = {} # Pentru strategii viitoare
        self._next_ticket_id = 1
        
        self.trade_history = []
        self.commission_per_lot = commission_per_lot
        self.spread_pips = spread_pips
        self.logger.log(f"📈 BacktestBroker (Portofoliu) inițializat cu capital de ${initial_equity:.2f}")

    def _setup_logger(self):
        class SimpleLogger:
            def log(self, message, level="info"):
                if "optuna" not in "".join(traceback.format_stack()):
                    print(f"[{level.upper()}] {message}")
        return SimpleLogger()

    def set_current_data(self, timestamp, data_for_all_symbols: dict):
        """Metodă apelată de motorul de backtest (portfolio_validator) la fiecare bară."""
        self.current_timestamp = timestamp
        self.current_tick_data = data_for_all_symbols

    def calculate_lot_size(self, symbol, stop_loss_pips):
        """Calculează lotul pe baza capitalului TOTAL al portofoliului."""
        risk_per_trade = self.config.get('general', {}).get('risk_per_trade', 0.01)
        risk_amount = self.equity * risk_per_trade 
        
        pip_value_per_lot = 10.0
        current_bar_data = self.current_tick_data.get(symbol)
        current_price = current_bar_data.close if current_bar_data is not None and not pd.isna(current_bar_data.close) else 1
        
        if current_price <= 0: current_price = 1
        
        if "JPY" in symbol: 
            pip_value_per_lot = 1000.0 / current_price
        elif "USDCHF" in symbol:
             pip_value_per_lot = 10.0 / current_price
        
        if stop_loss_pips <= 0: return 0.0
        risk_per_lot = stop_loss_pips * pip_value_per_lot
        if risk_per_lot <= 0: return 0.0
        raw_lot = risk_amount / risk_per_lot
        lot = round(raw_lot, 2)
        min_lot = 0.01
        max_lot = self.config.get('general', {}).get('max_position_lot', 1.0)
        return max(min_lot, min(lot, max_lot))

    def has_open_position(self, symbol: str) -> bool:
        """Verifică dacă există o poziție deschisă pentru un simbol specific."""
        for pos in self.open_positions:
            if pos['symbol'] == symbol:
                return True
        return False
        
    def get_open_position_by_symbol(self, symbol: str):
        for pos in self.open_positions:
            if pos['symbol'] == symbol:
                return pos
        return None

    def get_current_bar_data(self, symbol=None):
        """Returnează rândul curent de date pentru un simbol specific."""
        if symbol:
            return self.current_tick_data.get(symbol)
        if self.current_tick_data:
            return list(self.current_tick_data.values())[0]
        return None

    def get_pip_size(self, symbol: str) -> float:
        return 0.01 if "JPY" in symbol else 0.0001
    def get_digits(self, symbol: str) -> int:
        return 3 if "JPY" in symbol else 5
    def get_timeframe(self, tf_str: str): return tf_str

    def place_pending_order(self, symbol, order_type, entry_price, sl, tp, lot, magic, comment, setup_time):
        ticket = self._next_ticket_id
        self._next_ticket_id += 1
        self.pending_orders[ticket] = {
            "ticket": ticket, "symbol": symbol, "type": order_type, "lot": lot,
            "entry_price": entry_price, "sl": sl, "tp": tp, "magic": magic,
            "comment": comment, "setup_time": setup_time
        }
        return ticket
    def get_pending_order(self, ticket):
        return self.pending_orders.get(ticket)
    def cancel_pending_order(self, ticket):
        if ticket in self.pending_orders: del self.pending_orders[ticket]; return True
        return False

    def open_market_order(self, symbol, order_type, lot, sl, tp, magic_number, comment="", **kwargs):
        """Plasează un ordin la piață pe baza datelor curente."""
        tick = self.current_tick_data.get(symbol)
        if tick is None or pd.isna(tick.get('close')):
            return None
            
        pip_size = self.get_pip_size(symbol)
        spread_cost = self.spread_pips * pip_size
        entry_price = round(tick.close + (spread_cost / 2 if order_type == 0 else -spread_cost / 2), self.get_digits(symbol))
        commission = self.commission_per_lot * lot
        self.balance -= commission
        position = {"ticket": self._next_ticket_id, "symbol": symbol, "type": order_type,"lot": lot, "entry_price": entry_price, "entry_time": self.current_timestamp,"sl": sl, "tp": tp, "magic": magic_number, "comment": comment, "pnl": 0.0}
        self.open_positions.append(position)
        self._next_ticket_id += 1
        return position

    def _execute_pending_order(self, order, execution_price):
        commission = self.commission_per_lot * order['lot']
        self.balance -= commission
        position = {
            "ticket": order['ticket'], "symbol": order['symbol'], "type": order['type'],
            "lot": order['lot'], "entry_price": execution_price, "entry_time": self.current_timestamp,
            "sl": order['sl'], "tp": order['tp'], "magic": order['magic'],
            "comment": order['comment'], "pnl": 0.0,
            "be_applied": False
        }
        self.open_positions.append(position)
        del self.pending_orders[order['ticket']] 

    def update_all_positions(self):
        """Iterează prin TOATE pozițiile deschise și le actualizează P/L-ul."""
        if self.current_timestamp is None: return
        
        # Verificăm ordinele pending (pentru strategii viitoare)
        orders_to_execute = []
        for ticket, order in list(self.pending_orders.items()):
            tick = self.current_tick_data.get(order['symbol'])
            if tick is None or pd.isna(tick.get('high')): continue
            
            if order['type'] == 0 and tick.high > order['entry_price']: # BUY STOP
                orders_to_execute.append((order, order['entry_price']))
            elif order['type'] == 1 and tick.low < order['entry_price']: # SELL STOP
                orders_to_execute.append((order, order['entry_price']))
        for order, price in orders_to_execute:
            self._execute_pending_order(order, price)

        # Actualizăm pozițiile deschise
        floating_pnl = 0
        positions_to_close = []
        
        for pos in self.open_positions:
            symbol = pos['symbol']
            tick = self.current_tick_data.get(symbol)
            
            if tick is None or pd.isna(tick.get('close')):
                floating_pnl += pos['pnl']
                continue

            pip_value = 100000.0
            if "JPY" in symbol: pip_value = 1000.0
            
            pnl = 0.0
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
        self.equity_history.append((self.current_timestamp, self.equity))
        
    def _close_position(self, position, close_price):
        if position in self.open_positions:
            pip_value = 100000.0
            if "JPY" in position['symbol']: pip_value = 1000.0
                
            if position['type'] == 0: pnl_final = (close_price - position['entry_price']) * (position['lot'] * pip_value)
            else: pnl_final = (position['entry_price'] - close_price) * (position['lot'] * pip_value)
            position['exit_price'] = close_price; position['exit_time'] = self.current_timestamp; position['pnl'] = pnl_final
            self.balance += pnl_final
            self.trade_history.append(position)
            self.open_positions.remove(position)

    def generate_portfolio_report(self, symbols_tested, report_filename="portfolio_report_FINAL.txt"):
        """Generează raportul final pentru întregul portofoliu."""
        if not self.trade_history:
            report_lines = ["\n--- RAPORT PORTOFOLIU ---", "Nicio tranzacție nu a fost executată."]
            for line in report_lines: print(line)
            return

        total_trades = len(self.trade_history); wins = [t for t in self.trade_history if t['pnl'] > 0]; losses = [t for t in self.trade_history if t['pnl'] <= 0]
        win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
        total_pnl = sum(t['pnl'] for t in self.trade_history); sum_wins = sum(t['pnl'] for t in wins); sum_losses = abs(sum(t['pnl'] for t in losses))
        profit_factor = sum_wins / sum_losses if sum_losses > 0 else float('inf')

        equity_curve = pd.DataFrame(self.equity_history, columns=['datetime', 'equity']).set_index('datetime')['equity']
        peak_series = equity_curve.cummax()
        drawdown_series = (equity_curve - peak_series) / peak_series
        max_drawdown_pct = abs(drawdown_series.min()) * 100 if not drawdown_series.empty else 0
        
        report_lines = []; separator = "="*50
        report_lines.append("\n" + separator); report_lines.append("--- RAPORT FINAL PORTOFOLIU ---"); report_lines.append(separator)
        report_lines.append(f"Simboluri Testate: {', '.join(symbols_tested)}")
        report_lines.append(f"Perioada testată: {equity_curve.index[0]} -> {equity_curve.index[-1]}")
        report_lines.append(f"Capital Inițial Total: ${self.initial_equity:.2f}")
        report_lines.append(f"Capital Final Total: ${self.balance:.2f}")
        report_lines.append(f"Profit/Pierdere Net Total: ${total_pnl:.2f}")
        report_lines.append("-"*20)
        report_lines.append(f"Total tranzacții: {total_trades}"); report_lines.append(f"Tranzacții câștigătoare: {len(wins)}"); report_lines.append(f"Tranzacții pierzătoare: {len(losses)}")
        report_lines.append(f"Rata de succes (Win Rate): {win_rate:.2f}%"); report_lines.append(f"Profit Factor Portofoliu: {profit_factor:.2f}"); report_lines.append(f"Drawdown Maxim Portofoliu: {max_drawdown_pct:.2f}%")
        report_lines.append(separator)

        for line in report_lines: print(line)
        
        try:
            with open(report_filename, "w", encoding="utf-8") as f:
                f.write(f"Raport generat la: {datetime.now()}\n"); f.write("\n".join(report_lines))
            self.logger.log(f"ℹ️ Raportul de portofoliu a fost salvat în fișierul: {report_filename}")
        except Exception as e: self.logger.log(f"❌ Eroare la salvarea raportului de portofoliu: {e}", "error")