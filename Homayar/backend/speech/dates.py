"""Jalali calendar, spoken Persian numbers and natural date/time expressions.

* Jalali ⇄ Gregorian conversion: the 33-year break-table arithmetic used by
  the reference ``jalaali`` implementations (Borkowski), implemented here from
  the published algorithm; valid for Jalali years -61..3177.
* Spoken numbers: ``یک``…``نهصد``, ``هزار``/``میلیون``/``میلیارد`` joined by
  ``و`` (``بیست و پنج``, ``دو هزار و پانصد``), common colloquial forms
  (``یه``, ``پونزده``, ``پونصد``) and ordinals (``پنجم``, ``سوم``, ``اول``,
  ``بیست و یکم``, ``سی ام``).
* Expressions: ``امروز``/``فردا``/``پس‌فردا``/``دیروز``/``پریروز``, ``N روز
  دیگه/بعد/پیش``, ``N هفته دیگه``, ``هفته بعد/آینده``, weekdays with or without
  ``آینده``/``بعد``, ``۱۵ مهر``/``پانزدهم مهر ۱۴۰۴``, ``۱۴۰۳/۱۲/۳۰`` and ISO
  Gregorian dates; times anchored by ``ساعت``, a day-period word (``صبح``,
  ``ظهر``, ``بعدازظهر``, ``عصر``, ``شب``) or ``HH:MM``, with ``و نیم``/``و
  ربع``/``و N دقیقه``.

Nothing is guessed silently: every match carries all plausible candidates,
``ambiguous=True`` when more than one remains, a heuristic confidence (a rule
prior, not a measured accuracy) and character offsets into the original text.
A bare ``ده و نیم`` is only read as a time when anchored (see above) — without
an anchor it is as likely to be an amount.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

from backend.privacy.text_pii import normalize_digits
from backend.speech.text_normalize import unify_arabic_letters

TEHRAN = ZoneInfo("Asia/Tehran")

# --------------------------------------------------------------------------
# Jalali calendar
# --------------------------------------------------------------------------

_BREAKS = (
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
    1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178,
)  # fmt: skip


def _div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _mod(a: int, b: int) -> int:
    return a - _div(a, b) * b


def _jal_cal(jy: int) -> tuple[int, int, int]:
    """(leap index, gregorian year, march day of Farvardin 1). leap==0 ⇒ leap year."""
    if jy < _BREAKS[0] or jy >= _BREAKS[-1]:
        raise ValueError(f"Jalali year out of supported range: {jy}")
    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]
    jump = 0
    for jm in _BREAKS[1:]:
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return leap, gy, march


def is_jalali_leap(jy: int) -> bool:
    return _jal_cal(jy)[0] == 0


def jalali_month_length(jy: int, jm: int) -> int:
    if not 1 <= jm <= 12:
        raise ValueError("Jalali month must be 1..12")
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if is_jalali_leap(jy) else 29


def is_valid_jalali(jy: int, jm: int, jd: int) -> bool:
    try:
        return 1 <= jm <= 12 and 1 <= jd <= jalali_month_length(jy, jm)
    except ValueError:
        return False


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    if not is_valid_jalali(jy, jm, jd):
        raise ValueError(f"Invalid Jalali date {jy}/{jm}/{jd}")
    _, gy, march = _jal_cal(jy)
    ordinal = date(gy, 3, march).toordinal() + (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1
    return date.fromordinal(ordinal)


def gregorian_to_jalali(d: date) -> tuple[int, int, int]:
    gy = d.year
    jy = gy - 621
    leap, _, march = _jal_cal(jy)
    k = d.toordinal() - date(gy, 3, march).toordinal()
    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if leap == 1:
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


JALALI_MONTHS = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)  # fmt: skip
_MONTH_ALIASES = {name: i + 1 for i, name in enumerate(JALALI_MONTHS)}
_MONTH_ALIASES.update({"امرداد": 5, "اردی بهشت": 2})

# Python weekday(): Monday=0 … Sunday=6.
WEEKDAYS_FA = {
    "شنبه": 5, "یکشنبه": 6, "دوشنبه": 0, "سه شنبه": 1,
    "سشنبه": 1, "چهارشنبه": 2, "پنجشنبه": 3, "جمعه": 4,
}  # fmt: skip
WEEKDAY_NAMES_FA = {v: k for k, v in WEEKDAYS_FA.items() if k != "سشنبه"}


def format_jalali(d: date, *, month_name: bool = False) -> str:
    jy, jm, jd = gregorian_to_jalali(d)
    if month_name:
        return f"{jd} {JALALI_MONTHS[jm - 1]} {jy}"
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


# --------------------------------------------------------------------------
# Spoken numbers
# --------------------------------------------------------------------------

_UNITS = {
    "صفر": 0, "یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "چار": 4,
    "پنج": 5, "شش": 6, "شیش": 6, "هفت": 7, "هشت": 8, "نه": 9,
    "ده": 10, "یازده": 11, "دوازده": 12, "سیزده": 13, "چهارده": 14,
    "پانزده": 15, "پونزده": 15, "شانزده": 16, "شونزده": 16,
    "هفده": 17, "هیفده": 17, "هجده": 18, "هیجده": 18, "نوزده": 19,
    "بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50, "شصت": 60,
    "هفتاد": 70, "هشتاد": 80, "نود": 90,
    "صد": 100, "یکصد": 100, "دویست": 200, "سیصد": 300, "چهارصد": 400,
    "پانصد": 500, "پونصد": 500, "ششصد": 600, "شیشصد": 600, "هفتصد": 700,
    "هشتصد": 800, "نهصد": 900,
}  # fmt: skip
_SCALES = {"هزار": 1_000, "میلیون": 1_000_000, "میلیارد": 1_000_000_000}
_ORDINAL_SPECIAL = {"اول": 1, "نخست": 1, "سوم": 3, "سوم‌": 3, "سیم": 3}


def _word_value(word: str) -> tuple[int, bool] | None:
    """(value, is_ordinal) for a single number word."""
    if word in _UNITS:
        return _UNITS[word], False
    if word in _SCALES:
        return _SCALES[word], False
    if word in _ORDINAL_SPECIAL:
        return _ORDINAL_SPECIAL[word], True
    for suffix in ("مین", "م"):
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            if stem in _UNITS and stem not in {"صفر", "یه", "نه"} or stem in _SCALES:
                return (_UNITS.get(stem) or _SCALES[stem]), True
            if stem == "نه" and suffix == "م":  # نهم
                return 9, True
    return None


def _is_digits(tok: str) -> bool:
    return tok.isascii() and tok.isdigit()


def _parse_number_at(words: list[str], i: int) -> tuple[int, int, bool] | None:
    """Parse a spoken or digit number starting at ``words[i]``.
    Returns (value, index after last consumed word, is_ordinal)."""
    if i >= len(words):
        return None
    first = words[i]
    if _is_digits(first):
        j = i + 1
        ordinal = False
        if j < len(words) and words[j] in {"ام", "م", "امین"}:
            ordinal, j = True, j + 1
        return int(first), j, ordinal
    if _word_value(first) is None:
        return None
    total, current = 0, 0
    j = i
    ordinal = False
    prev = "start"  # "unit" | "scale" | "sep"
    while j < len(words):
        wv = _word_value(words[j])
        if wv is None:
            break
        value, is_ord = wv
        if words[j] in _SCALES or (is_ord and value in _SCALES.values()):
            current = max(current, 1) * value
            total += current
            current = 0
            prev = "scale"
        elif words[j] == "صد" and prev == "unit" and 1 <= current <= 9:
            current *= 100
        else:
            if prev == "unit":
                break  # "بیست سی" is two numbers, not one
            current += value
            prev = "unit"
        j += 1
        if is_ord:
            ordinal = True
            break
        if j + 1 < len(words) and words[j] == "و" and _word_value(words[j + 1]) is not None:
            j += 1
            prev = "sep"
            continue
        if j < len(words) and (words[j] in _SCALES or words[j] == "صد"):
            continue
        if j < len(words) and words[j] in {"ام", "امین"}:
            ordinal = True
            j += 1
        break
    return total + current, j, ordinal


_WORD_SPLIT_RE = re.compile(r"[^\s‌،,.؛;؟?!()«»\"]+")


def _shadow(text: str) -> str:
    """Same length as ``text``: digits and Arabic letters unified, ZWNJ → space."""
    return unify_arabic_letters(normalize_digits(text)).replace("‌", " ")


def parse_spoken_number(text: str) -> int | None:
    """Whole-string spoken/digit number, e.g. ``"بیست و پنج"`` → 25, ``"پنجم"`` → 5."""
    words = _WORD_SPLIT_RE.findall(_shadow(text.strip()))
    parsed = _parse_number_at(words, 0)
    if parsed is None or parsed[1] != len(words):
        return None
    return parsed[0]


def parse_ordinal(text: str) -> int | None:
    words = _WORD_SPLIT_RE.findall(_shadow(text.strip()))
    parsed = _parse_number_at(words, 0)
    if parsed is None or parsed[1] != len(words) or not parsed[2]:
        return None
    return parsed[0]


# --------------------------------------------------------------------------
# Expression extraction
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date  # inclusive


@dataclass(frozen=True)
class TemporalMatch:
    kind: str  # "date" | "date_range" | "time"
    start: int
    end: int
    raw: str
    candidates: tuple[date | DateRange | time, ...]
    confidence: float
    rule: str
    ambiguous: bool = field(default=False)

    @property
    def value(self) -> date | DateRange | time | None:
        """The single resolved value, or None when ambiguous (never a guess)."""
        return None if self.ambiguous else self.candidates[0]


@dataclass(frozen=True)
class _Tok:
    text: str
    start: int
    end: int


def _local_today(reference: datetime, tz: tzinfo) -> date:
    if reference.tzinfo is None:
        raise ValueError("reference must be timezone-aware")
    return reference.astimezone(tz).date()


_REL_DAY = {"امروز": 0, "فردا": 1, "پسفردا": 2, "دیروز": -1, "پریروز": -2}
_FUTURE_WORDS = {"دیگه", "دیگر", "بعد", "آینده", "اینده", "بعدی"}
_PAST_WORDS = {"پیش", "قبل"}
_PERIODS = {
    "صبح": "morning", "بامداد": "morning", "ظهر": "noon", "بعدازظهر": "afternoon",
    "عصر": "afternoon", "غروب": "evening", "شب": "night", "نیمه شب": "midnight",
}  # fmt: skip

_ISO_RE = re.compile(r"(?<!\d)(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})(?!\d)")
_HHMM_RE = re.compile(r"(?<![\d/])(\d{1,2}):(\d{2})(?![\d/])")


def _week_start(d: date) -> date:
    """Persian week starts on Saturday."""
    return d - timedelta(days=(d.weekday() - 5) % 7)


def _apply_period(hour: int, minute: int, period: str | None) -> list[time]:
    if not (0 <= hour <= 24 and 0 <= minute <= 59):
        return []
    if hour == 24:
        return [time(0, minute)]
    if period is None:
        if hour == 0 or hour > 12:
            return [time(hour, minute)]
        if hour == 12:
            return [time(12, minute), time(0, minute)]
        return [time(hour, minute), time(hour + 12, minute)]
    if hour > 12:
        return [time(hour, minute)]
    if period == "morning":
        return [time(hour % 12, minute)] if hour != 12 else [time(0, minute), time(12, minute)]
    if period == "noon":
        return [time(12 if hour == 12 else hour + 12 if hour <= 4 else hour, minute)]
    if period in {"afternoon", "evening"}:
        return [time(hour % 12 + 12, minute)]
    if period == "night":
        if hour == 12:
            return [time(0, minute)]
        if hour <= 4:
            return [time(hour, minute)]  # "۲ شب" is 02:00
        return [time(hour + 12, minute)]
    if period == "midnight":
        return [time(0 if hour == 12 else hour, minute)]
    return []


def extract_temporal(
    text: str,
    reference: datetime,
    tz: tzinfo = TEHRAN,
) -> list[TemporalMatch]:
    """All date/time expressions in ``text`` relative to ``reference``
    (timezone-aware; its local date in ``tz`` is "today")."""
    today = _local_today(reference, tz)
    shadow = _shadow(text)
    out: list[TemporalMatch] = []
    used: list[tuple[int, int]] = []

    def emit(kind: str, s: int, e: int, cands: list, conf: float, rule: str) -> None:
        uniq = tuple(dict.fromkeys(cands))
        if not uniq:
            return
        out.append(
            TemporalMatch(
                kind,
                s,
                e,
                text[s:e],
                uniq,
                conf if len(uniq) == 1 else min(conf, 0.5),
                rule,
                len(uniq) > 1,
            )
        )
        used.append((s, e))

    # 1. Numeric full dates.
    for m in _ISO_RE.finditer(shadow):
        y, mo, d = (int(g) for g in m.groups())
        try:
            if y >= 1700:
                value = date(y, mo, d)
                rule = "numeric_gregorian"
            else:
                value = jalali_to_gregorian(y, mo, d)
                rule = "numeric_jalali"
        except ValueError:
            continue
        emit("date", m.start(), m.end(), [value], 0.95, rule)

    # 2. HH:MM clock times (period word may follow).
    toks_all = [_Tok(m.group(), m.start(), m.end()) for m in _WORD_SPLIT_RE.finditer(shadow)]

    def period_after(end: int) -> tuple[str | None, int]:
        rest = [t for t in toks_all if t.start >= end][:2]
        if rest and rest[0].text in {"بعد", "نیمه"} and len(rest) > 1:
            joined = rest[0].text + ("" if rest[0].text == "بعد" else " ") + rest[1].text
            if rest[0].text == "بعد" and rest[1].text in {"ازظهر", "از"}:
                more = [t for t in toks_all if t.start >= rest[1].end][:1]
                if rest[1].text == "از" and more and more[0].text == "ظهر":
                    return "afternoon", more[0].end
                if rest[1].text == "ازظهر":
                    return "afternoon", rest[1].end
            if joined in _PERIODS:
                return _PERIODS[joined], rest[1].end
        if rest and rest[0].text in _PERIODS:
            return _PERIODS[rest[0].text], rest[0].end
        return None, end

    for m in _HHMM_RE.finditer(shadow):
        if any(s <= m.start() < e for s, e in used):
            continue
        h, mi = int(m.group(1)), int(m.group(2))
        period, end = period_after(m.end())
        start = m.start()
        prev = [t for t in toks_all if t.end <= start]
        if prev and prev[-1].text == "ساعت":
            start = prev[-1].start
        emit("time", start, end, _apply_period(h, mi, period), 0.9, "clock_hhmm")

    toks = [t for t in toks_all if not any(s <= t.start < e for s, e in used)]
    words = [t.text for t in toks]
    i = 0
    while i < len(toks):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else None

        # Relative day words (پس فردا is split by ZWNJ/space in the shadow).
        if w == "پس" and nxt == "فردا":
            emit(
                "date",
                toks[i].start,
                toks[i + 1].end,
                [today + timedelta(days=2)],
                0.9,
                "relative_day",
            )
            i += 2
            continue
        if w in _REL_DAY:
            emit(
                "date",
                toks[i].start,
                toks[i].end,
                [today + timedelta(days=_REL_DAY[w])],
                0.9,
                "relative_day",
            )
            i += 1
            continue

        # هفته بعد / هفته آینده / هفته دیگه / هفته پیش
        if w == "هفته" and nxt in _FUTURE_WORDS | _PAST_WORDS | {"این"}:
            offset = 7 if nxt in _FUTURE_WORDS else -7
            ws = _week_start(today) + timedelta(days=offset)
            emit(
                "date_range",
                toks[i].start,
                toks[i + 1].end,
                [DateRange(ws, ws + timedelta(days=6))],
                0.85,
                "relative_week",
            )
            i += 2
            continue

        # Weekdays: "سه شنبه" is two tokens in the shadow.
        wd_name, wd_end_idx = None, i
        if w == "سه" and nxt == "شنبه":
            wd_name, wd_end_idx = "سه شنبه", i + 1
        elif w in WEEKDAYS_FA:
            wd_name = w
        if wd_name is not None:
            target = WEEKDAYS_FA[wd_name]
            after = words[wd_end_idx + 1] if wd_end_idx + 1 < len(words) else None
            delta = (target - today.weekday()) % 7
            upcoming = today + timedelta(days=delta)
            end_idx = wd_end_idx
            if after in _FUTURE_WORDS:
                end_idx += 1
                first = upcoming if delta else upcoming + timedelta(days=7)
                cands = [first]
                if _week_start(first) == _week_start(today):
                    # "چهارشنبه آینده" said on Saturday: this week's or next week's.
                    cands.append(first + timedelta(days=7))
                emit("date", toks[i].start, toks[end_idx].end, cands, 0.8, "weekday_next")
            elif after in _PAST_WORDS:
                end_idx += 1
                back = (today.weekday() - target) % 7 or 7
                emit(
                    "date",
                    toks[i].start,
                    toks[end_idx].end,
                    [today - timedelta(days=back)],
                    0.8,
                    "weekday_past",
                )
            else:
                back = (today.weekday() - target) % 7 or 7
                cands = [upcoming, today - timedelta(days=back)]
                if delta == 0:
                    cands = [today, today + timedelta(days=7)]
                emit("date", toks[i].start, toks[end_idx].end, cands, 0.6, "weekday_bare")
            i = end_idx + 1
            continue

        # Numbers: "N روز/هفته/ماه دیگه|پیش", "15 مهر [1404]", or times.
        num = _parse_number_at(words, i)
        if num is not None:
            value, j, is_ord = num
            unit = words[j] if j < len(words) else None
            direction = words[j + 1] if j + 1 < len(words) else None
            if unit in {"روز", "هفته"} and direction in _FUTURE_WORDS | _PAST_WORDS and not is_ord:
                days = value * (7 if unit == "هفته" else 1)
                sign = 1 if direction in _FUTURE_WORDS else -1
                emit(
                    "date",
                    toks[i].start,
                    toks[j + 1].end,
                    [today + timedelta(days=sign * days)],
                    0.85,
                    "relative_n_days",
                )
                i = j + 2
                continue
            if unit in _MONTH_ALIASES or (unit == "اردی" and direction == "بهشت"):
                month = _MONTH_ALIASES.get(unit, 2)
                end_j = j + (2 if unit == "اردی" else 1)
                year_tok = _parse_number_at(words, end_j)
                if year_tok is not None and year_tok[0] >= 1300 and not year_tok[2]:
                    jy = year_tok[0]
                    if is_valid_jalali(jy, month, value):
                        emit(
                            "date",
                            toks[i].start,
                            toks[year_tok[1] - 1].end,
                            [jalali_to_gregorian(jy, month, value)],
                            0.95,
                            "day_month_year",
                        )
                    i = year_tok[1]
                    continue
                ty = gregorian_to_jalali(today)[0]
                cands = []
                for jy in (ty, ty + 1, ty - 1):
                    if is_valid_jalali(jy, month, value):
                        cands.append(jalali_to_gregorian(jy, month, value))
                future = sorted(d for d in cands if d >= today)
                past = sorted((d for d in cands if d < today), reverse=True)
                ordered = future[:1] + past[:1]
                emit("date", toks[i].start, toks[end_j - 1].end, ordered, 0.75, "day_month")
                i = end_j
                continue

            # Time: anchored by ساعت before, or a period word after.
            anchored = i > 0 and words[i - 1] == "ساعت"
            minute = 0
            k = j
            if (
                j < len(words)
                and words[j] == "دقیقه"
                and i + 2 < j
                and words[i + 1] == "و"
                and _word_value(words[i]) is not None
            ):
                # "سه و بیست دقیقه": the greedy number read "سه و بیست" as 23.
                mnum = _parse_number_at(words, i + 2)
                if mnum is not None and mnum[1] == j:
                    value, minute, k = _word_value(words[i])[0], mnum[0], j + 1  # type: ignore[index]
            elif k + 1 < len(words) and words[k] == "و" and words[k + 1] == "نیم":
                minute, k = 30, k + 2
            elif k + 1 < len(words) and words[k] == "و" and words[k + 1] == "ربع":
                minute, k = 15, k + 2
            elif k + 1 < len(words) and words[k] == "و":
                mnum = _parse_number_at(words, k + 1)
                if mnum is not None and mnum[1] < len(words) and words[mnum[1]] == "دقیقه":
                    minute, k = mnum[0], mnum[1] + 1
            period, pend = period_after(toks[k - 1].end)
            preceded_by_date = (
                bool(out)
                and out[-1].kind in {"date", "date_range"}
                and (toks[i].start - out[-1].end <= 2)
            )
            if (anchored or period is not None or preceded_by_date) and not is_ord and value <= 24:
                start = toks[i - 1].start if anchored else toks[i].start
                end = pend if period is not None else toks[k - 1].end
                conf = 0.9 if period is not None else 0.7
                emit("time", start, end, _apply_period(value, minute, period), conf, "spoken_time")
                # Skip tokens consumed by the period word.
                while k < len(toks) and toks[k].start < end:
                    k += 1
                i = k
                continue
            i = max(j, i + 1)
            continue
        i += 1

    return sorted(out, key=lambda m: m.start)
