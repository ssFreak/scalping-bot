import pandas as pd


def calculate_atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_prev_close = (df["high"] - df["close"].shift()).abs()
    low_prev_close = (df["low"] - df["close"].shift()).abs()
    df["tr"] = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    df["atr"] = df["tr"].ewm(span=period, adjust=False).mean()
    return df


def calculate_daily_pivots_from_rates(d1_rates_df):
    """
    Pivoturi clasice din sesiunea precedentă (folosește bara D1 anterioară).
    """
    if d1_rates_df is None or len(d1_rates_df) < 2:
        return None
    prev = d1_rates_df.iloc[-2]
    high, low, close = float(prev["high"]), float(prev["low"]), float(prev["close"])
    pp = (high + low + close) / 3.0
    r1, s1 = 2 * pp - low, 2 * pp - high
    r2, s2 = pp + (high - low), pp - (high - low)
    return {"PP": pp, "R1": r1, "S1": s1, "R2": r2, "S2": s2}
