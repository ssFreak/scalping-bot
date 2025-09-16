# managers/trade_manager.py
import pandas as pd

class TradeManager:
    """
    Manager pentru tranzacții:
      - trimite ordine market,
      - închide poziții,
      - trailing stop bazat pe ATR pe timeframe-ul strategiei (setat per simbol).
    NOTĂ: nu schimbăm semnături existente; adăugăm doar logică + o metodă nouă de setare timeframe.
    """

    def __init__(self, logger, mt5_connector, magic_number=13930, trade_deviation=10, risk_manager=None):
        self.logger = logger
        self.mt5 = mt5_connector
        self.magic_number = magic_number
        self.trade_deviation = trade_deviation
        self.risk_manager = risk_manager

        # registry: simbol -> timeframe (constanta MT5) pentru trailing
        self.trailing_tf_by_symbol = {}
        # fallback: M5 dacă există în connector, altfel None (strategiile ar trebui să seteze explicit)
        self.default_trailing_tf = getattr(self.mt5, "TIMEFRAME_M5", None)

    # ---- nou, NON-BREAKING: setarea timeframe-ului de trailing pentru un simbol
    def set_trailing_timeframe(self, symbol, timeframe):
        """
        Setează timeframe-ul (constanta MT5 sau string 'M1'/'M5'/...) folosit la ATR pentru trailing.
        Nu modifică semnături existente; este o extensie sigură.
        """
        tf_const = None
        if isinstance(timeframe, str):
            # dacă connectorul are helper, îl folosim, altfel încercăm getattr pe mt5.* în interiorul connectorului
            if hasattr(self.mt5, "get_timeframe"):
                tf_const = self.mt5.get_timeframe(timeframe)
            else:
                # best-effort: încercăm să existe atribut TIMEFRAME_*
                tf_const = getattr(self.mt5, f"TIMEFRAME_{timeframe.upper()}", None)
        else:
            tf_const = timeframe

        if tf_const is None:
            self.logger.log(f"⚠️ Timeframe necunoscut pentru trailing ({symbol} -> {timeframe}). Se folosește fallback.")
            return

        self.trailing_tf_by_symbol[symbol] = tf_const

    # =============================
    #  --- ORDERS ---
    # =============================
    def open_trade(self, symbol, order_type, lot, entry_price, sl, tp):
        """
        Deschide o tranzacție de tip BUY/SELL la prețul curent (market order).
        Semnătura rămâne neschimbată.
        """
        # ultimul gardian de risc (nu schimbăm interfețe, doar folosim dacă e disponibil)
        if self.risk_manager:
            if not self.risk_manager.can_open_symbol(symbol):
                return False

        tick = self.mt5.get_symbol_tick(symbol) if hasattr(self.mt5, "get_symbol_tick") else None
        if tick is None:
            # compatibilitate: unele implementări au get_symbol_info_tick
            if hasattr(self.mt5, "get_symbol_info_tick"):
                tick = self.mt5.get_symbol_info_tick(symbol)
        if tick is None:
            self.logger.log(f"❌ Nu am tick pentru {symbol}")
            return False

        if order_type == "BUY":
            trade_type = self.mt5.ORDER_TYPE_BUY
            price = tick.ask
        elif order_type == "SELL":
            trade_type = self.mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            self.logger.log(f"❌ Tip de ordin invalid: {order_type}")
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

        result = self.mt5.order_send(request)
        if not result or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"❌ Eșec open_trade {order_type} {symbol} -> {getattr(result, 'retcode', 'no_retcode')}")
            return False

        if self.risk_manager:
            self.risk_manager.register_trade(symbol)
        self.logger.log(f"✅ Open {order_type} {symbol} {lot} lot | SL={sl:.5f} TP={tp:.5f} @ {price:.5f}")
        return True

    def close_trade(self, position):
        """
        Închide o poziție deschisă (market close).
        """
        tick = self.mt5.get_symbol_tick(position.symbol) if hasattr(self.mt5, "get_symbol_tick") else None
        if tick is None and hasattr(self.mt5, "get_symbol_info_tick"):
            tick = self.mt5.get_symbol_info_tick(position.symbol)
        if tick is None:
            self.logger.log(f"❌ Nu am tick pentru {position.symbol}")
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
            "comment": f"Close {position.ticket}"
        }

        result = self.mt5.order_send(request)
        if not result or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"❌ Eșec close_trade {position.symbol} {position.ticket}")
            return False

        self.logger.log(f"✅ Close {position.symbol} {position.ticket} @ {price:.5f}")
        return True

    # =============================
    #  --- TRAILING STOP ---
    # =============================
    def manage_trailing_stop(self, symbol):
        """
        Trailing stop bazat pe ATR pe timeframe-ul setat pentru simbol (de strategie).
        Dacă nu a fost setat explicit, folosește fallback (M5 dacă este disponibil în connector).
        Semnătura rămâne neschimbată.
        """
        # determină timeframe-ul pentru trailing
        tf = self.trailing_tf_by_symbol.get(symbol) or self.default_trailing_tf
        if tf is None:
            # dacă nu avem, încercăm un fallback best-effort
            tf = getattr(self.mt5, "TIMEFRAME_M5", None)
        if tf is None:
            # fără timeframe nu putem calcula ATR
            return

        rates = self.mt5.get_rates(symbol, tf, 100)
        if rates is None or len(rates) < 20:
            return
        df = pd.DataFrame(rates)

        atr = self._calculate_atr(df, 14)
        if atr <= 0:
            return

        positions = self.mt5.get_positions(symbol=symbol)
        if not positions:
            return

        info = self.mt5.get_symbol_info(symbol)
        if not info:
            return
        point = float(info.point)

        # parametri trailing (simpli și robuști)
        be_min_profit_pips = 10  # break-even la +10 pips
        step_pips = 5            # mută SL doar dacă schimbarea depășește 5 pips
        atr_mult = 1.5           # distanță trailing = 1.5 * ATR (în unități de preț)

        for pos in positions:
            tick = self.mt5.get_symbol_tick(symbol) if hasattr(self.mt5, "get_symbol_tick") else None
            if tick is None and hasattr(self.mt5, "get_symbol_info_tick"):
                tick = self.mt5.get_symbol_info_tick(symbol)
            if not tick:
                continue

            # preț curent în funcție de direcție
            current_price = tick.bid if pos.type == self.mt5.ORDER_TYPE_SELL else tick.ask
            entry = float(pos.price_open)

            # profit în pips
            profit_pips = ((current_price - entry) / point) if pos.type == self.mt5.ORDER_TYPE_BUY else ((entry - current_price) / point)
            if profit_pips < be_min_profit_pips:
                continue

            # break-even dacă nu are SL
            if (not pos.sl) or pos.sl == 0:
                new_sl = entry
            else:
                # trailing clasic pe ATR
                distance = atr_mult * atr
                new_sl = (current_price - distance) if pos.type == self.mt5.ORDER_TYPE_BUY else (current_price + distance)

                # step logic: nu actualiza pentru mutări sub 5 pips
                diff_pips = abs((new_sl - float(pos.sl)) / point)
                if diff_pips < step_pips:
                    continue

            self._update_sl(pos, new_sl)

    # --- helpers ---
    def _calculate_atr(self, df, period=14):
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if pd.notna(atr) else 0.0

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
            self.logger.log(f"✅ TS updated {position.symbol} pos#{position.ticket} SL={new_sl:.5f}")
        else:
            self.logger.log(f"❌ TS update failed {position.symbol} pos#{position.ticket}")
