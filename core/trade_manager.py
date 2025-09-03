# core/trade_manager.py
import MetaTrader5 as mt5
import time
from datetime import datetime
import pytz

class TradeManager:
    """
    Manager pentru tranzacții. Se ocupă de trimiterea ordinelor, închiderea pozițiilor
    și managementul trailing stop-ului.
    """
    def __init__(self, logger, magic_number, trade_deviation, bot_manager=None):
        self.logger = logger
        self.magic_number = magic_number
        self.trade_deviation = trade_deviation
        self.bot_manager = bot_manager

    def open_trade(self, symbol, order_type, lot, entry_price, sl, tp):
        """
        Trimite un ordin de tranzacționare (cumpărare sau vânzare).

        Returnează True dacă ordinul a fost trimis cu succes, False altfel.
        """
        # Verificare globală: nu trimitem ordine dacă bot_manager spune că nu se poate trade-a
        if hasattr(self, 'bot_manager') and self.bot_manager is not None:
            try:
                if not self.bot_manager.can_trade():
                    self.logger.log("⛔ Deschidere ordin blocată: condiții globale nu permit tranzacționarea (max_daily_loss/market).")
                    return False
            except Exception as e:
                # dacă ceva e greșit la can_trade, blocăm pentru siguranță
                self.logger.log(f"❌ Eroare la verificarea can_trade() înainte de open_trade: {e}")
                return False

        # Verifică tipul de ordin
        if order_type == "BUY":
            trade_type = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(symbol).ask
        elif order_type == "SELL":
            trade_type = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(symbol).bid
        else:
            self.logger.log(f"❌ Tip de ordin invalid: {order_type}")
            return False

        # Construiește cererea
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": trade_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.trade_deviation,
            "magic": self.magic_number,
            "comment": f"{order_type} by {self.magic_number}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        self.logger.log(f"🔄 Trimitere ordin de {order_type} pentru {symbol} cu lotajul {lot}...")
        
        # Trimite ordinul și verifică rezultatul
        result = mt5.order_send(request)
        if result is None:
            self.logger.log(f"❌ order_send() a returnat None pentru {symbol}. Request: {request}")
            return False

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"❌ Eșec la trimiterea ordinului: {result.retcode}. Eroare: {mt5.last_error()}")
            # Imprimă request-ul pentru depanare
            self.logger.log(f"Request: {request}")
            return False
        
        self.logger.log(f"✅ Ordin de {order_type} trimis cu succes! Ticket: {getattr(result, 'order', 'n/a')}")
        return True

    def close_trade(self, position):
        """
        Închide o poziție deschisă.
        """
        if position.type == mt5.ORDER_TYPE_BUY:
            trade_type = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(position.symbol).bid
        elif position.type == mt5.ORDER_TYPE_SELL:
            trade_type = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(position.symbol).ask
        else:
            self.logger.log("❌ Tip de poziție necunoscut.")
            return False

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": position.ticket,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": trade_type,
            "price": price,
            "deviation": self.trade_deviation,
            "magic": self.magic_number,
            "comment": f"Close position {position.ticket}"
        }

        self.logger.log(f"🔄 Închidere poziție {position.ticket} pentru {position.symbol}...")
        result = mt5.order_send(request)
        if result is None:
            self.logger.log(f"❌ order_send() a returnat None la închidere poziție {position.ticket}")
            return False

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.log(f"❌ Eșec la închiderea poziției {position.ticket}: {result.retcode}. Eroare: {mt5.last_error()}")
            return False

        self.logger.log(f"✅ Poziția {position.ticket} a fost închisă cu succes.")
        return True

    def manage_trailing_stop(self, symbol):
        """
        Implementează un trailing stop.
        """
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return

        for position in positions:
            current_price = mt5.symbol_info_tick(symbol).bid if position.type == mt5.ORDER_TYPE_SELL else mt5.symbol_info_tick(symbol).ask
            trailing_stop_distance = 250 # Puncte
            
            # Trailing Stop pentru poziții de CUMPĂRARE
            if position.type == mt5.ORDER_TYPE_BUY:
                # Verifică dacă prețul actual a crescut suficient pentru a muta SL
                new_sl = current_price - trailing_stop_distance * mt5.symbol_info(symbol).point
                # Daca position.sl este None sau 0 verificam in consecinta
                current_sl = getattr(position, 'sl', 0.0) or 0.0
                if new_sl > current_sl:
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": position.ticket,
                        "symbol": symbol,
                        "sl": new_sl,
                        "tp": position.tp,
                        "deviation": self.trade_deviation,
                        "magic": self.magic_number,
                    }
                    result = mt5.order_send(request)
                    if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                        self.logger.log(f"✅ TS actualizat pentru poziția BUY {position.ticket} la {new_sl:.5f}")
                    
            # Trailing Stop pentru poziții de VÂNZARE
            elif position.type == mt5.ORDER_TYPE_SELL:
                current_sl = getattr(position, 'sl', 0.0) or 0.0
                new_sl = current_price + trailing_stop_distance * mt5.symbol_info(symbol).point
                if new_sl < current_sl or current_sl == 0.0:
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": position.ticket,
                        "symbol": symbol,
                        "sl": new_sl,
                        "tp": position.tp,
                        "deviation": self.trade_deviation,
                        "magic": self.magic_number,
                    }
                    result = mt5.order_send(request)
                    if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                        self.logger.log(f"✅ TS actualizat pentru poziția SELL {position.ticket} la {new_sl:.5f}")

    def get_today_total_profit(self):
        """
        Calculează profitul total din tranzacțiile închise astăzi. Corect gestionează fusul orar.
        """
        timezone = pytz.timezone("Europe/Bucharest")
        now_local = datetime.now(timezone)

        # începutul zilei locale
        start_local = timezone.localize(datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0))
        start_utc = start_local.astimezone(pytz.UTC)
        end_utc = now_local.astimezone(pytz.UTC)

        # obținem deal-urile din MT5 în interval UTC
        deals = mt5.history_deals_get(start_utc, end_utc)
        total_profit = 0.0

        if deals:
            for deal in deals:
                try:
                    total_profit += float(deal.profit)
                except Exception:
                    # ignorăm eventuale câmpuri neașteptate
                    continue

        # log pentru debugging
        self.logger.log(f"[DEBUG] Calcul profit zilnic (TradeManager): {total_profit:.2f} (interval UTC {start_utc} - {end_utc})")
        return total_profit
