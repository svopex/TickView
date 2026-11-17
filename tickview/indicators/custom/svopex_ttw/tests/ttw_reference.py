"""Doslovný přepis Pine skriptu Svopex TTW (bez optimalizací) pro kontrolu převodu.

Kód záměrně kopíruje strukturu Pine (funkce f_*, smyčky, přístup k sériím
přes index) a nic necachuje ani předčasně neukončuje. Test porovnává jeho
výsledky s optimalizovaným převodem v ../indicator.py.
"""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

na = math.nan


def isna(value) -> bool:
    return value != value


def pine_for(start: int, end: int):
    # Smyčka Pine: včetně konce, při start > end sestupně
    return range(start, end + 1) if end >= start else range(start, end - 1, -1)


def pmin(a, b):
    return na if isna(a) or isna(b) else min(a, b)


def pmax(a, b):
    return na if isna(a) or isna(b) else max(a, b)


def reference(candles, p: dict, timeframe: str, kind: str, tick: float, step_ms: int):
    """Vrátí (úsečky [(start, cena, konec, cena, barva)], signály {(index, směr): délka})."""
    n = len(candles)
    rev = list(reversed(candles))

    # Přístup k sériím jako v Pine: mimo historii na
    def series(attr):
        return lambda i: getattr(rev[i], attr) if 0 <= i < n else na

    open_, high, low, close = series("open"), series("high"), series("low"), series("close")
    zone = ZoneInfo(p["midnight_timezone"])

    def hour(i):
        return datetime.fromtimestamp(rev[i].open_time / 1000, tz=zone).hour if 0 <= i < n else na

    def is_monday(i):
        return datetime.fromtimestamp(rev[i].open_time / 1000, tz=zone).weekday() == 0

    length = p["bars_to_process"]
    gAcs = sum(high(i) - low(i) for i in range(length)) / length if n >= length else na

    def f_pulnoc(idx):
        res = False
        if p["skip_midnight_candles"] and step_ms < 7_200_000:
            h = hour(idx)
            if not isna(h) and h == p["midnight_hour"] and not is_monday(idx):
                res = True
        return res

    def f_minLen(acs):
        return tick * p["min_candle_length"] * 10 if (timeframe == "1h" and kind == "forex") \
            else acs * 3 / p["max_candle_length"] * p["min_candle_length"]

    def f_maxLen(acs):
        return tick * p["max_candle_length"] * 10 if (timeframe == "1h" and kind == "forex") else acs * 3

    def f_prumer(idx, ls):
        hi = (high(idx) + low(idx)) / 2
        lo = hi
        for j in pine_for(idx, idx + p["from_extreme_bars"] - 1):
            m = (high(j) + low(j)) / 2
            if m > hi:
                hi = m
            if m < lo:
                lo = m
        k = p["from_extreme_coef"]
        return (hi - lo) * k + lo if ls == 0 else (hi - lo) * (1 - k) + lo

    def extrem(s, idx, per, lowest):
        res = False
        if idx >= per + 1:
            v = s(idx)
            res = True
            for j in pine_for(idx - per, idx - 1):
                if (s(j) <= v) if lowest else (s(j) >= v):
                    res = False
            for j in pine_for(idx + 1, idx + per - 1):
                if (s(j) < v) if lowest else (s(j) > v):
                    res = False
        return res

    def f_majSwingLow(idx):
        return low(idx) if extrem(low, idx, p["major_swing_width"], True) else 0.0

    def f_majSwingHigh(idx):
        return high(idx) if extrem(high, idx, p["major_swing_width"], False) else 0.0

    def f_isSwingMajor(idx, ls):
        per = min(p["major_swing_width"], p["minor_swing_width"])
        if ls == 0:
            return extrem(low, idx, per, True) if p["first_touches_on_wicks"] \
                else extrem(open_, idx, per, False) or extrem(close, idx, per, True)
        return extrem(high, idx, per, False) if p["first_touches_on_wicks"] \
            else extrem(open_, idx, per, False) or extrem(close, idx, per, True)

    def f_isSwingMinor(idx, ls, acs):
        good = False
        m = p["swing_height_mult"]
        if ls == 0:
            for i in pine_for(0, p["swing_bars"] - 1):
                pr = low(idx) if p["first_touches_on_wicks"] else pmin(close(idx), open_(idx))
                lft = high(idx + (i + 1))
                rgt = high(idx - (i + 1)) if idx - (i + 1) >= 0 else na
                if (pr + acs * m < rgt) or (pr + acs * m < lft):
                    good = True
        else:
            for j in pine_for(0, p["swing_bars"] - 1):
                pr = high(idx) if p["first_touches_on_wicks"] else pmax(open_(idx), close(idx))
                lft = low(idx + (j + 1))
                rgt = low(idx - (j + 1)) if idx - (j + 1) >= 0 else na
                if (pr - acs * m > rgt) or (pr - acs * m > lft):
                    good = True
        if ls == 0:
            for k in pine_for(0, p["swing_bars"] - 1):
                pr = low(idx)
                lft = low(idx + (k + 1))
                rgt = low(idx - (k + 1)) if idx - (k + 1) >= 0 else na
                if (not isna(lft) and lft < pr) or (not isna(rgt) and rgt < pr):
                    good = False
        else:
            for k in pine_for(0, p["swing_bars"] - 1):
                pr = high(idx)
                lft = high(idx + (k + 1))
                rgt = high(idx - (k + 1)) if idx - (k + 1) >= 0 else na
                if (not isna(lft) and lft > pr) or (not isna(rgt) and rgt > pr):
                    good = False
        return good

    def f_formaceLong(idx, acs):
        o1, o2, o3 = open_(idx), open_(idx + 1), open_(idx + 2)
        c1, c2, c3 = close(idx), close(idx + 1), close(idx + 2)
        l1, l2, l3 = low(idx), low(idx + 1), low(idx + 2)
        h1, h2 = high(idx), high(idx + 1)
        minLen, maxLen = f_minLen(acs), f_maxLen(acs)
        res = 0
        rOne = abs(c1 - l1)
        if rOne >= minLen and rOne <= maxLen:
            if c1 > o1 and (c1 - o1) * (1 / 0.6) > (h1 - l1) and (h1 - c1) * 2 < (o1 - l1):
                res = 1
        rStar = pmax(pmax(abs(c1 - l1), abs(c1 - l2)), abs(c1 - l3))
        if rStar >= minLen and rStar <= maxLen:
            zaklad = c3 < o3 and c1 > o1 and abs(c2 - o2) * p["star_ratio"] < abs(c1 - o1) \
                and abs(c2 - o2) * p["star_ratio"] < abs(c3 - o3) \
                and (c1 > h2 if p["eng_close_beyond_previous"] else c1 > pmax(o2, c2)) \
                and c1 > o2 and o3 > c2 and o3 > o2 and c2 < o3 - ((o3 - c3) / 2) and c1 != h1
            if zaklad:
                if not p["from_extreme"] or f_prumer(idx, 0) > c1:
                    res = 3
        rEng = pmax(abs(c1 - l1), abs(c1 - l2))
        if rEng >= minLen and rEng <= maxLen:
            zaklad = c2 < o2 and c1 > o1 and (c1 > h2 if p["eng_close_beyond_previous"] else c1 > o2) \
                and abs(c1 - o1) > abs(o2 - c2) and c1 != h1
            if zaklad:
                if not p["from_extreme"] or f_prumer(idx, 0) > c1:
                    res = 2
        return res

    def f_formaceShort(idx, acs):
        o1, o2, o3 = open_(idx), open_(idx + 1), open_(idx + 2)
        c1, c2, c3 = close(idx), close(idx + 1), close(idx + 2)
        l1, l2 = low(idx), low(idx + 1)
        h1, h2, h3 = high(idx), high(idx + 1), high(idx + 2)
        minLen, maxLen = f_minLen(acs), f_maxLen(acs)
        res = 0
        rOne = abs(c1 - h1)
        if rOne >= minLen and rOne <= maxLen:
            if c1 < o1 and (o1 - c1) * (1 / 0.6) > (h1 - l1) and (c1 - l1) * 2 < (h1 - o1):
                res = 1
        rStar = pmax(pmax(abs(c1 - h1), abs(c1 - h2)), abs(c1 - h3))
        if rStar >= minLen and rStar <= maxLen:
            zaklad = c3 > o3 and c1 < o1 and abs(c2 - o2) * p["star_ratio"] < abs(c1 - o1) \
                and abs(c2 - o2) * p["star_ratio"] < abs(c3 - o3) \
                and (c1 < l2 if p["eng_close_beyond_previous"] else c1 < pmin(o2, c2)) \
                and c1 < o2 and o3 < c2 and o3 < o2 and c2 > o3 + (c3 - o3) / 2 and c1 != l1
            if zaklad:
                if not p["from_extreme"] or f_prumer(idx, 1) < c1:
                    res = 3
        rEng = pmax(abs(c1 - h1), abs(c1 - h2))
        if rEng >= minLen and rEng <= maxLen:
            zaklad = c2 > o2 and c1 < o1 and (c1 < l2 if p["eng_close_beyond_previous"] else c1 < o2) \
                and abs(o1 - c1) > abs(c2 - o2) and c1 != l1
            if zaklad:
                if not p["from_extreme"] or f_prumer(idx, 1) < c1:
                    res = 2
        return res

    def f_formace(idx, ls, acs):
        return f_formaceLong(idx, acs) if ls == 0 else f_formaceShort(idx, acs)

    def f_existSpace(s, m, t):
        return (s - m > p["min_gap"]) and (m - t > p["min_gap"])

    def f_linkaProtina(iS, iP, iT, df, ls):
        mira = p["trendline_miss"]
        o1, o2, c1, c2 = open_(iT), open_(iT + 1), close(iT), close(iT + 1)
        l1, l2, h1, h2 = low(iT), low(iT + 1), high(iT), high(iT + 1)
        if ls == 0:
            pS = low(iS) if p["first_touches_on_wicks"] else pmin(open_(iS), close(iS))
            pP = low(iP) if p["first_touches_on_wicks"] else pmin(open_(iP), close(iP))
            pT1 = pS + (pP - pS) / float(iP - iS) * float(iT - iS)
            pT2 = pS + (pP - pS) / float(iP - iS) * float((iT + 1) - iS)
            if p["last_touch_on_wicks"]:
                lo1 = l1 - (h1 - l1) * mira
                lo2 = l2 - (h1 - l1) * mira
            else:
                lo1 = pmin(c1, o1) - (pmin(c1, o1) - l1) / 2 - (h1 - l1) * mira
                lo2 = pmin(c2, o2) - (pmin(c1, o1) - l1) / 2 - (h1 - l1) * mira
            res = ((lo1 <= pT1) and (pT1 <= l1 + (h1 - l1) / 2)) or ((df > 1) and (lo2 <= pT2) and (pT2 <= l2 + (h2 - l2) / 2))
        else:
            pS = high(iS) if p["first_touches_on_wicks"] else pmax(open_(iS), close(iS))
            pP = high(iP) if p["first_touches_on_wicks"] else pmax(open_(iP), close(iP))
            pT1 = pS + (pP - pS) / float(iP - iS) * float(iT - iS)
            pT2 = pS + (pP - pS) / float(iP - iS) * float((iT + 1) - iS)
            if p["last_touch_on_wicks"]:
                hi1 = h1 + (h1 - l1) * mira
                hi2 = h2 + (h1 - l1) * mira
            else:
                hi1 = pmax(c1, o1) + (h1 - pmax(c1, o1)) / 2 + (h1 - l1) * mira
                hi2 = pmax(c2, o2) + (h2 - pmax(c2, o2)) / 2 + (h1 - l1) * mira
            res = ((hi1 >= pT1) and (pT1 >= h1 - (h1 - l1) / 2)) or ((df > 1) and (hi2 >= pT2) and (pT2 >= h2 - (h2 - l2) / 2))
        return res, pT1, pS, pP

    def f_useckaNeniProtnuta(iS, iT, pT, iP, df, ls, acs):
        res = True
        fromIdx, toIdx = iS - 1, iT + df
        k = p["segment_cross_tolerance"]
        if fromIdx >= toIdx:
            if ls == 0:
                pS = low(iS) if p["first_touches_on_wicks"] else pmin(open_(iS), close(iS))
                for idx in pine_for(fromIdx, toIdx):
                    if res and not f_pulnoc(idx):
                        pl = low(idx) if p["first_touches_on_wicks"] else pmin(open_(idx), close(idx))
                        now = pS + (pT - pS) / float(iT - iS) * float(idx - iS)
                        if now > pl + (acs * k) and iP != idx:
                            res = False
            else:
                pS = high(iS) if p["first_touches_on_wicks"] else pmax(open_(iS), close(iS))
                for idx in pine_for(fromIdx, toIdx):
                    if res and not f_pulnoc(idx):
                        ph = high(idx) if p["first_touches_on_wicks"] else pmax(open_(idx), close(idx))
                        now = pS + (pT - pS) / float(iT - iS) * float(idx - iS)
                        if now < ph - (acs * k) and iP != idx:
                            res = False
        return res

    def f_testDoubleBottom(iT, iS, ls, acs):
        dbk = p["double_bottom_tolerance"]
        found, price = False, 0.0
        lT, hT, oT = low(iT), high(iT), open_(iT)
        if ls == 0:
            p1 = f_majSwingLow(iS)
            major = p1 > 0 and ((lT > p1 - acs * dbk and lT < p1 + acs * dbk) or (oT > p1 - acs * dbk and oT < p1 + acs * dbk) or (lT <= p1 and oT >= p1))
            if major:
                found, price = True, p1
            else:
                p2 = pmin(open_(iS), close(iS))
                major2 = p1 > 0 and ((lT > p2 - acs * dbk and lT < p2 + acs * dbk) or (oT > p2 - acs * dbk and oT < p2 + acs * dbk) or (lT <= p2 and oT >= p2))
                if major2:
                    found, price = True, p2
        else:
            p1 = f_majSwingHigh(iS)
            major = p1 > 0 and ((hT > p1 - acs * dbk and hT < p1 + acs * dbk) or (oT > p1 - acs * dbk and oT < p1 + acs * dbk) or (hT >= p1 and oT <= p1))
            if major:
                found, price = True, p1
            else:
                p2 = pmax(open_(iS), close(iS))
                major2 = p1 > 0 and ((hT > p2 - acs * dbk and hT < p2 + acs * dbk) or (oT > p2 - acs * dbk and oT < p2 + acs * dbk) or (hT >= p2 and oT <= p2))
                if major2:
                    found, price = True, p2
        return found, price

    def f_dbJeSvickaVSignalu(iT, iS, df, ls, price):
        res = False
        fromIdx, toIdx = iT + df, iS - 1
        if fromIdx <= toIdx:
            for i in pine_for(fromIdx, toIdx):
                if not res and not f_pulnoc(i):
                    if ls == 0:
                        if price > pmin(close(i), open_(i)):
                            res = True
                    else:
                        if price < pmax(close(i), open_(i)):
                            res = True
        return res

    segments, hits = [], {}
    for ls in pine_for(0, 1 if p["show_shorts"] else 0):
        for iT in pine_for(0, p["bars_to_process"] - 1):
            df = f_formace(iT, ls, gAcs)
            if df > 0:
                counter = 0
                for iS in pine_for(iT + df, iT + p["max_signal_length"]):
                    if counter >= p["max_segments_per_signal"]:
                        break
                    ok = f_isSwingMajor(iS, ls) if p["use_major_swing"] else f_isSwingMinor(iS, ls, gAcs)
                    if ok and iS - 1 >= iT + 1:
                        for iP in pine_for(iS - 1, iT + 1):
                            if counter >= p["max_segments_per_signal"]:
                                break
                            if not f_pulnoc(iS) and not f_pulnoc(iP) and not f_pulnoc(iT):
                                if f_isSwingMinor(iP, ls, gAcs):
                                    prot, pL, pS, pP = f_linkaProtina(iS, iP, iT, df, ls)
                                    if prot and f_existSpace(iS, iP, iT):
                                        if f_useckaNeniProtnuta(iS, iT, pL, iP, df, ls, gAcs):
                                            if abs(iS - iT) > p["min_formation_length"]:
                                                typeOk = (p["show_trendline"] and ((pS <= pL and ls == 0) or (pS >= pL and ls != 0))) \
                                                    or (p["show_template"] and ((pS >= pL and ls == 0) or (pS <= pL and ls != 0)))
                                                if typeOk:
                                                    counter += 1
                                                    hits[(iT, ls)] = df
                                                    segments.append((iS, pS, iT, pL, ls))
                                                    if p["two_color_line"]:
                                                        segments.append((iS, pS, iP, pP, "mid"))
    if p["show_double_top_bottom"]:
        for ls in pine_for(0, 1 if p["show_shorts"] else 0):
            for iT in pine_for(0, p["bars_to_process"] - 1):
                df = f_formace(iT, ls, gAcs)
                if df > 0:
                    for iS in pine_for(iT + df, iT + p["max_signal_length"]):
                        if not f_pulnoc(iS) and not f_pulnoc(iT):
                            found, price = f_testDoubleBottom(iT, iS, ls, gAcs)
                            if found:
                                if not f_dbJeSvickaVSignalu(iT, iS, df, ls, price):
                                    if abs(iS - iT) > p["min_double_bottom_length"]:
                                        hits[(iT, ls)] = df
                                        segments.append((iS, price, iT, price, f"db{ls}"))
                                break
    return segments, hits
