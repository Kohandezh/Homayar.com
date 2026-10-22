"""Word count of a transcript text, the numerator of a speaking rate."""

from __future__ import annotations


def count_words(text: str) -> int:
    """Count tokens split on Unicode whitespace that hold at least one letter or digit.

    ZWNJ (U+200C, the Persian half-space) is not whitespace, so a word written with it
    counts once.  A token of only punctuation, such as ``-`` or ``...``, is not a word.
    """
    return sum(1 for token in text.split() if any(char.isalnum() for char in token))
