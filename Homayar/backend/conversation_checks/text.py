"""Tokens and doubt cues of one turn (HOM-55).

Every fold goes through ``backend.speech.text_normalize``, the single owner of text
normalization.  This module only splits the folded text into tokens, finds whole-token
phrase matches, and marks the tokens that carry a negation or quotation cue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from backend.speech.text_normalize import normalize, normalize_for_matching, registered_languages

# Negation cues, compared as whole folded tokens.  The SPEC minimum plus a few common
# forms; a new cue only sends more matches to review.
NEGATION_CUES = frozenset(
    {
        # English
        "not",
        "no",
        "never",
        "cannot",
        "nothing",
        "nobody",
        "none",
        "neither",
        "nor",
        "without",
        # Persian
        "نه",
        "هرگز",
        "نباید",
        "نیست",
        "نیستم",
        "نیستیم",
        "نیستید",
        "نیستند",
        "نبود",
        "نبوده",
        "ندارم",
        "ندارد",
        "نداریم",
        "ندارید",
        "ندارند",
        "نکنید",
        "نکرد",
        "نکردم",
        "نکردیم",
        "نکرده",
        "نخواهد",
        "نخواهیم",
        "نشد",
        "نشده",
        "هیچ",
        "هیچوقت",
        # Azerbaijani (basic)
        "deyil",
        "yox",
        "heç",
    }
)
# A folded token that starts with one of these is a negation cue (Persian نمی‌ verbs).
NEGATION_PREFIXES = ("نمی",)
# A reporting verb anywhere in the turn makes every match in it quoted speech.
REPORTING_VERBS = frozenset(
    {
        # English
        "said",
        "says",
        "told",
        "tells",
        "saying",
        # Persian
        "گفت",
        "گفتند",
        "گفتید",
        "گفتم",
        "گفتی",
        "گفتیم",
        "گفته",
        "میگوید",
        "میگویند",
        "میگفت",
        "میگفتند",
        # Azerbaijani (basic)
        "dedi",
        "dedilər",
    }
)
# Quote marks, read in the original text, where « and » keep their direction (the Persian
# normalizer turns both into ").  A straight quote closes an open straight quote and opens
# a new one otherwise.  Each opening mark has its own closing mark.
STRAIGHT_QUOTES = frozenset({'"', "＂"})
QUOTE_PAIRS = {"“": "”", "«": "»"}
OPEN_QUOTES = frozenset(QUOTE_PAIRS)
CLOSE_QUOTES = frozenset(QUOTE_PAIRS.values())
_ALL_QUOTES = STRAIGHT_QUOTES | OPEN_QUOTES | CLOSE_QUOTES
# An English n't contraction; the fold splits "can't" into the tokens "can" and "t".
_CONTRACTION = re.compile(r"n['’]t\b", re.IGNORECASE)


def fold_tokens(text: str, language: str) -> tuple[str, ...]:
    """The tokens of ``normalize_for_matching(text, language)``, split on a single space."""
    folded = normalize_for_matching(text, language)
    return tuple(folded.split(" ")) if folded else ()


def folds_in_some_language(text: str) -> bool:
    """True when ``text`` folds to at least one token in some registered language."""
    return any(fold_tokens(text, language) for language in registered_languages())


def find_matches(phrase: tuple[str, ...], tokens: tuple[str, ...]) -> list[tuple[int, int]]:
    """Every half-open ``(start, end)`` where ``phrase`` is a contiguous run of ``tokens``."""
    size = len(phrase)
    if not size:
        return []
    return [
        (start, start + size)
        for start in range(len(tokens) - size + 1)
        if tokens[start : start + size] == phrase
    ]


@dataclass(frozen=True)
class TurnText:
    """One turn's folded tokens and, per token, whether a cue marks it."""

    tokens: tuple[str, ...]
    quoted: tuple[bool, ...]  # inside quote marks
    negation: tuple[bool, ...]  # a negation cue
    reporting: bool  # the turn holds a reporting verb


@cache
def _cues(language: str) -> tuple[frozenset[str], tuple[str, ...], frozenset[str]]:
    """The cue words folded the way a turn of ``language`` is folded."""
    negation = frozenset(normalize_for_matching(word, language) for word in NEGATION_CUES)
    prefixes = tuple(normalize_for_matching(word, language) for word in NEGATION_PREFIXES)
    reporting = frozenset(normalize_for_matching(word, language) for word in REPORTING_VERBS)
    return negation, prefixes, reporting


def clear_caches() -> None:
    _cues.cache_clear()


def analyse_turn(text: str, language: str) -> TurnText:
    tokens = fold_tokens(text, language)
    negation_words, prefixes, reporting_words = _cues(language)
    shown = normalize(text, language)
    negation = [token in negation_words or token.startswith(prefixes) for token in tokens]
    if _CONTRACTION.search(shown):
        for index in range(1, len(tokens)):
            if tokens[index] == "t" and tokens[index - 1].endswith("n"):
                negation[index - 1] = negation[index] = True
    return TurnText(
        tokens=tokens,
        quoted=_quoted_flags(text, shown, tokens, language),
        negation=tuple(negation),
        reporting=any(token in reporting_words for token in tokens),
    )


def _quoted_flags(
    text: str, shown: str, tokens: tuple[str, ...], language: str
) -> tuple[bool, ...]:
    """Per token, whether it lies inside quote marks, at any depth.

    The original ``text`` is cut at each quote mark and every piece is folded on its own.
    Quote marks are separators for the fold, so the pieces' tokens line up with
    ``tokens``.  A stack of open marks gives the depth of each piece.  A straight mark
    cannot show whether it opens or closes, so with two or more straight pairs every piece
    between the first and the last straight mark counts as quoted: ``"a "b" c"`` is one
    quote with ``b`` inside it, not two quotes with ``b`` outside.

    Fail safe: every token counts as quoted when the structure is not clear (a mark closes
    nothing or the wrong mark, a quote is never closed), when the pieces' tokens do not
    line up, or when the original text has no quote mark but its punctuation-keeping form
    ``shown`` has one.  More review, never a missed doubt.
    """
    pieces: list[tuple[str, bool]] = []
    stack: list[str] = []
    straight: list[int] = []  # the piece index each straight mark ends
    clear = True
    start = 0
    for position, char in enumerate(text):
        if char not in _ALL_QUOTES:
            continue
        pieces.append((text[start:position], bool(stack)))
        start = position + 1
        if char in STRAIGHT_QUOTES:
            straight.append(len(pieces) - 1)
            if stack and stack[-1] in STRAIGHT_QUOTES:
                stack.pop()
            else:
                stack.append(char)
        elif char in OPEN_QUOTES:
            stack.append(char)
        elif stack and QUOTE_PAIRS.get(stack[-1]) == char:
            stack.pop()
        else:
            clear = False
    pieces.append((text[start:], bool(stack)))
    if len(straight) >= 4:
        first, last = straight[0], straight[-1]
        pieces = [
            (piece, inside or first < index <= last) for index, (piece, inside) in enumerate(pieces)
        ]
    if len(pieces) == 1:
        quoted_anyway = any(char in _ALL_QUOTES for char in shown)
        return (quoted_anyway,) * len(tokens)
    piece_tokens: list[str] = []
    flags: list[bool] = []
    for piece, piece_inside in pieces:
        folded = fold_tokens(piece, language)
        piece_tokens.extend(folded)
        flags.extend([piece_inside] * len(folded))
    if clear and not stack and tuple(piece_tokens) == tokens:
        return tuple(flags)
    return (True,) * len(tokens)
