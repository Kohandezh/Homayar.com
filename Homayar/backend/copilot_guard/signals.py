"""Injection phrase signals in untrusted text (HOM-97).

Detection is best effort.  It is a signal for audit and for the answer check, never the
defense: the defense is that the planner never sees a transcript, that routing takes the
scope from authorization, and that code chooses the tools.  A span with a signal is still
data, and it may still be cited.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from backend.speech.text_normalize import LETTER_VARIANTS_BASIC, translation_table


class InjectionSignal(StrEnum):
    IGNORE_INSTRUCTIONS = "ignore_instructions"
    ROLE_REASSIGNMENT = "role_reassignment"
    ROLE_TAG = "role_tag"
    TOOL_CALL = "tool_call"
    FENCE_SPOOF = "fence_spoof"


# Arabic yeh, alef maksura and kaf become the Persian letters.  The table comes from the
# owner module.  Only these three of its letters apply (SPEC 3.2): teh marbuta and the
# alef forms stay as they are.  Its normalize and normalize_for_matching are not used:
# they also change commas, quotes, digits and case and drop punctuation, and the number,
# quote, role-tag and link checks depend on those.
_SPEC_LETTERS = frozenset({"ARABIC LETTER YEH", "ARABIC LETTER ALEF MAKSURA", "ARABIC LETTER KAF"})
_LETTERS = translation_table(
    {
        arabic: persian
        for arabic, persian in LETTER_VARIANTS_BASIC.items()
        if unicodedata.name(arabic) in _SPEC_LETTERS
    }
)
_WHITESPACE = re.compile(r"\s+")
# The line breaks a model may read as a new line.  A role tag counts only at a line start.
_LINE_BREAK = re.compile(r"[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")
_PUNCTUATION = re.compile(r"[^\w\s]+")


def normalize_text(text: str) -> str:
    """NFKC; Arabic yeh, alef maksura and kaf to the Persian letters; every ``Cf``
    character removed (ZWNJ and ZWJ too); each run of whitespace to one space.  No case
    folding.  The quote check of ``check_answer`` uses exactly this."""
    text = unicodedata.normalize("NFKC", text).translate(_LETTERS)
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    return _WHITESPACE.sub(" ", text)


def _fold(text: str) -> str:
    """``normalize_text``, then case folded and without combining marks (``Mn``), so
    ``İGNORE``, accents and harakat do not hide a phrase."""
    decomposed = unicodedata.normalize("NFD", normalize_text(text).casefold())
    kept = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return _WHITESPACE.sub(" ", kept.translate(_LETTERS))


def _words(folded: str) -> str:
    """The folded text with punctuation turned into spaces: the view of the phrase
    rules, so ``ignore, previous instructions`` still reads as a phrase."""
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", folded)).strip()


# --- phrase rules, matched on the words view --------------------------------------------------

# "previous instructions", "your rules": a strong qualifier takes any orders word.
_QUALIFIER = (
    r"(?:previous|prior|above|earlier|preceding|former|foregoing|original|initial|system|your)"
)
# "all instructions": a weak qualifier takes only the words of a prompt, because "forget
# all the rules of the promotion" is ordinary call-center speech.
_WEAK_QUALIFIER = r"(?:all|any|every|these|those)"
_PROMPT_ORDERS = r"(?:instructions?|prompts?|directives?|guardrails?|guidelines?)"
_ORDERS = (
    r"(?:instructions?|prompts?|rules?|directions?|directives?|guidelines?|guardrails?|"
    r"commands?|context|constraints?|restrictions?|polic(?:y|ies))"
)
_ROLE = (
    r"(?:assistants?|ai|chatbot|bot|model|llm|gpt|admins?|administrators?|sysadmin|superadmin|"
    r"superuser|root|system|developer|dan|jailbroken|unrestricted|unfiltered|uncensored|god)"
)
_FA_ORDERS = r"(?:دستورالعمل|دستور|قوانین|قانون|قواعد|رهنمود|پرامپت|تعلیمات)(?:ات|ی| ?ها(?:ی|یی)?)?"
_FA_ALL = r"(?:همه|همهی|تمام|تمامی|کلیه)(?: ی)?"
_FA_EARLIER = r"(?:قبلی|قبلیت|قبلیات|قبل|پیشین|بالا|بالایی|سیستمی|سیستم|اولیه|اصلی|خودت)"
_FA_DROP = (
    r"(?:نادیده ?بگیر|نادیده ?گرفته (?:شود|شوند|بشه)|فراموش ?کن|کنار ?بگذار|کنار ?بذار|"
    r"اعتنا ?نکن|توجه ?نکن|پیروی ?نکن|اطاعت ?نکن|عمل ?نکن|لغو ?کن|بیخیال)"
)
_FA_DROP_STRONG = (
    r"(?:نادیده ?بگیر|نادیده ?گرفته (?:شود|شوند|بشه)|فراموش ?کن|اعتنا ?نکن|توجه ?نکن|"
    r"پیروی ?نکن|اطاعت ?نکن)"
)
_NOT_A_STATEMENT = "".join(
    rf"(?<!\b{word} )"
    for word in ("i", "we", "they", "he", "she", "always", "often", "usually", "sometimes")
)
_FA_ROLE = (
    r"(?:مدیر سیستم|مدیر کل|ادمین|ادمینیستراتور|دستیار|ربات|چت ?بات|هوش مصنوعی|مدل زبانی|"
    r"توسعه ?دهنده|برنامه ?نویس|روت|سوپر ?یوزر|کاربر ارشد|سیستم)"
)
_FA_BE = r"(?:هستی|هستید|هستین|باش|باشی|باشید|خواهی بود|خواهید بود|شدی|شدید|میشوی|میشوید)"
_FA_FROM_NOW = (
    r"(?:از این به بعد|ازین به بعد|از الان به بعد|از حالا به بعد|از این لحظه به بعد|"
    r"از این لحظه|از الان|از حالا|از اکنون)"
)

_WORD_RULES = {
    InjectionSignal.IGNORE_INSTRUCTIONS: (
        rf"\b(?:ignore|disregard|forget|override|bypass)\b(?: \w+){{0,3}}? {_QUALIFIER} "
        rf"(?:\w+ ){{0,2}}?{_ORDERS}\b",
        rf"\b(?:ignore|disregard|forget|override|bypass)\b(?: \w+){{0,3}}? {_WEAK_QUALIFIER} "
        rf"(?:\w+ ){{0,2}}?{_PROMPT_ORDERS}\b",
        # The qualifier after the orders word: "the instructions above", "given earlier".
        rf"\b(?:ignore|disregard|forget|override|bypass)\b(?: \w+){{0,3}}? {_ORDERS} "
        rf"(?:\w+ )?(?:above|before|earlier|previously|given)\b",
        # No qualifier at all, but a prompt word right after the verb: "ignore instructions".
        # A subject or a habit word before the verb is a statement, not an order: "I always
        # forget the instructions".
        rf"{_NOT_A_STATEMENT}\b(?:ignore|disregard|forget|override|bypass) "
        rf"(?:the |these |those |my |your )?{_PROMPT_ORDERS}\b",
        rf"\b(?:do not|don t|dont|never|stop) (?:follow|obey|following|obeying)(?: \w+){{0,2}}? "
        rf"(?:previous|prior|above|earlier|system|your|original|initial) (?:\w+ )?{_ORDERS}\b",
        r"\b(?:ignore|disregard) (?:everything|anything|all|the) (?:above|before|said above|"
        r"written above|previously said)\b",
        rf"(?<!\w)(?:{_FA_ALL} )?{_FA_ORDERS} {_FA_EARLIER}(?: \w+){{0,3}}? (?:را )?{_FA_DROP}",
        rf"(?<!\w){_FA_ALL} {_FA_ORDERS}(?: \w+){{0,3}}? (?:را )?{_FA_DROP}",
        # An orders word right before a strong drop verb: "دستورات را نادیده بگیر".  Not
        # "cancel" or "set aside": "دستور را لغو کنید" is a customer cancelling an order.
        rf"(?<!\w){_FA_ORDERS} (?:را |رو )?{_FA_DROP_STRONG}",
        rf"(?<!\w){_FA_DROP}\w* (?:{_FA_ALL} )?{_FA_ORDERS} {_FA_EARLIER}(?!\w)",
        r"(?<!\w)(?:تجاهل|اهمل|انس)\w* (?:\w+ ){0,2}?(?:التعلیمات|الاوامر|التوجیهات)(?!\w)",
    ),
    InjectionSignal.ROLE_REASSIGNMENT: (
        rf"\b(?:you are|you re|youre) now (?:(?:a|an|the|my|your) (?:\w+ ){{0,3}}?{_ROLE}|"
        rf"{_ROLE}|free)\b",
        rf"\b(?:from now on|from this point on|from here on|henceforth|starting now|"
        rf"starting from now|as of now) (?:you are|you re|you will be|you ll be|you become|"
        rf"you will act as|you act as|you must act as|act as|be|become|pretend to be) "
        rf"(?:a |an |the |my |your )?(?:\w+ ){{0,3}}?{_ROLE}\b",
        rf"\bact as (?:a |an |the |my |your |if you are |if you were )?(?:\w+ ){{0,3}}?{_ROLE}\b",
        rf"\b(?:pretend|imagine) (?:to be|you are|you re|that you are|that you re) "
        rf"(?:a |an |the )?(?:\w+ ){{0,3}}?{_ROLE}\b",
        r"\b(?:enable|enter|activate|switch to|switch into|turn on|you are now in|you re now in) "
        r"(?:the )?(?:developer|dev|god|admin|jailbreak|dan|unrestricted|debug|sudo|root) mode\b",
        r"\b(?:roleplay|role play) as\b",
        r"\byour new (?:role|persona|identity) is\b",
        rf"(?<!\w){_FA_FROM_NOW} (?:تو|شما) (?:یک |یه )?{_FA_ROLE}(?: \w+){{0,3}}? {_FA_BE}",
        rf"(?<!\w)(?:تو|شما) (?:اکنون|الان|حالا|دیگه|دیگر|از این پس|ازین پس) (?:یک |یه )?"
        rf"{_FA_ROLE}(?: \w+){{0,3}}? {_FA_BE}",
        rf"(?<!\w)نقش (?:یک |یه )?{_FA_ROLE}(?: \w+){{0,2}}? (?:را )?(?:بازی|ایفا) ?کن",
        rf"(?<!\w)وانمود کن(?:ید)? (?:که )?(?:تو |شما )?(?:یک |یه )?{_FA_ROLE}",
        r"(?<!\w)(?:حالت|مود) (?:توسعه ?دهنده|ادمین|مدیر|نامحدود|بدون محدودیت|دیباگ|خدا)"
        r"(?: را)? (?:فعال|روشن) ?کن",
    ),
}

# --- markup rules, matched on the folded text (punctuation kept) ------------------------------

# Every quote mark a model may read as a JSON quote.
_QUOTE = "[\"'`«»‘’‚‛“”„‟‹›]"
# Every bracket that may read as the "<" of a fence marker.
_ANGLE = "[<«‹≪⟨⟪〈《]"

_TEXT_RULES = {
    InjectionSignal.IGNORE_INSTRUCTIONS: (
        r"\b(?:new|updated|real|actual|revised|secret|hidden) (?:system )?"
        r"(?:instructions?|prompt) ?:",
    ),
    InjectionSignal.ROLE_TAG: (
        r"<\|[a-z0-9_ ]{1,40}\|>",
        r"\[ ?/? ?inst ?\]",
        r"<< ?/? ?sys ?>>",
        r"< ?/? ?(?:system|assistant|developer|user|human|im_start|im_end|sys|system_prompt|"
        r"instructions?|start_of_turn|end_of_turn)(?: [^<>]{0,40})? ?>",
        r"#{1,6} ?(?:system|assistant|developer)(?: prompt| message)? ?:",
    ),
    InjectionSignal.TOOL_CALL: (
        rf"{_QUOTE} ?(?:tools?|tool_name|tool_use|tool_calls?|tool_input|tool_choice|functions?|"
        rf"function_calls?|invoke|arguments|parameters) ?{_QUOTE} ?[:=]",
        rf"\{{ ?{_QUOTE}? ?(?:tools?|tool_name|tool_use|tool_calls?|function_calls?|function) ?"
        rf"{_QUOTE}? ?:",
        r"< ?/? ?(?:[a-z_]+ ?: ?)?(?:tool_use|tool_calls?|tool_results?|tool_code|tool_output|"
        r"function_calls?|function_results?|invoke|parameter)\b",
        r"< ?/? ?tools? ?>",
        r"\[ ?/? ?(?:tool_calls?|tool_results?|available_tools) ?\]",
        r"\b(?:tool_calls?|tool_use|function_calls?)\b",
    ),
    InjectionSignal.FENCE_SPOOF: (
        rf"{_ANGLE}{{2,8}} ?(?:untrusted|end)\b",
        r"\buntrusted ?kind ?=",
        r"< ?/? ?untrusted\b",
        r"\bfence ?= ?[0-9a-f]{8,}",
    ),
}

# A line that starts with a role name and a colon, as chat transcripts mark a turn.
_ROLE_LINE = re.compile(
    r"^(?:(?:[#>*_]+ ?)?(?:system|assistant|developer)[*_]* ?:"
    r"|\[ ?(?:system|assistant|developer) ?\])"
)


def _compile(
    rules: dict[InjectionSignal, tuple[str, ...]],
) -> tuple[tuple[InjectionSignal, re.Pattern[str]], ...]:
    return tuple(
        (signal, re.compile(pattern)) for signal, patterns in rules.items() for pattern in patterns
    )


_ON_WORDS = _compile(_WORD_RULES)
_ON_TEXT = _compile(_TEXT_RULES)


def scan_injection(text: str) -> tuple[InjectionSignal, ...]:
    """The distinct injection signals in ``text``, sorted by value; ``()`` when none.

    The text is normalized first (``normalize_text``, then case folded and without
    combining marks).  A role tag counts only at the start of a line, so the lines are
    split before whitespace is collapsed.  ``ValueError`` for a non-str."""
    if not isinstance(text, str):
        raise ValueError("text must be a str")
    folded = _fold(text)
    words = _words(folded)
    found = {signal for signal, pattern in _ON_WORDS if pattern.search(words)}
    found.update(signal for signal, pattern in _ON_TEXT if pattern.search(folded))
    if any(_ROLE_LINE.match(_fold(line).strip()) for line in _LINE_BREAK.split(text)):
        found.add(InjectionSignal.ROLE_TAG)
    return tuple(sorted(found, key=lambda signal: signal.value))
