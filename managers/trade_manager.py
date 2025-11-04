import traceback
from datetime import datetime
import numpy as np

class TradeManager:
    def __init__(self, logger, trade_deviation, mt5, risk_manager=None):
        self.logger = logger
        self.trade_deviation = trade_deviation
        self.mt5 = mt5
        self.risk_manager = risk_manager

    def _ensure_symbol(self, symbol: str) -> bool:
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            self.logger.log(f"❌ Simbolul {symbol} nu a fost găsit în Market Watch")
            return False
        if not info.visible:
            if not self.mt5.symbol_select(symbol, True):
                self.logger.log(f"❌ Nu am putut selecta simbolul {symbol} în Market Watch")
                return False
        return True
        
    def _update_sl(self, symbol, ticket, new_sl, magic_number):
        if not self._ensure_symbol(symbol): return False
        position = None
        positions = self.mt5.positions_get(symbol=symbol)
        if not positions: return False
        for p in positions:
            if p.ticket == ticket:
                position = p
                break
        if not position: return False

        # Obținem numărul corect de zecimale pentru JPY-safe
        digits = self.mt5.get_digits(symbol)

        request = {
            "action": self.mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": float(np.round(new_sl, digits)), # Folosim digits
            "tp": position.tp,
            "magic": magic_number,
            "comment": "Update SL",
        }
        result = self.mt5.order_send(request)
        if result is None:
            self.logger.log(f"❌ Eșec actualizare SL pentru {symbol}: order_send a returnat None", "error")
            return False
        if result.retcode == self.mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"✅ SL actualizat pentru {symbol}, ticket={ticket}, new SL={new_sl}")
            return True
        else:
            self.logger.log(f"❌ Eșec actualizare SL pentru {symbol}: retcode={result.retcode}, comment={getattr(result,'comment','')}", "error")
            return False

    def safe_order_send(self, request, context=""):
        symbol = request.get("symbol")
        if not self._ensure_symbol(symbol):
            return None
        try:
            result = self.mt5.order_send(request)
            if result is None:
                err = self.mt5.last_error()
                self.logger.log(f"❌ order_send a returnat None ({context}) pentru {symbol}, last_error={err}", "error")
                return None
            if result.retcode != self.mt5.TRADE_RETCODE_DONE:
                self.logger.log(f"❌ order_send a eșuat ({context}) {symbol}: retcode={result.retcode}, comment={getattr(result, 'comment', '')}", "error")
                return result
            self.logger.log(f"✅ OrderSend OK ({context}) {symbol}: order={getattr(result,'order',0)}")
            return result
        except Exception as e:
            self.logger.log(f"❌ Excepție în safe_order_send ({context}) {symbol}: {e}", "error")
            return None

    def close_all_trades(self, magic_number=0):
        all_positions = self.mt5.positions_get() or []
        all_orders = self.mt5.orders_get() or []
        if magic_number > 0:
            positions = [p for p in all_positions if p.magic == magic_number]
            orders = [o for o in all_orders if o.magic == magic_number]
            context_log = f"(magic={magic_number})"
        else:
            positions = all_positions; orders = all_orders; context_log = "(toate strategiile)"
        
        if not positions and not orders:
             self.logger.log(f"ℹ️ Nu există poziții sau ordine de gestionat {context_log}.")
             return True

        self.logger.log(f"🛑 Inițiază închiderea a {len(positions)} poziții și anularea a {len(orders)} ordine pending {context_log}...")
        for pos in positions:
            try:
                self.close_trade(pos.symbol, pos.ticket, pos.magic)
            except Exception as e:
                self.logger.log(f"❌ Eroare la procesarea închiderii poziției {pos.ticket}: {e}", "error")
        for order in orders:
            try:
                if not self._ensure_symbol(order.symbol): continue
                request_remove = {"action": self.mt5.TRADE_ACTION_REMOVE, "order": order.ticket, "symbol": order.symbol, "magic": order.magic}
                self.mt5.order_send(request_remove)
            except Exception as e:
                self.logger.log(f"❌ Eroare la procesarea anulării ordinului {order.ticket}: {e}", "error")
        return True


    def open_trade(self, symbol, order_type, lot, sl, tp, deviation_points, magic_number, comment=""):
        if not self._ensure_symbol(symbol): return None
        tick = self.mt5.get_symbol_info_tick(symbol)
        if tick is None:
            self.logger.log(f"❌ Nu am putut obține tick data pentru {symbol}")
            return None

        price = tick.ask if order_type == self.mt5.ORDER_TYPE_BUY else tick.bid
        
        # --- CORECȚIA CRITICĂ: Folosim IOC în loc de RETURN ---
        filling_type = self.mt5.ORDER_FILLING_IOC 
        
        # Obținem numărul corect de zecimale pentru JPY-safe
        digits = self.mt5.get_digits(symbol) 

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": order_type,
            "price": float(price),
            "sl": float(np.round(sl, digits)), # Rotunjire JPY-safe
            "tp": float(np.round(tp, digits)), # Rotunjire JPY-safe
            "deviation": self.trade_deviation,
            "magic": magic_number,
            "comment": comment,
            "type_filling": filling_type, # Folosim IOC
        }

        result = self.safe_order_send(request, f"open {symbol}")

        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            ticket = getattr(result, "order", 0)
            order_type_str = "BUY" if order_type == self.mt5.ORDER_TYPE_BUY else "SELL"
            self.logger.log_position(ticket=ticket, symbol=symbol, order_type=order_type_str, lot_size=lot, entry_price=price, sl=sl, tp=tp, comment=comment, closed=False)

        return result

    def close_trade(self, symbol, ticket, magic_number):
        if not self._ensure_symbol(symbol): return None
        positions = self.mt5.positions_get(symbol=symbol)
        if not positions: return None
        pos = next((p for p in positions if p.ticket == ticket), None)
        if pos is None: return None
        tick = self.mt5.get_symbol_info_tick(symbol)
        if tick is None: return None

        price = tick.bid if pos.type == self.mt5.ORDER_TYPE_BUY else tick.ask
        order_type = (self.mt5.ORDER_TYPE_SELL if pos.type == self.mt5.ORDER_TYPE_BUY else self.mt5.ORDER_TYPE_BUY)

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(pos.volume),
            "type": order_type,
            "position": ticket,
            "price": float(price),
            "deviation": self.trade_deviation,
            "magic": magic_number,
            "comment": "Close trade",
            "type_filling": self.mt5.ORDER_FILLING_IOC, # Adăugat și aici
        }
        result = self.safe_order_send(request, f"close {symbol}")
        
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            self.logger.log_position(ticket=ticket, symbol=symbol, order_type="CLOSE", lot_size=pos.volume, entry_price=price, sl=pos.sl, tp=pos.tp, comment=f"Closed ticket {ticket}", closed=True, exit_price=price)

        return result

    def apply_trailing(self, symbol, position, atr_price: float, pip: float, params: dict):
        # ... (Logica ta de trailing stop) ...
        # Asigură-te că apelezi _update_sl cu toți parametrii:
        # self._update_sl(symbol, ticket, new_sl, magic_number)
        pass