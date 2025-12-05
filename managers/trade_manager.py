# managers/trade_manager.py - IMPLEMENTARE CU SYNC CORECT ȘI HARD SL UPDATE

import traceback
import numpy as np
# Nu mai importam MT5 direct aici, folosim connector-ul transmis

class TradeManager:
    def __init__(self, logger, trade_deviation, mt5, risk_manager=None):
        self.logger = logger
        self.trade_deviation = trade_deviation
        self.mt5 = mt5 # Acesta este wrapper-ul thread-safe
        self.risk_manager = risk_manager
        
        self.profit_lock_hwm = {} 
        self.internal_active_symbols = set()

    def _ensure_symbol(self, symbol: str) -> bool:
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            self.logger.log(f"❌ Simbolul {symbol} nu a fost găsit.")
            return False
        if not info.visible:
            if not self.mt5.symbol_select(symbol, True):
                self.logger.log(f"❌ Nu am putut selecta simbolul {symbol}.")
                return False
        return True

    def sync_internal_tracker(self):
        """
        Sincronizează trackerul intern cu realitatea din MT5.
        Elimină simbolurile care nu mai au poziții active.
        """
        try:
            positions = self.mt5.positions_get()
            real_symbols = set()
            if positions:
                for pos in positions:
                    real_symbols.add(pos.symbol)
            
            # Resetăm complet setul intern pe baza realității
            self.internal_active_symbols = real_symbols
            
        except Exception as e:
            self.logger.log(f"Eroare la sync_internal_tracker: {e}", "error")

    def _update_sl(self, symbol, ticket, new_sl, magic_number):
        """Trimite o cerere MT5 pentru a modifica doar SL-ul."""
        if not self._ensure_symbol(symbol): return False
        
        positions = self.mt5.positions_get(ticket=ticket)
        if not positions: return False
        position = positions[0]
        
        # Anti-Spam: Dacă SL e deja aproape identic, nu facem request
        if abs(position.sl - new_sl) < 1e-5:
            return True 

        symbol_info = self.mt5.get_symbol_info(symbol)
        if not symbol_info: return False
        
        # Verificare StopLevel (distanța minimă față de preț)
        stop_level = symbol_info.trade_stops_level * symbol_info.point
        tick = self.mt5.get_symbol_info_tick(symbol)
        if not tick: return False
        
        current_price = tick.bid if position.type == self.mt5.ORDER_TYPE_BUY else tick.ask
        
        # Ajustare dacă noul SL e prea aproape de preț
        if abs(current_price - new_sl) < stop_level:
            if position.type == self.mt5.ORDER_TYPE_BUY:
                new_sl = current_price - stop_level - symbol_info.point
            else:
                new_sl = current_price + stop_level + symbol_info.point

        request = {
            "action": self.mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": float(np.round(new_sl, self.mt5.get_digits(symbol))),
            "tp": float(position.tp), 
            "deviation": self.trade_deviation,
            "magic": magic_number,
        }
        
        result = self.mt5.order_send(request)
        if result and (result.retcode == self.mt5.TRADE_RETCODE_DONE or result.retcode == self.mt5.TRADE_RETCODE_NO_CHANGES):
            return True
        return False

    def open_trade(self, symbol, order_type, lot, sl, tp, deviation_points, magic_number, comment, ml_features=None):
        # 🟢 SAFETY: Verificăm tracker-ul intern
        if symbol in self.internal_active_symbols:
            # Opțional: Poți comenta log-ul de mai jos dacă e prea zgomotos
            # self.logger.log(f"Skip {symbol}: Poziție deja activă intern.") 
            return None

        if not self._ensure_symbol(symbol): return None
        tick = self.mt5.get_symbol_info_tick(symbol)
        if tick is None: return None

        price = tick.ask if order_type == 0 else tick.bid
        digits = self.mt5.get_digits(symbol)
        
        trade_type = self.mt5.ORDER_TYPE_BUY if order_type == 0 else self.mt5.ORDER_TYPE_SELL

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": trade_type,
            "price": float(price),
            "deviation": deviation_points,
            "sl": float(np.round(sl, digits)),
            "tp": float(np.round(tp, digits)),
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_FOK,
            "magic": magic_number,
            "comment": comment,
        }

        result = self.mt5.order_send(request)
        
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            ticket = result.order
            self.internal_active_symbols.add(symbol) # 🔒 Blocăm simbolul imediat
            
            self.logger.log_position(
                ticket=ticket, symbol=symbol, order_type="BUY" if order_type == 0 else "SELL",
                lot_size=lot, entry_price=price, sl=sl, tp=tp, 
                comment=comment, closed=False, ml_features=ml_features 
            )
            return ticket
        return None
        
    def close_trade(self, symbol: str, ticket: int, magic_number: int):
        positions = self.mt5.positions_get(ticket=ticket)
        if not positions:
            # Dacă nu o găsim, forțăm curățarea trackerului
            if symbol in self.internal_active_symbols:
                self.internal_active_symbols.remove(symbol)
            return False
            
        position = positions[0]
        tick = self.mt5.get_symbol_info_tick(symbol)
        close_price = tick.bid if position.type == self.mt5.ORDER_TYPE_BUY else tick.ask
        close_type = self.mt5.ORDER_TYPE_SELL if position.type == self.mt5.ORDER_TYPE_BUY else self.mt5.ORDER_TYPE_BUY 
        
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": position.volume,
            "type": close_type,
            "position": ticket,
            "price": close_price,
            "deviation": self.trade_deviation,
            "magic": magic_number,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_FOK,
        }

        result = self.mt5.order_send(request)
        
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            self.logger.log_position(
                ticket=ticket, symbol=symbol, order_type="CLOSE", lot_size=position.volume,
                exit_price=close_price, closed=True
            )
            # Sincronizarea se va face automat și în monitor, dar ajută să o facem și aici
            if symbol in self.internal_active_symbols:
                self.internal_active_symbols.remove(symbol)
            return True
        return False

    def close_all_trades(self):
        positions = self.mt5.positions_get()
        if not positions:
             self.internal_active_symbols.clear()
             return
        
        for pos in positions:
            self.close_trade(pos.symbol, pos.ticket, pos.magic)
        self.internal_active_symbols.clear()

    def apply_trailing(self, position):
        """
        Aplică logica de Break-Even și Profit Lock.
        FIX: Anti-spam logging și rotunjire corectă.
        """
        try:
            params = self.risk_manager.get_trailing_params()
            if not params.get("enabled", False): return

            symbol = position.symbol
            ticket = position.ticket
            entry = position.price_open
            sl = position.sl
            tp = position.tp
            type_op = position.type
            
            # Info simbol
            tick = self.mt5.get_symbol_info_tick(symbol)
            if not tick: return
            
            curr_price = tick.bid if type_op == self.mt5.ORDER_TYPE_BUY else tick.ask
            
            sym_info = self.mt5.get_symbol_info(symbol)
            if not sym_info: return
            
            point = sym_info.point
            digits = sym_info.digits
            
            # --- 1. Break Even Simplu (bazat pe puncte) ---
            be_points = float(params.get("be_min_profit_points", 0))
            be_secure = float(params.get("be_secured_points", 0))
            
            if be_points > 0:
                profit_pts = (curr_price - entry) / point if type_op == 0 else (entry - curr_price) / point
                
                if profit_pts >= be_points:
                    new_sl_be = entry + (be_secure * point) if type_op == 0 else entry - (be_secure * point)
                    new_sl_be = round(new_sl_be, digits) # Rotunjire
                    
                    # Verificăm diferența semnificativă (> 0.5 puncte) pentru a evita spam-ul
                    if (type_op == 0 and new_sl_be > (sl + point * 0.5)) or \
                       (type_op == 1 and (sl == 0 or new_sl_be < (sl - point * 0.5))):
                        
                        self.logger.log(f"➡️ [{symbol}] BE Trigger ({profit_pts:.0f} pts). Mutare SL la {new_sl_be}")
                        if self._update_sl(symbol, ticket, new_sl_be, position.magic):
                            sl = new_sl_be # Actualizăm local pentru pasul următor

            # --- 2. Profit Lock (Hard SL) ---
            profit_lock_pct = float(params.get("profit_lock_percent", 0.0))
            
            if profit_lock_pct > 0.0 and tp > 0:
                total_dist = abs(tp - entry)
                curr_dist = abs(curr_price - entry)
                
                if total_dist == 0: return

                pct_reached = curr_dist / total_dist
                
                if pct_reached >= profit_lock_pct:
                    # Calcul țintă
                    lock_dist = total_dist * (profit_lock_pct - 0.02) # Mic buffer
                    
                    if type_op == 0: # BUY
                        target_sl = entry + lock_dist
                    else: # SELL
                        target_sl = entry - lock_dist
                    
                    # Rotunjire obligatorie
                    target_sl = round(target_sl, digits)
                    
                    # 🛑 FIX SPAM: Logăm și executăm DOAR dacă diferența e relevantă
                    # Verificăm dacă noul SL este mai bun decât cel curent cu cel puțin 1 'point'
                    
                    should_update = False
                    if type_op == 0: # Buy - SL trebuie să urce
                        if target_sl > (sl + point * 0.5):
                            should_update = True
                    else: # Sell - SL trebuie să scadă
                        if sl == 0.0 or target_sl < (sl - point * 0.5):
                            should_update = True
                            
                    if should_update:
                        self.logger.log(f"🔒 Profit Lock {symbol}: Securing at {target_sl}")
                        self._update_sl(symbol, ticket, target_sl, position.magic)

        except Exception as e:
            self.logger.log(f"Err trailing {ticket}: {e}", "error")