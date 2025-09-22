import pandas as pd

class TradeManager:
    """
    Manager pentru operațiuni de tranzacționare: open/close și trailing stop.
    Folosește unitatea 'pips' pentru orice distanță, convertită corect în preț.
    """   
    def __init__(self, config, logger, trade_deviation, mt5, risk_manager=None):
        self.config = config
        self.logger = logger
        self.trade_deviation = trade_deviation
        self.mt5 = mt5
        self.risk_manager = risk_manager
        self.magic_number = 1393193

    def _ensure_symbol(self, symbol: str) -> bool:
        """Asigură că simbolul există și e vizibil în Market Watch."""
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            self.logger.log(f"❌ Symbol {symbol} not found in MT5")
            return False
        if not info.visible:
            self.mt5.symbol_select(symbol, True)
            self.logger.log(f"ℹ️ Symbol {symbol} was hidden, selected now in Market Watch")
        return True

    def _clamp_and_round_volume(self, symbol, volume):
        info = self.mt5.get_symbol_info(symbol)
        if not info:
            return volume
        vol_min = float(getattr(info, "volume_min", 0.01) or 0.01)
        vol_max = float(getattr(info, "volume_max", 100.0) or 100.0)
        step   = float(getattr(info, "volume_step", 0.01) or 0.01)
        cfg_max = float(self.risk_manager.config.get("max_position_lot", vol_max)) if self.risk_manager else vol_max
        # clamp
        volume = max(min(volume, vol_max, cfg_max), vol_min)
        # round to step
        volume = round(round(volume / step) * step, 8)
        return volume
        
    def _get_deviation_points(self, symbol: str) -> int:
        """Convertește deviation din pips în points pentru simbolul dat."""
        symbol_info = self.mt5.get_symbol_info(symbol)
        if not symbol_info:
            return 0
        pip_size = self.mt5.get_pip_size(symbol)
        point = symbol_info.point
        return int(self.trade_deviation * (pip_size / point))
        
    def safe_order_send(self, request, action_desc=""):
        info = self.mt5.get_symbol_info(request["symbol"])
        if info is None:
            self.logger.log(f"❌ Symbol {request['symbol']} not found in MT5")
            return None
            
        if not info.visible:
            self.mt5.symbol_select(request["symbol"], True)
            self.logger.log(f"ℹ️ Symbol {request['symbol']} selected in Market Watch")

        result = self.mt5.order_send(request)
        if result is None:
            err = getattr(self.mt5, "last_error", lambda: ("?", "?"))()
            self.logger.log(f"❌ order_send returned None {action_desc} | last_error={err} request={request}")
            
        return result

    def open_trade(self, symbol, order_type, lot, entry_price, sl, tp):
        if not self._ensure_symbol(symbol):
            return False

        # asigurăm că lotul e valid pentru simbol + config
        lot = self._clamp_and_round_volume(symbol, float(lot))

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            self.logger.log(f"❌ Nu am tick pentru {symbol}")
            return False

        if order_type == "BUY":
            trade_type = self.mt5.mt5.ORDER_TYPE_BUY
            price = tick.ask
        elif order_type == "SELL":
            trade_type = self.mt5.mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            self.logger.log(f"❌ Tip de ordin invalid: {order_type}")
            return False

        request = {
            "action": self.mt5.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": trade_type,
            "price": float(price),
            "sl": float(sl),
            "tp": float(tp),
            "deviation": self._get_deviation_points(symbol),
            "magic": int(self.magic_number),
            "comment": f"{order_type} by bot",
            "type_time": self.mt5.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.mt5.ORDER_FILLING_FOK,
        }

        self.logger.log(f"📤 Sending order: {request}")
        result = self.mt5.order_send(request)

        if result is None:
            err = getattr(self.mt5, "last_error", lambda: ("?", "?"))()
            self.logger.log(f"❌ order_send returned None on open {symbol} {order_type} | last_error={err}")
            self.logger.log(f"Request: {request}")
            return False

        if getattr(result, "retcode", None) != self.mt5.mt5.TRADE_RETCODE_DONE:
            self.logger.log(
                f"❌ Eșec open_trade {order_type} {symbol} lot={lot:.2f} | "
                f"retcode={result.retcode} comment={getattr(result, 'comment', '')} "
                f"request={request}"
            )
            return False

        self.logger.log(
            f"✅ Open {order_type} {symbol} lot={lot:.2f} | SL={sl:.5f} TP={tp:.5f} @ {price:.5f} "
            f"(order={result.order}, deal={result.deal})"
        )
        return True

    def close_trade(self, position):
        symbol = position.symbol
        if not self._ensure_symbol(symbol):
            return False

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            self.logger.log(f"❌ Nu am tick pentru {symbol}")
            return False

        if position.type == self.mt5.mt5.ORDER_TYPE_BUY:
            trade_type = self.mt5.mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            trade_type = self.mt5.mt5.ORDER_TYPE_BUY
            price = tick.ask

        request = {
            "action": self.mt5.mt5.TRADE_ACTION_DEAL,
            "position": position.ticket,
            "symbol": symbol,
            "volume": position.volume,
            "type": trade_type,
            "price": float(price),
            "deviation": self._get_deviation_points(symbol),
            "magic": int(self.magic_number),
            "comment": f"Close position {position.ticket}"
        }

        result = self.mt5.order_send(request)
        if result is None:
            err = getattr(self.mt5, "last_error", lambda: ("?", "?"))()
            self.logger.log(f"❌ order_send returned None on close {symbol} pos#{position.ticket} | last_error={err}")
            self.logger.log(f"Request: {request}")
            return False

        if getattr(result, "retcode", None) != self.mt5.mt5.TRADE_RETCODE_DONE:
            self.logger.log(
                f"❌ Eșec close_trade {symbol} pos#{position.ticket} "
                f"retcode={result.retcode} comment={getattr(result, 'comment', '')} "
                f"request={request}"
            )
            return False

        self.logger.log(
            f"✅ Close {symbol} pos#{position.ticket} @ {price:.5f} "
            f"(order={result.order}, deal={result.deal})"
        )
        return True

    def manage_trailing_stop(self, symbol):
        """
        Trailing stop în PIPS:
          - Mută SL la break-even după ce profitul depășește be_min_profit_pips
          - După break-even, SL urmărește prețul cu atr_multiplier * ATR(M5)
          - Update doar dacă mutarea depășește step_pips
        """
        if not self._ensure_symbol(symbol):
            return

        positions = self.mt5.positions_get(symbol=symbol)
        if not positions:
            return

        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            return

        pip = self.mt5.get_pip_size(symbol)
        trailing = self.risk_manager.get_trailing_params() if self.risk_manager else {
            "be_min_profit_pips": 10, "step_pips": 5, "atr_multiplier": 1.5
        }

        tf_m5 = self.mt5.get_timeframe("M5")
        rates = self.mt5.get_rates(symbol, tf_m5, 100)
        if rates is None or not hasattr(rates, "__len__") or len(rates) < 20:
            return

        df = pd.DataFrame(rates)
        if df.empty:
            return

        atr_price = self._calculate_atr(df, 14)  # în preț
        if atr_price is None or atr_price <= 0:
            return

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            return

        for pos in positions:
            current = float(tick.bid) if pos.type == self.mt5.mt5.ORDER_TYPE_SELL else float(tick.ask)
            entry = float(pos.price_open)
            current_sl = float(pos.sl) if getattr(pos, "sl", 0.0) else 0.0

            # calculează profitul curent în pips
            if pos.type == self.mt5.mt5.ORDER_TYPE_BUY:
                profit_pips = (current - entry) / pip
            else:
                profit_pips = (entry - current) / pip

            # dacă profitul nu e suficient, nu facem trailing
            if profit_pips < float(trailing["be_min_profit_pips"]):
                continue

            # === 1. Mutare la break-even dacă SL e sub entry ===
            if (pos.type == self.mt5.mt5.ORDER_TYPE_BUY and (current_sl == 0.0 or current_sl < entry)) \
               or (pos.type == self.mt5.mt5.ORDER_TYPE_SELL and (current_sl == 0.0 or current_sl > entry)):
                self._update_sl(pos, symbol, entry)
                continue  # după mutarea la BE, trailing-ul va intra pe următoarea iterație

            # === 2. Trailing dinamic după break-even ===
            distance_price = float(trailing["atr_multiplier"]) * float(atr_price)

            if pos.type == self.mt5.mt5.ORDER_TYPE_BUY:
                candidate_sl = current - distance_price
                if (candidate_sl - current_sl) >= float(trailing["step_pips"]) * pip:
                    new_sl = max(entry, candidate_sl)
                    self._update_sl(pos, symbol, new_sl)

            else:  # SELL
                candidate_sl = current + distance_price
                if (current_sl - candidate_sl) >= float(trailing["step_pips"]) * pip:
                    new_sl = min(entry, candidate_sl)
                    self._update_sl(pos, symbol, new_sl)

    def _calculate_atr(self, df, period=14):
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        try:
            return float(atr)
        except Exception:
            return None

    def _update_sl(self, position, symbol, new_sl):
        if not self._ensure_symbol(symbol):
            return

        # Determinăm entry ca să știm dacă mutăm la BE
        entry = float(position.price_open)
        is_be = abs(new_sl - entry) < (self.mt5.get_symbol_info(symbol).point * 2)  # toleranță 2 puncte

        request = {
            "action": self.mt5.mt5.TRADE_ACTION_SLTP,
            "position": position.ticket,
            "symbol": symbol,
            "sl": float(new_sl),
            "tp": float(position.tp),
            "deviation": self._get_deviation_points(symbol),
            "magic": int(self.magic_number),
        }

        result = self.mt5.order_send(request)
        if result is None:
            err = getattr(self.mt5, "last_error", lambda: ("?", "?"))()
            self.logger.log(f"❌ order_send returned None on TS update {symbol} pos#{position.ticket} | last_error={err}")
            self.logger.log(f"Request: {request}")
            return

        if getattr(result, "retcode", None) == self.mt5.mt5.TRADE_RETCODE_DONE:
            if is_be:
                self.logger.log(f"✅ SL moved to BREAK-EVEN {symbol} pos#{position.ticket} SL={new_sl:.5f}")
            else:
                self.logger.log(f"✅ Trailing SL updated {symbol} pos#{position.ticket} SL={new_sl:.5f}")
        else:
            self.logger.log(
                f"❌ TS update failed {symbol} pos#{position.ticket} "
                f"retcode={result.retcode} comment={getattr(result, 'comment', '')} "
                f"request={request}"
            )