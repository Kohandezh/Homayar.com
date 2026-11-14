"""HOM-98 (SPEC 2.4): the Copilot question-set format, the scoring rules, the report,
the f58 block and the CLI, with Amendments 2 (A2.1, A2.2, A2.6, A2.7, A2.8) and
Amendment 5 (A5.5 to A5.8, A5.12).

Pure unit tests: no database, no fixtures of the backend world.  Every defect of
the format is a ``ValueError``; every scoring rule is checked against a fixed
``ObservedAnswer``; the report is computed by hand; the CLI is run against
files in ``tmp_path`` (and once as a real subprocess, A5.6).
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from backend.evaluation import copilot as module
from backend.evaluation.copilot import (
    QUESTION_SET_FORMAT,
    REPORT_VERSION,
    CopilotEvaluationReport,
    ObservedAnswer,
    Question,
    QuestionKind,
    QuestionScore,
    parse_question_set,
    run_question_set,
    score_question,
)

# --- the format ------------------------------------------------------------------------------


def raw_set(**overrides) -> bytes:
    """One valid minimal set, as bytes."""
    document = {
        "format": "copilot-questions/1",
        "set_id": "synthetic-placeholder-v1",
        "approved": False,
        "notice": "SYNTHETIC PLACEHOLDER. NOT the approved 100 business questions.",
        "questions": [
            {
                "id": "c01",
                "question": "چند گفتگو؟",
                "caller": "a_owner",
                "kind": "count",
                "expected_intent": "issue_count",
                "expected": {"values": ["3"]},
                "slices": {"lang": "fa"},
            }
        ],
    }
    document.update(overrides)
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def question_of(kind: QuestionKind = QuestionKind.COUNT, **overrides) -> Question:
    base = {
        "id": "q1",
        "question": "one question",
        "caller": "a_owner",
        "kind": kind,
        "expected_intent": "issue_count",
        "expected": {"values": ["3"]},
        "slices": {},
    }
    expected = overrides.pop("expected", None)
    base.update(overrides)
    if expected is not None:
        base["expected"] = expected
    return parse_question_set(raw_set(questions=[base])).questions[0]


def observed(**overrides) -> ObservedAnswer:
    base = {
        "status": "answered",
        "reason": None,
        "intent_id": "issue_count",
        "text": "The count is 3.",
        "cited_conversation_ids": ("m1",),
        "fact_values": (Decimal("3"),),
    }
    base.update(overrides)
    return ObservedAnswer(**base)


def test_a_valid_set_parses_with_its_digest():
    raw = raw_set()
    made = parse_question_set(raw)
    import hashlib

    assert made.format == QUESTION_SET_FORMAT
    assert made.set_id == "synthetic-placeholder-v1"
    assert made.approved is False
    assert made.notice.startswith("SYNTHETIC PLACEHOLDER")
    assert made.sha256 == hashlib.sha256(raw).hexdigest()
    (question,) = made.questions
    assert question.kind is QuestionKind.COUNT
    assert question.expected_intent == "issue_count"
    assert question.slices == {"lang": "fa"}
    with pytest.raises(TypeError):
        question.expected["values"] = []  # type: ignore[index]
    with pytest.raises(TypeError):
        question.slices["lang"] = "en"  # type: ignore[index]


def mutate(**changes) -> bytes:
    document = json.loads(raw_set())
    for path, value in changes.items():
        node = document
        *parents, last = path.split(".")
        for parent in parents:
            node = node[int(parent)] if isinstance(node, list) else node[parent]
        if value is ...:
            if isinstance(node, list):
                del node[int(last)]
            else:
                del node[last]
        elif isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"\xff\xfe{}", id="not-utf8"),
        pytest.param(b"{", id="not-json"),
        pytest.param(b"[]", id="not-an-object"),
    ],
)
def test_a_defective_document_is_a_value_error(raw):
    with pytest.raises(ValueError):
        parse_question_set(raw)


@pytest.mark.parametrize(
    ("name", "changes"),
    [
        pytest.param("unknown-top-key", {"extra": 1}, id="unknown-top-key"),
        pytest.param("missing-top-key", {"format": ...}, id="missing-top-key"),
        pytest.param("wrong-format", {"format": "copilot-questions/2"}, id="wrong-format"),
        pytest.param("bad-set-id", {"set_id": "Bad ID"}, id="bad-set-id"),
        pytest.param("set-id-newline", {"set_id": "good\n"}, id="set-id-newline"),
        pytest.param("approved-int", {"approved": 1}, id="approved-int"),
        pytest.param("empty-notice", {"notice": ""}, id="empty-notice"),
        pytest.param("notice-not-str", {"notice": 5}, id="notice-not-str"),
        pytest.param("questions-not-list", {"questions": {}}, id="questions-not-list"),
        pytest.param("question-not-object", {"questions": ["nope"]}, id="question-not-object"),
        pytest.param("question-unknown-key", {"questions.0.tool": 1}, id="question-unknown-key"),
        pytest.param("question-missing-key", {"questions.0.caller": ...}, id="missing-key"),
        pytest.param("bad-id", {"questions.0.id": "Bad"}, id="bad-id"),
        pytest.param("long-id", {"questions.0.id": "a" * 65}, id="long-id"),
        pytest.param("id-newline", {"questions.0.id": "good\n"}, id="id-newline"),
        pytest.param("empty-question", {"questions.0.question": ""}, id="empty-question"),
        pytest.param("long-question", {"questions.0.question": "x" * 2001}, id="long-question"),
        pytest.param("question-not-str", {"questions.0.question": 7}, id="question-not-str"),
        pytest.param("empty-caller", {"questions.0.caller": ""}, id="empty-caller"),
        pytest.param("unknown-kind", {"questions.0.kind": "guess"}, id="unknown-kind"),
        pytest.param("kind-not-str", {"questions.0.kind": 3}, id="kind-not-str"),
        pytest.param(
            "expected-intent-not-str", {"questions.0.expected_intent": 3}, id="expected-intent"
        ),
        pytest.param(
            "expected-intent-null", {"questions.0.expected_intent": None}, id="expected-intent-null"
        ),
        pytest.param(
            "answer-two-empty-lists",
            {
                "questions.0.kind": "answer",
                "questions.0.expected": {"cited_conversations": [], "must_include": []},
            },
            id="answer-two-empty-lists",
        ),
        pytest.param("slices-not-str", {"questions.0.slices": {"lang": 1}}, id="slices-not-str"),
        pytest.param(
            "count-extra-key",
            {"questions.0.expected": {"values": ["3"], "why": 1}},
            id="count-extra-key",
        ),
        pytest.param("count-missing-values", {"questions.0.expected": {}}, id="count-missing"),
        pytest.param(
            "count-values-not-list", {"questions.0.expected": {"values": "3"}}, id="count-not-list"
        ),
        pytest.param(
            "count-values-empty", {"questions.0.expected": {"values": []}}, id="count-empty-list"
        ),
        pytest.param(
            "count-value-not-str", {"questions.0.expected": {"values": [3]}}, id="count-value-int"
        ),
        pytest.param(
            "count-value-not-decimal",
            {"questions.0.expected": {"values": ["3,5"]}},
            id="count-value-comma",
        ),
        pytest.param(
            "count-value-exponent", {"questions.0.expected": {"values": ["1e3"]}}, id="count-e3"
        ),
        pytest.param(
            "count-value-eastern-digit",
            {"questions.0.expected": {"values": ["۳"]}},
            id="count-persian-digit",
        ),
    ],
)
def test_every_format_defect_is_a_value_error(name, changes):
    with pytest.raises(ValueError):
        parse_question_set(mutate(**changes))


def test_a_duplicate_question_id_is_a_defect():
    document = json.loads(raw_set())
    document["questions"].append(document["questions"][0])
    with pytest.raises(ValueError):
        parse_question_set(json.dumps(document).encode())


def test_a_too_deeply_nested_document_is_a_value_error():
    """A5.7: ``RecursionError`` from ``json.loads`` is the same ``ValueError``
    as bad JSON, never an unhandled crash."""
    with pytest.raises(ValueError):
        parse_question_set(b"[" * 20000 + b"]" * 20000)


def test_a_repeated_json_key_is_a_defect_at_any_level():
    """A5.8: a frozen set is exact, so a repeated key is a defect at the top
    level and inside a question."""
    with pytest.raises(ValueError):
        parse_question_set(b'{"format": "a", "format": "b"}')
    with pytest.raises(ValueError):
        parse_question_set(
            b'{"format": "copilot-questions/1", "set_id": "s1", "approved": false,'
            b' "notice": "n", "questions": [{"id": "c1", "id": "c2",'
            b' "question": "?", "caller": "a", "kind": "count",'
            b' "expected": {"values": ["1"]}}]}'
        )


def test_a_lone_surrogate_in_the_question_text_is_a_defect():
    """A5.12: ``validate`` catches what ``answer_question`` refuses at step 0
    (A4.6), so a lone surrogate never enters a frozen set."""
    document = json.loads(raw_set())
    document["questions"][0]["question"] = "x\ud800"
    raw = json.dumps(document).encode()  # ensure_ascii escapes the surrogate
    assert b"\\ud800" in raw
    with pytest.raises(ValueError):
        parse_question_set(raw)


def test_the_allow_controls_of_the_format():
    # every optional shape that must load
    made = parse_question_set(
        raw_set(
            approved=True,
            questions=[
                {
                    "id": "a" * 64,
                    "question": "x" * 2000,
                    "caller": "someone",
                    "kind": "answer",
                    "expected": {"must_include": ["سلام"]},
                },
                {
                    "id": "u1",
                    "question": "why?",
                    "caller": "someone",
                    "kind": "unsupported",
                    "expected": {},
                },
                {
                    "id": "u2",
                    "question": "when?",
                    "caller": "someone",
                    "kind": "unsupported",
                    "expected": {"reason": "forecast_not_supported"},
                },
            ],
        )
    )
    assert made.approved is True
    assert [question.kind for question in made.questions] == [
        QuestionKind.ANSWER,
        QuestionKind.UNSUPPORTED,
        QuestionKind.UNSUPPORTED,
    ]


# --- the scoring rules -----------------------------------------------------------------------


def test_a_correct_count():
    score = score_question(question_of(), observed())
    assert score.correct and score.failure is None
    assert score.intent_match is True  # issue_count == issue_count
    assert score.status == "answered" and score.reason is None


def test_a_count_must_be_answered():
    score = score_question(question_of(), observed(status="abstained", reason="no_evidence"))
    assert not score.correct and score.failure == "not_answered"
    assert score.status == "abstained" and score.reason == "no_evidence"


def test_a_count_value_must_be_in_the_facts():
    score = score_question(question_of(), observed(fact_values=(Decimal("4"),), text="4"))
    assert score.failure == "count_not_in_facts"


def test_a_count_value_must_be_in_the_text():
    score = score_question(question_of(), observed(text="The count is 4."))
    assert score.failure == "count_not_in_text"


@pytest.mark.parametrize(
    ("value", "text", "match"),
    [
        pytest.param("3", "There were 3.", True, id="sentence-period"),
        pytest.param("3", "3.5 items", False, id="decimal-tail"),
        pytest.param("3.5", "3.5 items", True, id="decimal-value"),
        pytest.param("3.5", "35 items", False, id="no-point"),
        pytest.param("1003", "1,003 items", True, id="ascii-group"),
        pytest.param("1003", "۱٬۰۰۳ items", True, id="persian-group"),
        pytest.param("3", "1,003 items", False, id="inside-group"),
        pytest.param("3", "عدد ۳ بود", True, id="persian-digit"),
        pytest.param("3", "عدد ۳٫۵ بود", False, id="persian-decimal"),
        pytest.param("3", "23 items", False, id="touching-digit"),
        pytest.param("3", "2.3 items", False, id="fraction-tail"),
        pytest.param("3", "a3b", True, id="letters-fine"),
        pytest.param("-3", "drop of 3", True, id="sign-read-by-value"),
        pytest.param("3", "03 items", False, id="leading-zero"),
    ],
)
def test_the_whole_number_text_match(value, text, match):
    question = question_of(expected={"values": [value]})
    score = score_question(question, observed(text=text, fact_values=(Decimal(value),)))
    expected_failure = None if match else "count_not_in_text"
    assert score.failure == expected_failure


def answer_question(**overrides) -> Question:
    base = {
        "id": "a1",
        "question": "what was said?",
        "caller": "a_owner",
        "kind": QuestionKind.ANSWER,
        "expected_intent": None,
        "expected": {"cited_conversations": ["first"], "must_include": ["سلام"]},
        "slices": {},
    }
    base.update(overrides)
    return Question(**base)


def test_a_correct_answer():
    score = score_question(
        answer_question(),
        observed(text="گفتگو «سلام دنیا» بود", intent_id="conversation_lookup"),
        conversation_ids={"first": "m1"},
    )
    assert score.correct and score.failure is None


def test_an_answer_must_be_answered():
    score = score_question(
        answer_question(),
        observed(status="abstained", reason="no_evidence", text="", fact_values=()),
        conversation_ids={"first": "m1"},
    )
    assert score.failure == "not_answered"


def test_a_missing_citation_fails():
    score = score_question(
        answer_question(),
        observed(cited_conversation_ids=("m2",)),
        conversation_ids={"first": "m1"},
    )
    assert score.failure == "citation_missing"


def test_a_missing_text_fails_after_normalization():
    score = score_question(
        answer_question(expected={"must_include": ["SALAM"]}),
        observed(text="nothing here"),
        conversation_ids={"first": "m1"},
    )
    assert score.failure == "text_missing"


def test_an_unknown_alias_is_a_value_error_whatever_the_status():
    for answer in (
        observed(),
        observed(status="refused", reason="permission_denied", text="", fact_values=()),
    ):
        with pytest.raises(ValueError):
            score_question(answer_question(), answer)  # no mapping at all


def unsupported_question(**overrides) -> Question:
    base = {
        "id": "u1",
        "question": "next month?",
        "caller": "a_owner",
        "kind": QuestionKind.UNSUPPORTED,
        "expected_intent": "forecast",
        "expected": {"reason": "forecast_not_supported"},
        "slices": {},
    }
    base.update(overrides)
    return Question(**base)


@pytest.mark.parametrize(
    ("status", "reason", "failure"),
    [
        pytest.param("refused", "forecast_not_supported", None, id="refused"),
        pytest.param("abstained", "forecast_not_supported", None, id="abstained"),
        pytest.param("refused", "out_of_scope", "wrong_reason", id="wrong-reason"),
        pytest.param("answered", None, "answered", id="answered"),
    ],
)
def test_unsupported_scoring(status, reason, failure):
    answer = observed(status=status, reason=reason, text="", fact_values=())
    score = score_question(unsupported_question(), answer)
    assert score.failure == failure
    assert score.correct is (failure is None)


def test_intent_match_is_reported_and_never_part_of_correct():
    score = score_question(
        unsupported_question(expected_intent="conversation_lookup"),
        observed(status="refused", reason="forecast_not_supported", intent_id="forecast"),
    )
    assert score.correct and score.intent_match is False


# --- the report and f58 ----------------------------------------------------------------------


def score(question_id, kind, *, correct=True, status="answered") -> QuestionScore:
    return QuestionScore(
        question_id=question_id,
        kind=kind,
        correct=correct,
        status=status,
        reason=None if correct else "no_evidence",
        failure=None if correct else "not_answered",
        intent_match=None,
    )


def report_of(scores) -> dict:
    return CopilotEvaluationReport(
        format=QUESTION_SET_FORMAT,
        set_id="synthetic-placeholder-v1",
        approved=True,
        notice="notice",
        question_set_sha256="0" * 64,
        scores=tuple(scores),
    ).to_json()


def test_the_report_is_computed_by_hand():
    scores = [
        score("c1", QuestionKind.COUNT),
        score("c2", QuestionKind.COUNT, correct=False, status="abstained"),
        score("a1", QuestionKind.ANSWER, status="answered"),
        score("u1", QuestionKind.UNSUPPORTED, status="refused"),
        score("u2", QuestionKind.UNSUPPORTED, correct=False, status="answered"),
    ]
    report = report_of(scores)
    assert report["version"] == REPORT_VERSION
    assert report["total"] == 5 and report["correct"] == 3
    assert report["overall_accuracy"] == 0.6
    assert report["count"] == {"total": 2, "correct": 1, "exact_count_accuracy": 0.5}
    assert report["answer"] == {"total": 1, "correct": 1, "answer_accuracy": 1.0}
    assert report["unsupported"] == {
        "total": 2,
        "abstained": 1,
        "correct": 1,
        "abstention_rate": 0.5,
    }
    assert report["supported_abstained"] == 1
    assert report["false_abstention_rate"] == 1 / 3  # A2.1: over count.total + answer.total
    assert [entry["id"] for entry in report["questions"]] == ["a1", "c1", "c2", "u1", "u2"]


def test_an_empty_set_reports_null_ratios():
    report = report_of([])
    assert report["overall_accuracy"] is None
    assert report["count"]["exact_count_accuracy"] is None
    assert report["unsupported"]["abstention_rate"] is None  # A2.2
    assert report["false_abstention_rate"] is None  # A2.1
    assert report["f58"] == {
        "applicable": False,
        "passed": None,
        "checks": {
            "correct_at_least_95": False,
            "exact_count_accuracy": False,
            "abstention_rate": False,
        },
    }


def _hundred(correct_counts: int, *, one_count_abstains: bool = False) -> list[QuestionScore]:
    """100 approved questions: ``correct_counts`` exact counts, the rest
    unsupported questions that abstain (all correct).  With
    ``one_count_abstains`` the last count question is wrong instead."""
    scores = [
        score(f"c{i}", QuestionKind.COUNT, status="answered") for i in range(correct_counts)
    ]
    if one_count_abstains:
        scores.append(score("c-x", QuestionKind.COUNT, correct=False, status="abstained"))
    scores += [
        score(f"u{i}", QuestionKind.UNSUPPORTED, status="refused")
        for i in range(100 - len(scores))
    ]
    return scores


@pytest.mark.parametrize(
    ("scores", "passed"),
    [
        pytest.param(_hundred(95), True, id="ninety-five"),
        pytest.param(_hundred(94, one_count_abstains=True), False, id="one-count-lost"),
    ],
)
def test_f58_passes_only_with_95_of_100(scores, passed):
    assert len(scores) == 100
    report = report_of(scores)
    assert report["f58"]["applicable"] is True
    assert report["f58"]["passed"] is passed
    assert report["f58"]["checks"]["correct_at_least_95"] is True  # 95, 99 and 100 all pass it
    assert report["f58"]["checks"]["exact_count_accuracy"] is passed  # one lost count fails it
    assert report["f58"]["checks"]["abstention_rate"] is True


def _hundred_with(correct: int) -> list[QuestionScore]:
    """100 approved questions with exactly ``correct`` of them correct.  The
    wrong ones are answered answer questions, so every count stays exact and
    every unsupported question still abstains (A5.5)."""
    scores = [score(f"c{i}", QuestionKind.COUNT, status="answered") for i in range(90)]
    scores += [
        score(f"a{i}", QuestionKind.ANSWER, correct=False, status="answered")
        for i in range(100 - correct)
    ]
    scores += [
        score(f"u{i}", QuestionKind.UNSUPPORTED, status="refused")
        for i in range(100 - len(scores))
    ]
    return scores


@pytest.mark.parametrize(
    ("correct", "passed"),
    [
        pytest.param(95, True, id="exactly-ninety-five"),
        pytest.param(94, False, id="exactly-ninety-four"),
    ],
)
def test_f58_passes_at_exactly_95_and_fails_at_94(correct, passed):
    scores = _hundred_with(correct)
    assert len(scores) == 100
    assert sum(1 for entry in scores if entry.correct) == correct
    report = report_of(scores)
    assert report["f58"]["applicable"] is True
    assert report["f58"]["passed"] is passed
    assert report["f58"]["checks"]["correct_at_least_95"] is passed
    assert report["f58"]["checks"]["exact_count_accuracy"] is True
    assert report["f58"]["checks"]["abstention_rate"] is True


def test_f58_needs_exact_counts_and_full_abstention():
    # 95 correct, but one count wrong in the text: exact_count_accuracy below 1.
    scores = [score(f"c{i}", QuestionKind.COUNT, status="answered") for i in range(100)]
    scores[0] = score("c0", QuestionKind.COUNT, correct=False, status="answered")
    report = report_of(scores)
    assert report["f58"]["passed"] is False
    assert report["f58"]["checks"]["exact_count_accuracy"] is False
    # 95+ correct but no unsupported question: the abstention check fails (A1.4).
    report = report_of(
        [score(f"c{i}", QuestionKind.COUNT, status="answered") for i in range(100)]
    )
    assert report["f58"]["passed"] is False
    assert report["f58"]["checks"]["abstention_rate"] is False


def test_f58_is_not_applicable_below_100_or_unapproved():
    for approved, total in ((True, 99), (False, 100)):
        report = CopilotEvaluationReport(
            format=QUESTION_SET_FORMAT,
            set_id="s",
            approved=approved,
            notice="n",
            question_set_sha256="0" * 64,
            scores=tuple(score(f"c{i}", QuestionKind.COUNT) for i in range(total)),
        ).to_json()
        assert report["f58"]["applicable"] is False
        assert report["f58"]["passed"] is None


def test_run_question_set_scores_through_the_ask_function():
    made = parse_question_set(raw_set())
    answers = {"c01": observed()}

    def ask(question):
        return answers[question.id]

    report = run_question_set(made, ask)
    assert report.question_set_sha256 == made.sha256
    assert report.scores[0].correct is True


# --- the CLI ---------------------------------------------------------------------------------


def write_set(tmp_path, raw: bytes) -> str:
    path = tmp_path / "set.json"
    path.write_bytes(raw)
    return str(path)


def test_the_cli_prints_one_document_and_exits_zero(tmp_path, capsys):
    from backend.evaluation.copilot import main

    assert main(["validate", write_set(tmp_path, raw_set())]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {
        "format": "copilot-questions/1",
        "set_id": "synthetic-placeholder-v1",
        "approved": False,
        "questions": 1,
        "by_kind": {"count": 1},
        "sha256": parse_question_set(raw_set()).sha256,
    }


def test_the_cli_counts_the_kinds(tmp_path, capsys):
    from backend.evaluation.copilot import main

    raw = raw_set(
        questions=[
            {
                "id": "c1",
                "question": "how many?",
                "caller": "a",
                "kind": "count",
                "expected": {"values": ["1"]},
            },
            {
                "id": "u1",
                "question": "why?",
                "caller": "a",
                "kind": "unsupported",
                "expected": {},
            },
        ]
    )
    assert main(["validate", write_set(tmp_path, raw)]) == 0
    assert json.loads(capsys.readouterr().out)["by_kind"] == {"count": 1, "unsupported": 1}


def test_the_cli_exits_one_on_bad_data(tmp_path, capsys):
    from backend.evaluation.copilot import main

    path = tmp_path / "bad.json"
    path.write_bytes(b"{")
    assert main(["validate", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")


def test_the_cli_exits_one_on_a_missing_file(tmp_path, capsys):
    from backend.evaluation.copilot import main

    assert main(["validate", str(tmp_path / "nope.json")]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_the_cli_exits_two_on_bad_arguments(capsys):
    from backend.evaluation.copilot import main

    for argv in ([], ["nonsense"], ["validate"], ["validate", "a", "b"]):
        with pytest.raises(SystemExit) as exit_info:
            main(argv)  # type: ignore[arg-type]
        assert exit_info.value.code == 2


def _run_module_cli(tmp_path, raw: bytes) -> subprocess.CompletedProcess[str]:
    """``python -m backend.evaluation.copilot`` as a real subprocess (A5.6),
    with the package ``__init__`` at its base (it does not import this module)."""
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(  # noqa: S603 - fixed argv, the package's own CLI
        [sys.executable, "-m", "backend.evaluation.copilot", "validate", write_set(tmp_path, raw)],
        capture_output=True,
        text=True,
        cwd=root,
    )


def test_the_module_cli_prints_nothing_on_stderr_for_a_valid_file(tmp_path):
    done = _run_module_cli(tmp_path, raw_set())
    assert done.returncode == 0
    assert done.stderr == ""
    assert json.loads(done.stdout)["format"] == QUESTION_SET_FORMAT


def test_the_module_cli_prints_exactly_one_error_line_for_bad_data(tmp_path):
    done = _run_module_cli(tmp_path, b"{")
    assert done.returncode == 1
    assert done.stdout == ""
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    assert len(lines) == 1 and lines[0].startswith("error: ")


def test_the_module_cli_keeps_a_newline_key_on_one_stderr_line(tmp_path):
    """F3: the key messages quote the key (``!r``), so a key that holds a
    newline is still exactly one ``error:`` line, as a repeated key and as an
    unknown key."""
    body = raw_set().decode().lstrip("{")
    repeated = ('{"x\\ny": 1, "x\\ny": 2, ' + body).encode()
    done = _run_module_cli(tmp_path, repeated)
    assert done.returncode == 1
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    assert len(lines) == 1 and lines[0].startswith("error: ")
    unknown = json.dumps({**json.loads(raw_set()), "x\ny": 1}).encode()
    done = _run_module_cli(tmp_path, unknown)
    assert done.returncode == 1
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    assert len(lines) == 1 and lines[0].startswith("error: ")


def test_the_module_is_pure():
    import inspect

    source = inspect.getsource(module)
    for forbidden in ("import requests", "psycopg", "sqlalchemy", "datetime.now"):
        assert forbidden not in source
