import traceback


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
            self.logger.log(f"❌ SL update failed for {symbol}: order_send returned None, last_error={err}, request={request}")
            return False
        if result.retcode != self.mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"❌ SL update failed for {symbol}: retcode={result.retcode}, comment={getattr(result,'comment','')}, request={request}")
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
                self.logger.log(f"❌ order_send returned None ({context}) for {symbol}, last_error={err}, request={request}")
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
        Deschide o poziție market (BUY/SELL).
        """
        if not self._ensure_symbol(symbol):
            return None

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            self.logger.log(f"❌ No tick data for {symbol}")
            return None

        price = tick.ask if order_type == self.mt5.ORDER_TYPE_BUY else tick.bid

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
            "type_filling": self.mt5.ORDER_FILLING_RETURN,
        }

        return self.safe_order_send(request, f"open {symbol}")

    def close_trade(self, symbol, ticket):
        """
        Închide o poziție existentă.
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
        order_type = self.mt5.ORDER_TYPE_SELL if pos.type == self.mt5.ORDER_TYPE_BUY else self.mt5.ORDER_TYPE_BUY

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
            "type_filling": self.mt5.ORDER_FILLING_RETURN,
        }

        return self.safe_order_send(request, f"close {symbol}")
