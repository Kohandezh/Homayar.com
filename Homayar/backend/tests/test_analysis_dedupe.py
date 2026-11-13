"""HOM-351 Q3: duplicate findings are merged once, before grounding and storage.

``merge_duplicate_findings`` is pure: it reads one ``IntelligenceResult`` and
returns a new one.  Action items merge when their text is equal after
``normalize_for_matching`` and their kind is the same; decisions and key moments
merge on text alone; extraction values merge on the key the taxonomy validator
uses for its duplicate check (entity: label and text, other fields: label).
Insights are never merged.  A merge keeps the first item at its position, the
union of the cited turns and the first owner and due date that are set.

HOM-351 review rulings: R1 the merge runs in ``persist_analysis``, the one place the
full job and the recompute share; R2 entity text is folded as the validator folds it
(Persian), whatever the call language; R3 actions with the same text but different
owners stay apart.
"""

from __future__ import annotations

import copy
import inspect
from datetime import date

import pytest
from sqlalchemy import select

from backend.database.saas_models import ActionItem as ActionItemRow
from backend.jobs import processor, recompute
from backend.providers.openai_compatible import SYSTEM_PROMPT
from backend.providers.schemas import (
    ActionItem,
    ClaimBasis,
    ClaimStatus,
    Decision,
    DecisionDueDate,
    DecisionOwner,
    IntelligenceInsight,
    IntelligenceResult,
    KeyMoment,
    SummaryField,
    SummaryFields,
)
from backend.services.analysis_dedupe import merge_duplicate_findings
from backend.tests._database import inspection_session_factory
from backend.tests.test_analysis_calendar import _recomputed_meeting
from backend.tests.test_analysis_recompute import _current_run
from backend.tests.test_hom133_action_grounding_oracle import (
    _Actions,
    _authenticated_workspace,
    _rows,
    _run,
    _spans,
    _upload_meeting,
)

NOT_STATED = SummaryField(status=ClaimStatus.NOT_STATED)
FIELDS = SummaryFields(purpose=NOT_STATED, resolution=NOT_STATED, next_steps=NOT_STATED)


def _result(**fields) -> IntelligenceResult:
    base = {"provider": "test", "model": "m", "summary": "خلاصه", "summary_fields": FIELDS}
    base.update(fields)
    return IntelligenceResult(**base)


def _action(description: str, *indexes: int, **fields) -> ActionItem:
    return ActionItem(
        description=description, source_turn_indexes=list(indexes), kind="commitment", **fields
    )


def _decision(description: str, *indexes: int, **fields) -> Decision:
    return Decision(
        description=description,
        basis=ClaimBasis.EXPLICIT,
        source_turn_indexes=list(indexes),
        **fields,
    )


def _moment(description: str, *indexes: int, kind: str = "commitment") -> KeyMoment:
    return KeyMoment(kind=kind, description=description, source_turn_indexes=list(indexes))


# --- action items ----------------------------------------------------------------


def test_an_extra_space_is_the_same_action_and_merges_with_the_union_of_turns():
    result = _result(action_items=[_action("تماس با مشتری", 3, 1), _action("تماس  با مشتری", 2, 1)])

    merged = merge_duplicate_findings(result, language="fa")

    (item,) = merged.action_items
    assert item.description == "تماس با مشتری"
    assert item.source_turn_indexes == [1, 2, 3]


def test_an_action_with_other_words_stays_a_second_action():
    result = _result(action_items=[_action("تماس با مشتری", 1), _action("تماس با مشتری فردا", 2)])

    merged = merge_duplicate_findings(result, language="fa")

    assert [item.description for item in merged.action_items] == [
        "تماس با مشتری",
        "تماس با مشتری فردا",
    ]


def test_equal_text_with_another_kind_stays_two_actions():
    result = _result(
        action_items=[
            _action("ارسال پیش‌فاکتور", 1),
            ActionItem(description="ارسال پیش‌فاکتور", kind="next_step", source_turn_indexes=[2]),
        ]
    )

    merged = merge_duplicate_findings(result, language="fa")

    assert [item.kind for item in merged.action_items] == ["commitment", "next_step"]


def test_punctuation_zwnj_and_letter_forms_do_not_make_a_new_action():
    # Arabic yeh and kaf, a ZWNJ dropped and a trailing period: the same words.
    result = _result(
        action_items=[
            _action("ارسال پیش‌فاکتور برای مشتری", 0),
            _action("ارسال پیشفاکتور براي مشتري.", 1),
        ]
    )

    (item,) = merge_duplicate_findings(result, language="fa").action_items
    assert item.source_turn_indexes == [0, 1]


def test_the_first_action_keeps_its_place_and_its_fields():
    result = _result(
        action_items=[
            _action("الف", 0, priority="high"),
            _action("ب", 1),
            _action("الف", 2, priority="low"),
            _action("ج", 3),
            _action("ب", 4),
        ]
    )

    merged = merge_duplicate_findings(result, language="fa")

    assert [(item.description, item.source_turn_indexes) for item in merged.action_items] == [
        ("الف", [0, 2]),
        ("ب", [1, 4]),
        ("ج", [3]),
    ]
    assert merged.action_items[0].priority.value == "high"


def test_a_merge_takes_the_first_owner_and_date_that_are_set_with_their_quotes():
    result = _result(
        action_items=[
            _action("ارسال قرارداد", 0),
            _action("ارسال قرارداد", 1, owner="خانم رضایی", owner_quote="خانم رضایی می‌فرسته"),
            _action("ارسال قرارداد", 2, due_date=date(2026, 11, 15), due_quote="فردا"),
            _action(
                "ارسال قرارداد",
                3,
                owner="خانم رضایی",
                owner_quote="خانم رضایی",
                due_date=date(2026, 11, 20),
                due_quote="جمعه",
            ),
        ]
    )

    (item,) = merge_duplicate_findings(result, language="fa").action_items

    assert (item.owner, item.owner_quote) == ("خانم رضایی", "خانم رضایی می‌فرسته")
    assert (item.due_date, item.due_quote) == (date(2026, 11, 15), "فردا")
    assert item.source_turn_indexes == [0, 1, 2, 3]


def test_an_owner_with_its_quote_wins_over_an_earlier_owner_without_one():
    # Grounding keeps an owner only with a quote, so the complete pair is kept.
    result = _result(
        action_items=[
            _action("ارسال قرارداد", 0, owner="خانم رضایی"),
            _action("ارسال قرارداد", 1, owner="خانم رضایی", owner_quote="خانم رضایی"),
            _action("ارسال قرارداد", 2, due_date=date(2026, 11, 15)),
            _action("ارسال قرارداد", 3, due_date=date(2026, 11, 16), due_quote="دوشنبه"),
        ]
    )

    (item,) = merge_duplicate_findings(result, language="fa").action_items

    assert (item.owner, item.owner_quote) == ("خانم رضایی", "خانم رضایی")
    assert (item.due_date, item.due_quote) == (date(2026, 11, 16), "دوشنبه")


def test_an_owner_without_any_quote_is_still_kept():
    result = _result(action_items=[_action("ارسال", 0), _action("ارسال", 1, owner="speaker_1")])

    (item,) = merge_duplicate_findings(result, language="fa").action_items
    assert (item.owner, item.owner_quote) == ("speaker_1", None)


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_r3_one_owner_and_no_owner_merge_and_keep_the_owner(order):
    items = [
        _action("ارسال قرارداد", 1, owner="خانم رضایی", owner_quote="خانم رضایی"),
        _action("ارسال قرارداد", 2),
    ]
    result = _result(action_items=[items[i] for i in order])

    (item,) = merge_duplicate_findings(result, language="fa").action_items

    assert (item.owner, item.owner_quote) == ("خانم رضایی", "خانم رضایی")
    assert item.source_turn_indexes == [1, 2]


def test_r3_different_owners_stay_two_actions_and_no_owner_is_lost():
    result = _result(
        action_items=[
            _action("ارسال قرارداد", 0, owner="خانم رضایی", owner_quote="خانم رضایی"),
            _action("ارسال قرارداد", 1, owner="آقای کریمی", owner_quote="آقای کریمی"),
        ]
    )

    merged = merge_duplicate_findings(result, language="fa")

    assert [(item.owner, item.source_turn_indexes) for item in merged.action_items] == [
        ("خانم رضایی", [0]),
        ("آقای کریمی", [1]),
    ]


def test_r3_an_action_without_owner_joins_the_first_owner_and_another_owner_stays_apart():
    result = _result(
        action_items=[
            _action("ارسال قرارداد", 0),
            _action("ارسال قرارداد", 1, owner="خانم رضایی", owner_quote="خانم رضایی"),
            _action("ارسال قرارداد", 2, owner="آقای کریمی", owner_quote="آقای کریمی"),
            _action("ارسال قرارداد", 3),
            _action("ارسال قرارداد", 4, owner="آقای کریمی"),
        ]
    )

    merged = merge_duplicate_findings(result, language="fa")

    assert [(item.owner, item.source_turn_indexes) for item in merged.action_items] == [
        ("خانم رضایی", [0, 1, 3]),
        ("آقای کریمی", [2, 4]),
    ]


def test_r3_an_owner_written_with_other_spacing_is_the_same_owner():
    result = _result(
        action_items=[
            _action("ارسال قرارداد", 0, owner="خانم رضایی"),
            _action("ارسال قرارداد", 1, owner="خانم  رضایی", owner_quote="خانم  رضایی"),
        ]
    )

    (item,) = merge_duplicate_findings(result, language="fa").action_items
    assert item.owner_quote == "خانم  رضایی"


# --- decisions and key moments ------------------------------------------------------


def test_equal_decisions_merge_and_keep_the_first_stated_owner_and_date():
    stated_owner = DecisionOwner(status=ClaimStatus.STATED, value="مدیر فروش")
    stated_due = DecisionDueDate(status=ClaimStatus.STATED, value=date(2026, 11, 20))
    result = _result(
        decisions=[
            _decision("تخفیف ده درصد تایید شد", 4),
            _decision("تخفیف ده درصد تایید شد.", 1, owner=stated_owner),
            _decision("تخفیف ده درصد تایید شد", 2, due_date=stated_due),
            _decision("قرارداد یک ساله شد", 5),
        ]
    )

    merged = merge_duplicate_findings(result, language="fa")

    first, second = merged.decisions
    assert first.description == "تخفیف ده درصد تایید شد"
    assert first.source_turn_indexes == [1, 2, 4]
    assert first.owner == stated_owner and first.due_date == stated_due
    assert second.description == "قرارداد یک ساله شد"


def test_key_moments_merge_on_text_whatever_their_kind():
    result = _result(
        key_moments=[
            _moment("مشتری قیمت را زیاد دانست", 1, kind="objection"),
            _moment("مشتری قیمت را زیاد دانست", 3, kind="complaint"),
            _moment("مشتری قیمت را قبول کرد", 4),
        ]
    )

    merged = merge_duplicate_findings(result, language="fa")

    assert [(m.kind, m.source_turn_indexes) for m in merged.key_moments] == [
        ("objection", [1, 3]),
        ("commitment", [4]),
    ]


def test_insights_are_never_merged():
    insight = IntelligenceInsight(category="risk", description="ریسک", source_turn_indexes=[0])
    result = _result(insights=[insight, insight.model_copy()])

    assert len(merge_duplicate_findings(result, language="fa").insights) == 2


# --- extraction --------------------------------------------------------------------


def _value(label: str, *indexes: int, **extra) -> dict:
    return {
        "label": label,
        "basis": "explicit",
        "source_turn_indexes": list(indexes),
        "confidence": 0.9,
        **extra,
    }


def _extraction(**fields) -> dict:
    empty = {"status": "not_stated", "values": []}
    return {name: fields.get(name, empty) for name in ("intent", "topic", "entity", "outcome")}


def test_extraction_values_with_one_label_merge_within_their_field():
    extraction = _extraction(
        topic={
            "status": "stated",
            "values": [
                _value("pricing", 2),
                _value("delivery", 1),
                _value("pricing", 0, confidence=0.5),
            ],
        },
        intent={"status": "stated", "values": [_value("pricing", 3)]},
    )

    merged = merge_duplicate_findings(_result(extraction=extraction), language="fa")

    assert merged.extraction["topic"]["values"] == [
        _value("pricing", 0, 2),
        _value("delivery", 1),
    ]
    # Another field is another key: the label is not merged across fields.
    assert merged.extraction["intent"]["values"] == [_value("pricing", 3)]


def test_two_entities_with_one_label_but_other_text_stay_two():
    extraction = _extraction(
        entity={
            "status": "stated",
            "values": [
                _value("person", 0, text="خانم رضایی"),
                _value("person", 1, text="آقای کریمی"),
                _value("person", 2, text="خانم  رضایی"),
            ],
        },
    )

    merged = merge_duplicate_findings(_result(extraction=extraction), language="fa")

    assert merged.extraction["entity"]["values"] == [
        _value("person", 0, 2, text="خانم رضایی"),
        _value("person", 1, text="آقای کریمی"),
    ]


def test_r2_entity_text_is_folded_as_the_validator_folds_it_whatever_the_language():
    from backend.analysis_taxonomy.validator import _fold

    # Arabic yeh and Persian yeh: one word for the validator's Persian fold, two for
    # the English normalizer.
    arabic, persian = "علي", "علی"
    assert _fold(arabic) == _fold(persian)
    extraction = _extraction(
        entity={
            "status": "stated",
            "values": [_value("person", 0, text=arabic), _value("person", 1, text=persian)],
        },
    )

    merged = merge_duplicate_findings(_result(extraction=extraction), language="en")

    assert merged.extraction["entity"]["values"] == [_value("person", 0, 1, text=arabic)]


def test_r2_other_free_text_keeps_the_call_language():
    # The same two spellings as action text in an English call stay two (C6).
    result = _result(action_items=[_action("علي", 0), _action("علی", 1)])
    assert len(merge_duplicate_findings(result, language="en").action_items) == 2
    assert len(merge_duplicate_findings(result, language="fa").action_items) == 1


@pytest.mark.parametrize(
    "extraction",
    [
        None,
        "not an object",
        [1, 2],
        {"topic": "x"},
        {"topic": {"status": "stated", "values": "x"}},
        {"topic": {"status": "stated", "values": [1, None, "pricing", {"basis": "explicit"}]}},
        {"topic": {"status": "stated", "values": [{"label": 3}, {"label": 3}]}},
        {
            "topic": {
                "status": "stated",
                "values": [
                    {"label": "pricing", "source_turn_indexes": "x"},
                    {"label": "pricing", "source_turn_indexes": [True, -1, 2]},
                ],
            }
        },
        {"entity": {"status": "stated", "values": [{"label": "person", "text": 5}]}},
    ],
)
def test_an_odd_extraction_never_raises_and_is_not_changed_where_it_is_odd(extraction):
    result = _result(extraction=extraction)
    before = copy.deepcopy(result.extraction)

    merged = merge_duplicate_findings(result, language="fa")

    assert result.extraction == before
    if not isinstance(extraction, dict):
        assert merged.extraction == extraction


def test_a_value_with_bad_turn_indexes_keeps_the_first_values_indexes():
    values = [
        {"label": "pricing", "source_turn_indexes": [1]},
        {"label": "pricing", "source_turn_indexes": "x"},
    ]
    result = _result(extraction={"topic": {"status": "stated", "values": values}})

    merged = merge_duplicate_findings(result, language="fa")

    assert merged.extraction["topic"]["values"] == [
        {"label": "pricing", "source_turn_indexes": [1]}
    ]


# --- purity and edges ---------------------------------------------------------------


def test_an_empty_result_stays_empty_and_equal():
    result = _result()
    merged = merge_duplicate_findings(result, language="fa")
    assert merged == result
    assert merged.action_items == merged.decisions == merged.key_moments == []


def test_a_legacy_result_without_summary_fields_is_accepted():
    result = IntelligenceResult(
        provider="legacy",
        model="m",
        summary="s",
        action_items=[_action("الف", 0), _action("الف", 1)],
    )
    (item,) = merge_duplicate_findings(result, language="fa").action_items
    assert item.source_turn_indexes == [0, 1]


def test_the_input_is_never_changed():
    extraction = _extraction(
        topic={"status": "stated", "values": [_value("pricing", 2), _value("pricing", 0)]}
    )
    result = _result(
        action_items=[_action("الف", 2), _action("الف", 1)],
        decisions=[_decision("ب", 1), _decision("ب", 0)],
        key_moments=[_moment("ج", 1), _moment("ج", 0)],
        extraction=extraction,
    )
    before = result.model_dump(mode="json")

    merge_duplicate_findings(result, language="fa")

    assert result.model_dump(mode="json") == before


@pytest.mark.parametrize("language", ["fa", "fa-IR", "en", "az", "unknown", "", "xx-YY"])
def test_any_language_works_and_an_unknown_one_reads_as_persian(language):
    result = _result(action_items=[_action("تماس با مشتری", 0), _action("تماس  با مشتری", 1)])
    (item,) = merge_duplicate_findings(result, language=language).action_items
    assert item.source_turn_indexes == [0, 1]


def test_text_that_normalizes_to_nothing_is_never_merged():
    result = _result(action_items=[_action("...", 0), _action("!!", 1), _action("...", 2)])
    assert len(merge_duplicate_findings(result, language="fa").action_items) == 3


def test_the_result_is_still_a_valid_result():
    result = _result(
        action_items=[_action("الف", 2), _action("الف", 1)],
        decisions=[_decision("ب", 1), _decision("ب", 0)],
    )
    merged = merge_duplicate_findings(result, language="fa")
    assert IntelligenceResult.model_validate(merged.model_dump()) == merged


# --- prompt and the one call site ---------------------------------------------------


def test_the_prompt_asks_for_each_item_once():
    assert "List each action item, decision, key moment and extraction value once" in SYSTEM_PROMPT


def test_r1_the_merge_has_one_call_site_in_persist_analysis():
    assert inspect.getsource(processor).count("merge_duplicate_findings(") == 1
    assert "merge_duplicate_findings(" in inspect.getsource(processor.persist_analysis)
    assert "merge_duplicate_findings" not in inspect.getsource(recompute)


# --- end to end: the processor stores one row for a duplicate -------------------------


def test_the_processor_stores_one_action_for_a_duplicate_with_the_union_of_its_turns(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    meeting_id, job_id = _upload_meeting(client, workspace_id, monkeypatch)

    def build(_request):
        return [
            ActionItem(description="پیگیری پیش‌فاکتور", kind="commitment", source_turn_indexes=[0]),
            ActionItem(description="پیگیری  پیش‌فاکتور", kind="commitment", source_turn_indexes=[1]),
            ActionItem(description="بررسی قرارداد", kind="commitment", source_turn_indexes=[2]),
        ]

    _run(job_id, settings, _Actions(build))

    rows = _rows(meeting_id)
    assert set(rows) == {"پیگیری پیش‌فاکتور", "بررسی قرارداد"}
    spans = _spans(meeting_id)
    own = [span for span in spans if span.finding_id == rows["پیگیری پیش‌فاکتور"].id]
    assert len(own) == 2


def test_r1_a_recompute_also_stores_one_action_for_a_duplicate(
    client, email_outbox, settings, monkeypatch
):
    def build(request):
        return [
            ActionItem(description="پیگیری فاکتور", kind="commitment", source_turn_indexes=[0]),
            ActionItem(description="پیگیری  فاکتور", kind="commitment", source_turn_indexes=[0]),
        ]

    intelligence = _Actions(build)
    meeting_id, _ = _recomputed_meeting(client, email_outbox, settings, monkeypatch, intelligence)

    run = _current_run(meeting_id)
    assert run.transcript_revision_id is not None and run.job_id is None  # the recompute's run
    with inspection_session_factory()() as db:
        ai = db.scalars(
            select(ActionItemRow).where(
                ActionItemRow.meeting_id == meeting_id, ActionItemRow.created_by == "ai"
            )
        ).all()
    assert [row.description for row in ai] == ["پیگیری فاکتور"]
    assert ai[0].analysis_run_id == run.id
