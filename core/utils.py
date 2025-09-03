# core/utils.py
import pandas as pd
from datetime import datetime
import pytz

def calculate_pivots(df):
    high, low, close = df.iloc[-1]['high'], df.iloc[-1]['low'], df.iloc[-1]['close']
    pivot = (high + low + close) / 3
    r1, s1 = 2*pivot - low, 2*pivot - high
    r2, s2 = pivot + (high - low), pivot - (high - low)
    return {'PP': pivot, 'R1': r1, 'S1': s1, 'R2': r2, 'S2': s2}

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_prev_close = abs(df['high'] - df['close'].shift())
    low_prev_close = abs(df['low'] - df['close'].shift())
    df['tr'] = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    df['atr'] = df['tr'].ewm(span=period, adjust=False).mean()
    return df

def is_forex_market_open():
    """
    Verifică dacă piața Forex este deschisă între 08:00 și 23:59, de luni până vineri.
    Folosește fusul orar Europe/Bucharest pentru coerență.
    """
    tz = pytz.timezone("Europe/Bucharest")
    now = datetime.now(tz)
    weekday = now.weekday()  # Luni = 0, Vineri = 4, Sâmbătă = 5, Duminică = 6
    hour = now.hour

    # Verifică pentru zilele lucrătoare (Luni-Vineri)
    if 0 <= weekday <= 4:
        # Verifică pentru orele de tranzacționare
        if 8 <= hour < 23 or (hour == 23 and now.minute < 59):
            return True
    return False
