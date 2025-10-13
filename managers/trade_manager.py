import traceback
from datetime import datetime


class TradeManager:
    """
    Manager pentru operațiuni de tranzacționare: open/close și update SL.
    Folosește unitatea 'pips' pentru distanțe, convertită corect în preț.
    """

    def __init__(self, logger, trade_deviation, mt5, risk_manager=None):
        self.logger = logger
        self.trade_deviation = trade_deviation
        self.mt5 = mt5
        self.risk_manager = risk_manager
        self.magic_number = 1393193

    # =====================
    # Helpers
    # =====================
    def _ensure_symbol(self, symbol: str) -> bool:
        """Asigură că simbolul există și e vizibil în Market Watch."""
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            self.logger.log(f"❌ Symbol {symbol} not found in Market Watch")
            return False
        if not info.visible:
            if not self.mt5.symbol_select(symbol, True):
                self.logger.log(f"❌ Could not select symbol {symbol} in Market Watch")
                return False
        return True
        
    def _update_sl(self, symbol, ticket, new_sl):
        """Actualizează Stop Loss pentru un ticket existent, eliminând Filling Mode din request."""
        if not self._ensure_symbol(symbol):
            return False

        position = None
        positions = self.mt5.positions_get(symbol=symbol)
        if not positions:
            return False
        for p in positions:
            if p.ticket == ticket:
                position = p
                break
        if not position:
            return False

        request = {
            "action": self.mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": position.tp,
            "magic": self.magic_number,
            "comment": "Update SL",
        }

        result = self.mt5.order_send(request) 
        
        # Logica robustă de verificare retcode (Invalid Ticket, No Changes)
        if result is None:
            err = self.mt5.last_error()
            self.logger.log(
                f"❌ SL update failed for {symbol}: order_send returned None, last_error={err}, request={request}"
            )
            return False
            
        retcode = result.retcode
        invalid_ticket_code = getattr(self.mt5, "TRADE_RETCODE_INVALID_TICKET", 10017)
        no_changes_code = getattr(self.mt5, "TRADE_RETCODE_NO_CHANGES", 10016)
        
        if retcode == self.mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"✅ SL updated for {symbol}, ticket={ticket}, new SL={new_sl}")
            return True
        elif retcode == invalid_ticket_code:
            self.logger.log(
                f"⚠️ SL update failed for {symbol}, ticket={ticket}: Poziția nu a fost găsită (probabil închisă de broker). Ignoră."
            )
            return True
        elif retcode == no_changes_code:
             self.logger.log(
                f"ℹ️ SL update for {symbol}, ticket={ticket}: Nu sunt necesare schimbări (SL e deja setat). Ignoră."
            )
             return True
        else:
            self.logger.log(
                f"❌ SL update failed for {symbol}: retcode={retcode}, "
                f"comment={getattr(result,'comment','')}, request={request}"
            )
            return False

    def safe_order_send(self, request, context=""):
        """
        Trimite un ordin la MT5 cu verificări de siguranță și logare extinsă.
        """
        symbol = request.get("symbol")
        if not self._ensure_symbol(symbol):
            return None

        try:
            result = self.mt5.order_send(request)
            if result is None:
                err = self.mt5.last_error()
                self.logger.log(
                    f"❌ order_send returned None ({context}) for {symbol}, last_error={err}, request={request}"
                )
                return None

            if result.retcode != self.mt5.TRADE_RETCODE_DONE:
                self.logger.log(
                    f"❌ order_send failed ({context}) {symbol}: retcode={result.retcode}, "
                    f"comment={getattr(result, 'comment', '')}, request={request}"
                )
                return result

            self.logger.log(
                f"✅ OrderSend OK ({context}) {symbol}: retcode={result.retcode}, "
                f"order={getattr(result,'order',0)}, deal={getattr(result,'deal',0)}"
            )
            return result
        except Exception as e:
            trace = traceback.format_exc()
            self.logger.log(f"❌ Exception in safe_order_send ({context}) {symbol}: {e}")
            self.logger.log(f"🔍 Stack trace: {trace}")
            return None

    # =====================
    # Open / Close trades
    # =====================
    def open_trade(self, symbol, order_type, lot, sl, tp, deviation_points, comment=""):
        """
        Deschide o poziție market (BUY/SELL) și o loghează în positions.xlsx dacă reușește.
        Folosește implicit ORDER_FILLING_RETURN.
        """
        if not self._ensure_symbol(symbol):
            return None

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            self.logger.log(f"❌ No tick data for {symbol}")
            return None

        price = tick.ask if order_type == self.mt5.ORDER_TYPE_BUY else tick.bid
        filling_type = self.mt5.ORDER_FILLING_RETURN

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.trade_deviation,
            "magic": self.magic_number,
            "comment": comment,
            "type_filling": filling_type,
        }

        result = self.safe_order_send(request, f"open {symbol}")

        # dacă s-a executat cu succes -> logăm și în positions.xlsx
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            ticket = getattr(result, "order", 0)
            order_type_str = "BUY" if order_type == self.mt5.ORDER_TYPE_BUY else "SELL"
            self.logger.log_position(
                ticket=ticket,
                symbol=symbol,
                order_type=order_type_str,
                lot_size=lot,
                entry_price=price,
                sl=sl,
                tp=tp,
                comment=comment,
                closed=False,
            )

        return result

    def close_trade(self, symbol, ticket):
        """
        Închide o poziție existentă și marchează în positions.xlsx că a fost închisă.
        """
        if not self._ensure_symbol(symbol):
            return None

        positions = self.mt5.positions_get(symbol=symbol)
        if not positions:
            self.logger.log(f"❌ No open positions to close for {symbol}")
            return None

        pos = next((p for p in positions if p.ticket == ticket), None)
        if pos is None:
            self.logger.log(f"❌ Ticket {ticket} not found for {symbol}")
            return None

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            self.logger.log(f"❌ No tick data for {symbol}")
            return None

        price = tick.bid if pos.type == self.mt5.ORDER_TYPE_BUY else tick.ask
        order_type = (
            self.mt5.ORDER_TYPE_SELL if pos.type == self.mt5.ORDER_TYPE_BUY else self.mt5.ORDER_TYPE_BUY
        )

        filling_type = self.mt5.ORDER_FILLING_RETURN

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": self.trade_deviation,
            "magic": self.magic_number,
            "comment": "Close trade",
            "type_filling": filling_type,
        }

        result = self.safe_order_send(request, f"close {symbol}")

        # dacă s-a executat cu succes -> notăm în positions.xlsx
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            self.logger.log_position(
                symbol=symbol,
                order_type="CLOSE",
                lot_size=pos.volume,
                entry_price=price,
                sl=pos.sl,
                tp=pos.tp,
                comment=f"Closed ticket {ticket}",
                closed=True,
            )

        return result

    def apply_trailing(self, symbol, position, atr_price: float, pip: float, params: dict):
        """
        Aplica trailing stop (break-even + ATR/step trailing) pentru o pozitie existenta.
        Implementeaza blocarea Trailing-ului dinamic sub pragul de BE Securizat.
        """
        try:
            # Preluăm datele din obiectul 'position' primit
            ticket = position.ticket
            entry_price = position.price_open
            sl = position.sl
            order_type = position.type  # 0=buy, 1=sell
    
            # Folosim metodele wrapper corecte
            info = self.mt5.get_symbol_info(symbol) 
            tick = self.mt5.get_symbol_tick(symbol)
            if info is None or tick is None: return
            
            point = info.point
            digits = info.digits
            current_price = tick.bid if order_type == 0 else tick.ask
    
            profit_pips = (current_price - entry_price) / point if order_type == 0 else (entry_price - current_price) / point
    
            self.logger.log(f"🔍 Trailing check {symbol} ticket={ticket} profit={profit_pips:.1f} pips (SL={sl}, TP={position.tp})")
    
            # Parametrii Trailing
            be_min_profit = params.get("be_min_profit_pips", 20)
            be_secured_pips = params.get("be_secured_pips", 5.0) 
            atr_mult = params.get("atr_multiplier", 3.0)
            trailing_step_pips = params.get("step_pips", 5.0)
            
            # Parametrii Profit Lock Absolut
            profit_lock_threshold = params.get("profit_lock_threshold_pips", 50.0)
            profit_lock_guarantee = params.get("profit_lock_guarantee_pips", 15.0)

            new_sl = None
    
            # 🛑 VERIFICARE CRITICĂ: Trailing se aplică doar pe profit.
            if profit_pips > 0:
                
                # --- Determinarea Nivelului de Securizare Fixă ---
                sl_target_pips = None
                
                if profit_pips >= profit_lock_threshold:
                    # 1. 🟢 PROFIT LOCK ABSOLUT (Securizare Câștig Mare: 50 pips)
                    sl_target_pips = profit_lock_guarantee
                elif profit_pips >= be_min_profit:
                    # 2. 🟡 BREAK-EVEN (Profit Securizat Inițial: 20 pips)
                    sl_target_pips = be_secured_pips
                
                
                # --- EXECUTAREA BLOCULUI DE SECURIZARE FIXĂ (Mutare SL la nivelul fix) ---
                
                if sl_target_pips is not None:
                    sl_lock_price = entry_price + sl_target_pips * point if order_type == 0 else entry_price - sl_target_pips * point
                    
                    # Mută SL-ul doar dacă este mai slab decât nivelul țintă
                    if (order_type == 0 and (sl is None or sl < sl_lock_price)) or \
                       (order_type == 1 and (sl is None or sl > sl_lock_price)):
                        
                        new_sl = round(sl_lock_price, digits)
                        self.logger.log(f"➡️ Setting SL to guaranteed profit: {sl_target_pips:.1f} pips.")
                        
                        # Trimitem comanda imediat și ieșim, lăsând logica Trailing dinamic să preia la următoarea rulare.
                        self._update_sl(symbol, ticket, new_sl)
                        return # ⬅️ RETURN: NU FACEM TRAILING DINAMIC ÎN ACEEAȘI RULARE
                
                
                # 🛑 NOU: BLOCAREA TRAILING-ULUI DINAMIC PÂNĂ LA ATINGEREA PRAGULUI FIX 🛑
                
                # Trailing-ul dinamic (ATR/Step) se execută doar dacă SL-ul este deja pe profit, 
                # ceea ce înseamnă că am trecut de faza inițială de securizare.
                is_sl_in_profit = (order_type == 0 and sl > entry_price) or (order_type == 1 and sl < entry_price)

                if is_sl_in_profit:
                    # Logica Step/ATR Trailing se aplică de aici în jos
                    
                    # 3. ⚡ STEP TRAILING (Agresiv, doar peste Profit Lock Threshold)
                    if profit_pips >= profit_lock_threshold and trailing_step_pips > 0:
                        
                        trailing_distance = trailing_step_pips * point
                        sl_new_price = current_price - trailing_distance if order_type == 0 else current_price + trailing_distance
                        
                        # Aplicăm Step Trailing DOAR dacă este mai bun decât SL-ul curent
                        if (order_type == 0 and sl_new_price > sl) or (order_type == 1 and sl_new_price < sl):
                            new_sl = round(sl_new_price, digits)
                            self.logger.log(f"⚡ Step Trailing: SL moved by {trailing_step_pips:.1f} pips step to {new_sl:.{digits}f}")

                    # 4. 🔵 ATR TRAILING (Standard)
                    
                    # Aplicăm ATR DOAR dacă Step Trailing nu a fost activat
                    if new_sl is None:
                        atr_pips = atr_price / pip
                        atr_trail_distance = atr_pips * atr_mult 
                        atr_sl_price = current_price - atr_trail_distance * point if order_type == 0 else current_price + atr_trail_distance * point
                        
                        # Aplicăm ATR dacă este mai bun (mai conservator/mai aproape de profit) decât SL-ul curent
                        if (order_type == 0 and atr_sl_price > sl) or (order_type == 1 and atr_sl_price < sl):
                            new_sl = round(atr_sl_price, digits)
                            self.logger.log(f"➡️ ATR trailing update to {new_sl:.{digits}f}")
                
            # Trimitere Ordin (dacă s-a calculat un nou SL în Blocul 3/4)
            if new_sl and sl != new_sl:
                self._update_sl(symbol, ticket, new_sl)
            else:
                self.logger.log(f"ℹ️ No SL change for {symbol}, ticket={ticket} (conditions not met or SL is the same)")
                
        except Exception as e:
            self.logger.log(f"❌ apply_trailing error for {symbol} ticket={ticket}: {e}")