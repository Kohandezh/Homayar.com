"""HOM-138 (F70, A7): the CSV formula rule on Persian text.

``neutralize_csv_cell`` is unchanged (HOM-137).  These tests pin how it
treats Persian strings: a formula character first, or after an invisible
character, gets the ``'`` prefix; ordinary Persian text, an inner ``=`` or
``-`` and Persian digits stay as they are.
"""

from __future__ import annotations

import csv
import io

import pytest

from backend.services.report_export_service import neutralize_csv_cell, render_report_export
from backend.tests.test_report_export_service import _provenance, _report

TRIGGERS = ["=", "+", "-", "@", "＝", "＋", "－", "＠"]
INVISIBLE = {
    "none": "",
    "zwnj": "‌",
    "rlm": "‏",
    "lrm": "‎",
    "alm": "؜",
    "zwsp": "​",
    "bom": "﻿",
    "nbsp": " ",
    "space": " ",
}


@pytest.mark.parametrize("trigger", TRIGGERS)
@pytest.mark.parametrize("hidden", list(INVISIBLE))
def test_a_persian_formula_gets_the_prefix(trigger, hidden):
    value = f"{INVISIBLE[hidden]}{trigger}SUM(A1) جمع کل"
    assert neutralize_csv_cell(value) == "'" + value


@pytest.mark.parametrize(
    "value",
    [
        "سلام دنیا",
        "قیمت = ۱۰۰ تومان",
        "سه-شنبه ساعت ۱۰",
        "۱۴۰۳",
        "۱۲۳-۴۵۶",
        "می‌شود",
        "‌می‌شود",
        "جلسه با Acme در ۱۴۰۳ برگزار شد",
        "",
    ],
)
def test_plain_persian_text_is_unchanged(value):
    assert neutralize_csv_cell(value) == value


def test_the_csv_starts_with_a_bom_and_decodes_back_to_the_persian_strings():
    overview = "قیمت فردا اعلام می‌شود، جلسه با Acme در ۱۴۰۳"
    formula = '‏=HYPERLINK("http://x") قیمت'
    rendered = render_report_export(
        _report(overview=overview, decision_text=formula),
        _provenance(format="csv"),
        "csv",
        title="گزارش",
    )
    assert rendered.content.startswith("﻿".encode())
    text = rendered.content.decode("utf-8")
    rows = dict(csv.reader(io.StringIO(text[1:], newline="")))
    assert rows["report.summary.overview"] == overview
    assert rows["report.outcome.decisions.0.text"] == "'" + formula
    assert rows["report.summary.topics.0"] == "قیمت"
