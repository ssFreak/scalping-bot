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

    def _choose_filling_mode(self, symbol):
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            self.logger.log(f"❌ No symbol info for {symbol}")
            return None

        fm = getattr(info, "filling_mode", None)
        self.logger.log(f"ℹ️ {symbol} filling_mode reported by broker = {fm}")

        valid = {
            self.mt5.ORDER_FILLING_FOK,
            self.mt5.ORDER_FILLING_IOC,
            self.mt5.ORDER_FILLING_RETURN,
            }
        # adăugăm și BOC dacă există în librărie, altfel fallback la int=3
        if hasattr(self.mt5, "ORDER_FILLING_BOC"):
            valid.add(self.mt5.ORDER_FILLING_BOC)
        else:
            valid.add(3)

        if fm in valid:
            return fm

        self.logger.log(f"❌ Unsupported filling mode {fm} for {symbol}")
        return None

    def _update_sl(self, symbol, ticket, new_sl):
        """Actualizează Stop Loss pentru un ticket existent."""
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
        if result is None:
            err = self.mt5.last_error()
            self.logger.log(
                f"❌ SL update failed for {symbol}: order_send returned None, last_error={err}, request={request}"
            )
            return False
        if result.retcode != self.mt5.TRADE_RETCODE_DONE:
            self.logger.log(
                f"❌ SL update failed for {symbol}: retcode={result.retcode}, "
                f"comment={getattr(result,'comment','')}, request={request}"
            )
            return False

        self.logger.log(f"✅ SL updated for {symbol}, ticket={ticket}, new SL={new_sl}")
        return True

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
        """
        if not self._ensure_symbol(symbol):
            return None

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            self.logger.log(f"❌ No tick data for {symbol}")
            return None

        price = tick.ask if order_type == self.mt5.ORDER_TYPE_BUY else tick.bid

        filling_type = self._choose_filling_mode(symbol)
        if filling_type is None:
            return None

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": deviation_points,
            "magic": self.magic_number,
            "comment": comment,
            "type_filling": filling_type,
        }

        result = self.safe_order_send(request, f"open {symbol}")

        # dacă s-a executat cu succes -> logăm și în positions.xlsx
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            order_type_str = "BUY" if order_type == self.mt5.ORDER_TYPE_BUY else "SELL"
            self.logger.log_position(
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

        filling_type = self._choose_filling_mode(symbol)
        if filling_type is None:
            return None

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

    def apply_trailing(self, symbol, pos, atr_price, pip, params):
        """
        Aplică trailing stop pe o poziție existentă.
        params: dict cu cheile:
            - be_min_profit_pips
            - step_pips
            - atr_multiplier
        """
        be_pips = float(params.get("be_min_profit_pips", 10))
        step_pips = float(params.get("step_pips", 5))
        atr_mult = float(params.get("atr_multiplier", 1.5))

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            return

        bid = float(getattr(tick, "bid", 0.0))
        ask = float(getattr(tick, "ask", 0.0))
        if bid <= 0.0 and ask <= 0.0:
            return

        current = ask if pos.type == self.mt5.ORDER_TYPE_BUY else bid
        entry = float(pos.price_open)
        current_sl = float(pos.sl) if float(getattr(pos, "sl", 0.0)) else 0.0

        # Profit în pips
        profit_pips = (current - entry) / pip if pos.type == self.mt5.ORDER_TYPE_BUY else (entry - current) / pip
        if profit_pips < be_pips:
            return

        # Mutare la break-even
        needs_be = (
            (pos.type == self.mt5.ORDER_TYPE_BUY and (current_sl == 0.0 or current_sl < entry)) or
            (pos.type == self.mt5.ORDER_TYPE_SELL and (current_sl == 0.0 or current_sl > entry))
        )
        if needs_be:
            self._update_sl(symbol, pos.ticket, entry)
            return

        # Trailing dinamic pe ATR
        distance_price = atr_mult * float(atr_price)

        if pos.type == self.mt5.ORDER_TYPE_BUY:
            candidate_sl = current - distance_price
            if candidate_sl > current_sl + step_pips * pip:
                self._update_sl(symbol, pos.ticket, max(entry, candidate_sl))
        else:
            candidate_sl = current + distance_price
            if candidate_sl < current_sl - step_pips * pip:
                self._update_sl(symbol, pos.ticket, min(entry, candidate_sl))

    def close_all_trades(self):
        """Închide toate pozițiile deschise pentru toate simbolurile."""
        positions = self.mt5.positions_get()
        if not positions:
            return

        for pos in positions:
            try:
                self.close_trade(pos.symbol, pos.ticket)
            except Exception as e:
                self.logger.log(f"❌ Eroare la închiderea forțată a {pos.symbol} ticket={pos.ticket}: {e}")
