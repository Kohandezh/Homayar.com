"""Grounding rules G1-G5 for AI action items (HOM-133, ADR 0058).

Each rule has a deny case and the allow control next to it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from backend.providers.schemas import (
    UNKNOWN_SPEAKER_LABEL,
    ActionItem,
    ActionKind,
    TranscriptTurn,
)
from backend.services.action_grounding import (
    OWNER_EXPLICIT,
    VALUE_UNSET,
    GroundedAction,
    ground_action,
)

# 08:00 UTC is 11:30 in Tehran, so the local day is 2026-11-12.
REF = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
TOMORROW = date(2026, 11, 13)


def _turns(*texts: str, speakers: tuple[str, ...] = ()) -> list[TranscriptTurn]:
    return [
        TranscriptTurn(text=text, speaker_label=(speakers[i] if i < len(speakers) else "SPEAKER_0"))
        for i, text in enumerate(texts)
    ]


def _action(**fields) -> ActionItem:
    fields.setdefault("description", "ارسال گزارش")
    fields.setdefault("source_turn_indexes", [0])
    return ActionItem(**fields)


# --- G1: an item with no cited turn in range is dropped ---------------------


def test_g1_empty_indexes_drop_the_item() -> None:
    assert ground_action(_action(source_turn_indexes=[]), _turns("سلام"), reference=REF) is None


def test_g1_all_indexes_out_of_range_drop_the_item() -> None:
    action = _action(source_turn_indexes=[2, 7])
    assert ground_action(action, _turns("سلام", "خداحافظ"), reference=REF) is None


def test_g1_no_turns_drop_the_item() -> None:
    assert ground_action(_action(), [], reference=REF) is None


def test_g1_control_one_index_in_range_keeps_the_item() -> None:
    action = _action(source_turn_indexes=[9, 1])
    grounded = ground_action(action, _turns("سلام", "گزارش را می‌فرستم"), reference=REF)
    assert isinstance(grounded, GroundedAction)
    assert grounded.turn_indexes == (1,)


def test_turn_indexes_keep_first_seen_order_without_duplicates() -> None:
    action = _action(source_turn_indexes=[2, 0, 2, 5, 0, 1])
    grounded = ground_action(action, _turns("a", "b", "c"), reference=REF, language="en")
    assert grounded is not None
    assert grounded.turn_indexes == (2, 0, 1)


# --- G2: owner --------------------------------------------------------------

OWNER_TURN = "علی، لطفاً گزارش را تا فردا بفرست."


def test_g2_control_owner_quoted_in_cited_turn_is_kept() -> None:
    action = _action(owner="علی", owner_quote="علی، لطفاً گزارش را")
    grounded = ground_action(action, _turns(OWNER_TURN), reference=REF)
    assert grounded is not None
    assert grounded.owner == "علی"
    assert grounded.owner_status == OWNER_EXPLICIT


def test_g2_owner_not_in_any_cited_turn_is_unset() -> None:
    # Invented owner: the quote is real but it does not name "مریم".
    action = _action(owner="مریم", owner_quote="علی، لطفاً گزارش را")
    grounded = ground_action(action, _turns(OWNER_TURN), reference=REF)
    assert grounded is not None
    assert grounded.owner is None
    assert grounded.owner_status == VALUE_UNSET


def test_g2_quote_not_in_cited_turn_is_unset() -> None:
    action = _action(owner="علی", owner_quote="علی گفت انجام می‌دهد")
    grounded = ground_action(action, _turns(OWNER_TURN), reference=REF)
    assert grounded is not None and grounded.owner is None


def test_g2_owner_quoted_only_in_uncited_turn_is_unset() -> None:
    turns = _turns("گزارش باید ارسال شود.", OWNER_TURN)
    action = _action(owner="علی", owner_quote="علی، لطفاً", source_turn_indexes=[0])
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner is None


def test_g2_control_that_turn_cited_keeps_the_owner() -> None:
    turns = _turns("گزارش باید ارسال شود.", OWNER_TURN)
    action = _action(owner="علی", owner_quote="علی، لطفاً", source_turn_indexes=[0, 1])
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner == "علی"


@pytest.mark.parametrize("quote", [None, "", "   ", "،؛"])
def test_g2_missing_or_empty_quote_leaves_owner_unset(quote) -> None:
    action = _action(owner="علی", owner_quote=quote)
    grounded = ground_action(action, _turns(OWNER_TURN), reference=REF)
    assert grounded is not None and grounded.owner is None


@pytest.mark.parametrize("owner", [None, ""])
def test_g2_blank_owner_is_unset(owner) -> None:
    action = _action(owner=owner, owner_quote="علی، لطفاً")
    grounded = ground_action(action, _turns(OWNER_TURN), reference=REF)
    assert grounded is not None and grounded.owner is None
    assert grounded.owner_status == VALUE_UNSET


def test_g2_owner_kept_exactly_as_given() -> None:
    turns = _turns("Please, John Smith will send the deck.")
    action = _action(owner="John Smith", owner_quote="john smith will send")
    grounded = ground_action(action, turns, reference=REF, language="en")
    assert grounded is not None and grounded.owner == "John Smith"


def test_g2_quote_matches_across_zwnj_and_punctuation() -> None:
    turns = _turns("سارا: من فاکتور را می‌فرستم.")
    action = _action(owner="سارا", owner_quote="سارا من فاکتور را میفرستم")
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner == "سارا"


# G2a: the speaker-label path works only for a commitment.


def test_g2a_control_commitment_speaker_label_owner_is_kept() -> None:
    turns = _turns("من گزارش را می‌فرستم.", speakers=("SPEAKER_1",))
    action = _action(
        kind=ActionKind.COMMITMENT, owner="SPEAKER_1", owner_quote="من گزارش را می‌فرستم"
    )
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner == "SPEAKER_1"
    assert grounded.owner_status == OWNER_EXPLICIT


def test_g2a_next_step_speaker_label_owner_is_unset() -> None:
    turns = _turns("بهتر است گزارش را بفرستیم.", speakers=("SPEAKER_1",))
    action = _action(
        kind=ActionKind.NEXT_STEP, owner="SPEAKER_1", owner_quote="بهتر است گزارش را بفرستیم"
    )
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner is None


def test_g2a_speaker_label_must_be_the_quoted_turns_speaker() -> None:
    turns = _turns("من گزارش را می‌فرستم.", "باشه.", speakers=("SPEAKER_1", "SPEAKER_0"))
    action = _action(
        kind=ActionKind.COMMITMENT,
        owner="SPEAKER_0",
        owner_quote="من گزارش را می‌فرستم",
        source_turn_indexes=[0, 1],
    )
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner is None


# G2c: the speaker-label path never grounds on the unknown speaker label.


def test_g2c_commitment_with_unknown_speaker_label_owner_is_unset() -> None:
    turns = _turns("من گزارش را می‌فرستم.", speakers=(UNKNOWN_SPEAKER_LABEL,))
    action = _action(
        kind=ActionKind.COMMITMENT,
        owner=UNKNOWN_SPEAKER_LABEL,
        owner_quote="من گزارش را می‌فرستم",
    )
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None
    assert grounded.owner is None
    assert grounded.owner_status == VALUE_UNSET


def test_g2c_control_real_speaker_label_commitment_is_kept() -> None:
    turns = _turns("من گزارش را می‌فرستم.", speakers=("SPEAKER_1",))
    action = _action(
        kind=ActionKind.COMMITMENT, owner="SPEAKER_1", owner_quote="من گزارش را می‌فرستم"
    )
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner == "SPEAKER_1"
    assert grounded.owner_status == OWNER_EXPLICIT


def test_g2c_control_unknown_speaker_turn_naming_the_owner_is_kept() -> None:
    turns = _turns("علی، لطفاً گزارش را بفرست.", speakers=(UNKNOWN_SPEAKER_LABEL,))
    action = _action(kind=ActionKind.COMMITMENT, owner="علی", owner_quote="علی، لطفاً")
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner == "علی"
    assert grounded.owner_status == OWNER_EXPLICIT


def test_g2a_next_step_name_path_still_grounds() -> None:
    action = _action(kind=ActionKind.NEXT_STEP, owner="علی", owner_quote="علی، لطفاً")
    grounded = ground_action(action, _turns(OWNER_TURN), reference=REF)
    assert grounded is not None and grounded.owner == "علی"


# G2b: the name path matches whole words.


def test_g2b_name_inside_a_longer_name_is_unset() -> None:
    turns = _turns("علیرضا گزارش را می‌فرستد.")
    action = _action(owner="علی", owner_quote="علیرضا گزارش را")
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner is None


def test_g2b_control_full_name_is_kept() -> None:
    turns = _turns("علیرضا گزارش را می‌فرستد.")
    action = _action(owner="علیرضا", owner_quote="علیرضا گزارش را")
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.owner == "علیرضا"


def test_g2b_english_partial_word_is_unset() -> None:
    turns = _turns("Johnson will send the deck.")
    action = _action(owner="John", owner_quote="Johnson will send")
    grounded = ground_action(action, turns, reference=REF, language="en")
    assert grounded is not None and grounded.owner is None


# --- G3: due date -----------------------------------------------------------

DUE_TURN = "علی، لطفاً گزارش را تا فردا بفرست."


def test_g3_control_quote_parses_to_the_same_date_is_kept() -> None:
    action = _action(due_date=TOMORROW, due_quote="تا فردا")
    grounded = ground_action(action, _turns(DUE_TURN), reference=REF)
    assert grounded is not None
    assert grounded.due_date == TOMORROW
    assert grounded.due_status == OWNER_EXPLICIT


def test_g3_quote_parses_to_another_date_is_unset() -> None:
    action = _action(due_date=date(2026, 11, 20), due_quote="تا فردا")
    grounded = ground_action(action, _turns(DUE_TURN), reference=REF)
    assert grounded is not None
    assert grounded.due_date is None
    assert grounded.due_status == VALUE_UNSET


def test_g3_relative_quote_with_wrong_reference_day_is_unset() -> None:
    action = _action(due_date=TOMORROW, due_quote="تا فردا")
    grounded = ground_action(action, _turns(DUE_TURN), reference=REF + timedelta(days=1))
    assert grounded is not None and grounded.due_date is None


def test_g3_relative_quote_with_right_reference_day_is_kept() -> None:
    action = _action(due_date=TOMORROW + timedelta(days=1), due_quote="تا فردا")
    grounded = ground_action(action, _turns(DUE_TURN), reference=REF + timedelta(days=1))
    assert grounded is not None and grounded.due_date == TOMORROW + timedelta(days=1)


def test_g3_invented_date_with_no_quote_is_unset() -> None:
    action = _action(due_date=TOMORROW, due_quote=None)
    grounded = ground_action(action, _turns("گزارش را بفرست."), reference=REF)
    assert grounded is not None and grounded.due_date is None


def test_g3_quote_not_in_cited_turn_is_unset() -> None:
    turns = _turns("گزارش را بفرست.", "تا فردا وقت داری.")
    action = _action(due_date=TOMORROW, due_quote="تا فردا", source_turn_indexes=[0])
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.due_date is None


def test_g3_control_quote_in_cited_turn_is_kept() -> None:
    turns = _turns("گزارش را بفرست.", "تا فردا وقت داری.")
    action = _action(due_date=TOMORROW, due_quote="تا فردا", source_turn_indexes=[0, 1])
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.due_date == TOMORROW


@pytest.mark.parametrize(
    ("text", "quote"),
    [
        ("هفته بعد گزارش را بفرست.", "هفته بعد"),  # date_range
        ("ساعت ۵ عصر گزارش را بفرست.", "ساعت ۵ عصر"),  # time only
        ("تا شنبه گزارش را بفرست.", "تا شنبه"),  # ambiguous weekday
        ("تا ۲۵ آبان گزارش را بفرست.", "تا ۲۵ آبان"),  # ambiguous year
    ],
)
def test_g3_range_time_or_ambiguous_quote_is_unset(text: str, quote: str) -> None:
    action = _action(due_date=TOMORROW, due_quote=quote)
    grounded = ground_action(action, _turns(text), reference=REF)
    assert grounded is not None and grounded.due_date is None


def test_g3b_two_dates_in_the_quote_are_unset() -> None:
    turns = _turns("گزارش را فردا یا پس‌فردا بفرست.")
    action = _action(due_date=TOMORROW, due_quote="فردا یا پس‌فردا")
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.due_date is None


def test_g3b_two_dates_for_the_same_day_are_unset() -> None:
    # Pinned reading: two date matches count as two, even when both are 2026-11-13.
    turns = _turns("گزارش را فردا، یعنی ۱۴۰۵/۰۸/۲۲ بفرست.")
    action = _action(due_date=TOMORROW, due_quote="فردا، یعنی ۱۴۰۵/۰۸/۲۲")
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.due_date is None


def test_g3b_control_range_next_to_one_date_is_kept() -> None:
    # Pinned reading: a date_range next to exactly one date match does not count.
    turns = _turns("هفته بعد، یعنی شنبه آینده گزارش را بفرست.")
    action = _action(due_date=date(2026, 11, 14), due_quote="هفته بعد، یعنی شنبه آینده")
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.due_date == date(2026, 11, 14)


def test_g3b_control_one_date_plus_a_time_is_kept() -> None:
    turns = _turns("گزارش را فردا ساعت ۱۰ بفرست.")
    action = _action(due_date=TOMORROW, due_quote="فردا ساعت ۱۰")
    grounded = ground_action(action, turns, reference=REF)
    assert grounded is not None and grounded.due_date == TOMORROW


def test_g3_iso_date_in_english_is_kept() -> None:
    turns = _turns("Please send the deck by 2026-11-20.")
    action = _action(due_date=date(2026, 11, 20), due_quote="by 2026-11-20")
    grounded = ground_action(action, turns, reference=REF, language="en")
    assert grounded is not None and grounded.due_date == date(2026, 11, 20)


def test_g3_english_relative_date_is_unset() -> None:
    # extract_temporal reads Persian and ISO only (ADR 0058, "Not built").
    turns = _turns("Please send the deck by tomorrow.")
    action = _action(due_date=TOMORROW, due_quote="by tomorrow")
    grounded = ground_action(action, turns, reference=REF, language="en")
    assert grounded is not None and grounded.due_date is None


# --- G4: kind ---------------------------------------------------------------


@pytest.mark.parametrize("kind", [ActionKind.COMMITMENT, ActionKind.NEXT_STEP])
def test_g4_kind_is_the_providers_label(kind: ActionKind) -> None:
    grounded = ground_action(_action(kind=kind), _turns("سلام"), reference=REF)
    assert grounded is not None and grounded.kind == kind.value


def test_g4_unlabelled_item_is_a_next_step() -> None:
    grounded = ground_action(_action(), _turns("سلام"), reference=REF)
    assert grounded is not None and grounded.kind == "next_step"


def test_g4_owner_and_due_do_not_depend_on_kind() -> None:
    for kind in (ActionKind.COMMITMENT, ActionKind.NEXT_STEP):
        action = _action(
            kind=kind,
            owner="علی",
            owner_quote="علی، لطفاً",
            due_date=TOMORROW,
            due_quote="تا فردا",
        )
        grounded = ground_action(action, _turns(OWNER_TURN), reference=REF)
        assert grounded is not None
        assert (grounded.owner, grounded.due_date) == ("علی", TOMORROW)


# --- G5: pure and never raises ----------------------------------------------


def test_g5_unknown_language_and_naive_reference_do_not_raise() -> None:
    action = _action(owner="علی", owner_quote="علی، لطفاً", due_date=TOMORROW, due_quote="تا فردا")
    grounded = ground_action(
        action, _turns(OWNER_TURN), reference=datetime(2026, 11, 12, 8, 0), language="xx-YY"
    )
    assert grounded is not None
    assert (grounded.owner, grounded.due_date) == ("علی", TOMORROW)


def test_g5_same_input_gives_the_same_output() -> None:
    action = _action(owner="علی", owner_quote="علی، لطفاً", due_date=TOMORROW, due_quote="تا فردا")
    turns = _turns(OWNER_TURN)
    assert ground_action(action, turns, reference=REF) == ground_action(
        action, turns, reference=REF
    )
