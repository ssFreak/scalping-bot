class TradeManager:
    """
    Manager pentru tranzacții – deschidere, închidere, trailing stop.
    IMPORTANT:
      - re-verifică riscul chiar înainte de order_send()
      - respectă one_position_per_symbol și cooldown via RiskManager.can_open_symbol()
    """

    def __init__(self, logger, mt5_connector, magic_number=13930, trade_deviation=10, risk_manager=None):
        self.logger = logger
        self.mt5 = mt5_connector
        self.magic_number = magic_number
        self.trade_deviation = trade_deviation
        self.risk_manager = risk_manager  # injectat din BotManager

    def open_trade(self, symbol, order_type, lot, entry_price, sl, tp):
        # ultima poartă de siguranță
        if self.risk_manager:
            if not self.risk_manager.can_trade(verbose=False):
                self.logger.log("⛔ Blocked by can_trade() at send time.")
                return False
            if not self.risk_manager.can_open_symbol(symbol):
                return False

        # pregătire simbol
        self.mt5.symbol_select(symbol, True)
        tick = self.mt5.get_tick(symbol)
        if not tick:
            self.logger.log(f"⚠️ No tick for {symbol}")
            return False

        if order_type == "BUY":
            trade_type = self.mt5.ORDER_TYPE_BUY
            price = tick.ask
        elif order_type == "SELL":
            trade_type = self.mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            self.logger.log(f"❌ Invalid order type: {order_type}")
            return False

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": trade_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.trade_deviation,
            "magic": self.magic_number,
            "comment": f"{order_type} by bot",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_FOK,
        }

        self.logger.log(f"🔄 Sending {order_type} {symbol} lot={lot} SL={sl} TP={tp}")
        result = self.mt5.order_send(request)

        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"✅ Order OK {order_type} {symbol} ticket={getattr(result, 'order', 'n/a')}")
            if self.risk_manager:
                self.risk_manager.register_trade(symbol)
            return True

        self.logger.log(f"❌ Order failed {order_type} {symbol}. Result={result}")
        return False

    def close_trade(self, position):
        tick = self.mt5.get_tick(position.symbol)
        if not tick:
            self.logger.log(f"⚠️ No tick for {position.symbol}")
            return False

        if position.type == self.mt5.ORDER_TYPE_BUY:
            trade_type = self.mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            trade_type = self.mt5.ORDER_TYPE_BUY
            price = tick.ask

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "position": position.ticket,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": trade_type,
            "price": price,
            "deviation": self.trade_deviation,
            "magic": self.magic_number,
            "comment": f"Close {position.ticket}",
        }

        self.logger.log(f"🔄 Closing position {position.ticket} ({position.symbol})")
        result = self.mt5.order_send(request)
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"✅ Closed {position.ticket}")
            return True
        self.logger.log(f"❌ Close failed {position.ticket}, result={result}")
        return False

    def manage_trailing_stop(self, symbol, ts_distance_price=None, ts_atr=None):
        """
        Trailing stop:
          - dacă se primește ts_atr: distanța = ts_atr * multiplier
          - altfel, dacă se primește ts_distance_price: distanță fixă în unități de preț
          - altfel nu face nimic
        """
        positions = self.mt5.get_positions(symbol=symbol)
        if not positions:
            return

        symbol_info = self.mt5.get_symbol_info(symbol)
        if not symbol_info:
            return

        for p in positions:
            tick = self.mt5.get_tick(symbol)
            if not tick:
                continue

            if ts_atr is not None:
                distance = ts_atr  # deja în preț (atr * mult)
            elif ts_distance_price is not None:
                distance = ts_distance_price
            else:
                return

            if p.type == self.mt5.ORDER_TYPE_BUY:
                current_price = tick.bid
                new_sl = current_price - distance
                if not p.sl or new_sl > p.sl:
                    self._update_sl(p, new_sl)
            else:
                current_price = tick.ask
                new_sl = current_price + distance
                if not p.sl or new_sl < p.sl:
                    self._update_sl(p, new_sl)

    def _update_sl(self, position, new_sl):
        request = {
            "action": self.mt5.TRADE_ACTION_SLTP,
            "position": position.ticket,
            "symbol": position.symbol,
            "sl": new_sl,
            "tp": position.tp,
            "deviation": self.trade_deviation,
            "magic": self.magic_number,
        }
        result = self.mt5.order_send(request)
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"✅ TS updated {position.symbol} ticket={position.ticket} SL={new_sl:.5f}")
        else:
            self.logger.log(f"❌ TS update failed {position.symbol} ticket={position.ticket}")
