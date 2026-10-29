"""Smoke test for the privacy gate tool (HOM-321).

The tool is driven only through its command line, the way a developer or CI
runs it.  No fixture from ``backend/tests/conftest.py`` is used (HOM-292 is
changing that file): only pytest built-ins such as ``tmp_path``.  No database,
no Redis, no network, no Docker.  No time or CPU budget is asserted anywhere
(HOM-307, HOM-310).  ``text_pii`` validators are imported only as an
independent check of the synthetic ground truth.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from backend.privacy.text_pii import valid_iban, valid_luhn, valid_national_id
from backend.tests.tools.privacy_gates import gates

TREE = Path(__file__).resolve().parents[4]  # the Homayar/ folder that holds this tool
TOOL = [sys.executable, "-m", "backend.tests.tools.privacy_gates"]
CORPORA = ("dense", "dsar", "prices", "words")
PRICE_BUCKETS = {"20-25": (20, 25), "26-35": (26, 35), "36-60": (36, 60)}
ALL_CLASSES = {
    "email",
    "phone",
    "ir_national_id",
    "bank_card",
    "iban",
    "ir_postal_code",
}
FOLD_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "0123456789" * 2)
EMAIL_RE = re.compile(r"[a-z0-9._-]+@example\.(com|org)")
PHONE_RE = re.compile(r"09[0-49][0-9]{8}|0[1-8][0-9]{9}")
SPEC_CLASS_LISTS = {
    "all": ["email", "phone", "ir_national_id", "bank_card", "iban", "ir_postal_code"],
    "speech": ["phone", "ir_national_id", "email"],
    "export": ["email", "phone", "ir_national_id", "bank_card", "iban"],
    "phone": ["phone"],
    "id": ["ir_national_id"],
    "card": ["bank_card"],
    "postal": ["ir_postal_code"],
    "phone+card": ["phone", "bank_card"],
}
SPEC_COST_WORKLOADS = {
    "detect-all-dense",
    "detect-export-dense",
    "detect-all-words",
    "export-dsar",
}


def run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this interpreter running the tool under test
        [*TOOL, *args], cwd=TREE, capture_output=True, text=True
    )


def read_rows(out: Path, name: str) -> list[dict]:
    lines = (out / "corpus" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def digits_only(raw: str) -> str:
    return "".join(char for char in raw.translate(FOLD_DIGITS) if char.isascii() and char.isdigit())


def iban_is_well_shaped(raw: str) -> bool:
    """Compact, or in groups of 4 joined by single spaces (SPEC 3.2)."""
    parts = raw.split(" ")
    compact = "".join(parts)
    if compact[:2] != "IR" or len(compact) != 26:
        return False
    return raw == compact or (len(parts) == 7 and [len(p) for p in parts] == [4] * 6 + [2])


def _copy_backend(destination: Path) -> Path:
    """A copy of backend/ outside git, without tests/, migrations/, caches."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        skip = {name for name in names if name == "__pycache__" or name.endswith(".pyc")}
        if Path(directory).name == "backend":
            skip |= {"tests", "migrations"}
        return skip

    shutil.copytree(TREE / "backend", destination / "backend", ignore=ignore)
    return destination


def _append_to_detect(copy: Path, wrapper: str) -> Path:
    """A tree copy whose module-level ``detect`` is wrapped by appended code.

    Appending (not editing a line of ``detect``) keeps the smoke test working
    when the body of ``detect`` itself changes, which is the main reason this
    tool exists.  The worker calls ``text_pii.detect`` through the module
    attribute, so it sees the wrapper."""
    pii = copy / "backend" / "privacy" / "text_pii.py"
    code = pii.read_text(encoding="utf-8")
    appended = "_hom321_detect = detect\n\n\n" + wrapper
    pii.write_text(code.rstrip() + "\n\n\n" + appended, encoding="utf-8")
    return copy


def test_same_seed_gives_byte_identical_corpora(tmp_path):
    first, second, other = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    assert run_tool("corpus", "--out", str(first), "--seed", "7", "--rows", "5").returncode == 0
    assert run_tool("corpus", "--out", str(second), "--seed", "7", "--rows", "5").returncode == 0
    assert run_tool("corpus", "--out", str(other), "--seed", "8", "--rows", "5").returncode == 0
    for name in CORPORA:
        path = first / "corpus" / f"{name}.jsonl"
        assert path.read_bytes() == (second / "corpus" / f"{name}.jsonl").read_bytes()
        assert path.read_bytes() != (other / "corpus" / f"{name}.jsonl").read_bytes()


def test_planted_items_slice_the_text_and_hold_valid_values(tmp_path):
    out = tmp_path / "corpus"
    assert run_tool("corpus", "--out", str(out), "--seed", "321", "--rows", "400").returncode == 0
    seen: set[str] = set()
    for name in CORPORA:
        for row in read_rows(out, name):
            for item in row["planted"]:
                assert row["text"][item["start"] : item["end"]] == item["raw"]
                seen.add(item["class"])
                if item["class"] == "ir_national_id":
                    assert valid_national_id(digits_only(item["raw"]))
                elif item["class"] == "bank_card":
                    assert valid_luhn(digits_only(item["raw"]))
                elif item["class"] == "iban":
                    compact = "".join(
                        char
                        for char in item["raw"].upper().translate(FOLD_DIGITS)
                        if char.isalnum()
                    )
                    assert valid_iban(compact)
                    assert iban_is_well_shaped(item["raw"]), item["raw"]
                elif item["class"] == "email":
                    assert EMAIL_RE.fullmatch(item["raw"]), item["raw"]
                elif item["class"] == "phone":
                    assert PHONE_RE.fullmatch(digits_only(item["raw"])), item["raw"]
            if name != "dsar":
                assert row["subject"] == []
                assert not any(item["subject"] for item in row["planted"])
    assert seen == ALL_CLASSES


def test_every_corpus_exists_and_rows_match_their_shape(tmp_path):
    out = tmp_path / "corpus"
    assert run_tool("corpus", "--out", str(out), "--seed", "11", "--rows", "24").returncode == 0
    for name in CORPORA:
        rows = read_rows(out, name)
        assert rows, name
        assert len(rows) == 24
    prices = read_rows(out, "prices")
    for row in prices:
        count = len(digits_only(row["text"]))
        low, high = PRICE_BUCKETS[row["bucket"]]
        assert low <= count <= high, row
        assert row["planted"] == []
    assert {row["bucket"] for row in prices} == set(PRICE_BUCKETS)
    for row in read_rows(out, "dsar"):
        assert 1 <= len(row["subject"]) <= 3, row


def test_tiny_corpora_still_fill_every_price_bucket(tmp_path):
    out = tmp_path / "corpus"
    assert run_tool("corpus", "--out", str(out), "--seed", "3", "--rows", "1").returncode == 0
    prices = read_rows(out, "prices")
    assert len(prices) == 3
    assert {row["bucket"] for row in prices} == set(PRICE_BUCKETS)
    assert len(read_rows(out, "dense")) == 1
    assert len(read_rows(out, "words")) == 1


def test_run_with_base_equal_to_tree_passes_every_gate(tmp_path):
    out = tmp_path / "out"
    done = run_tool(
        "run", "--base-dir", str(TREE), "--rows", "24", "--cost-rows", "8", "--out", str(out)
    )
    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    assert lines[-1] == "RESULT: PASS"
    for gate in ("G1 ", "G2 ", "G3 ", "G4 ", "G5 "):
        assert any(line.startswith(gate) for line in lines), gate
    for line in lines:
        if line.startswith(("G1 ", "G2 ", "G3 ")):
            cells = [cell.strip() for cell in line.split("|")]
            assert cells[2] == "-", line
    assert (out / "report.json").is_file()
    assert (out / "report.md").is_file()


def test_run_fails_when_the_patch_drops_phone_spans(tmp_path):
    broken = _append_to_detect(
        _copy_backend(tmp_path / "broken"),
        "def detect(text, classes=SUPPORTED_CLASSES):\n"
        "    return [s for s in _hom321_detect(text, classes)\n"
        "            if s.pii_class is not PiiClass.PHONE]\n",
    )
    out = tmp_path / "out"
    done = run_tool(
        "run",
        "--base-dir",
        str(TREE),
        "--tree-dir",
        str(broken),
        "--rows",
        "24",
        "--cost-rows",
        "8",
        "--out",
        str(out),
    )
    assert done.returncode == 1, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    for gate in ("G1 ", "G2 "):
        gate_lines = [line for line in lines if line.startswith(gate)]
        assert gate_lines and "FAIL" in gate_lines[0], gate
    assert lines[-1].startswith("RESULT: FAIL")


def test_run_fails_and_reports_examples_when_the_patch_spans_price_rows(tmp_path):
    loud = _append_to_detect(
        _copy_backend(tmp_path / "loud"),
        "def detect(text, classes=SUPPORTED_CLASSES):\n"
        "    spans = _hom321_detect(text, classes)\n"
        "    if not spans and text[:1].isdigit():\n"
        "        end = min(11, len(text))\n"
        "        spans = [PiiSpan(0, end, PiiClass.PHONE, text[:end])]\n"
        "    return spans\n",
    )
    out = tmp_path / "out"
    done = run_tool(
        "run",
        "--base-dir",
        str(TREE),
        "--tree-dir",
        str(loud),
        "--rows",
        "24",
        "--cost-rows",
        "8",
        "--out",
        str(out),
    )
    assert done.returncode == 1, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    g4_lines = [line for line in lines if line.startswith("G4 ")]
    assert g4_lines and all("FAIL" in line for line in g4_lines), g4_lines
    assert lines[-1].startswith("RESULT: FAIL")
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    examples = report["gates"]["G4"]["examples"]
    assert examples, "G4 failed but report.json holds no examples"
    first = examples[0]
    assert set(first) == {"row", "bucket", "class_list", "patch_spans"}
    assert first["patch_spans"]
    rows = {row["id"]: row for row in read_rows(out, "prices")}
    assert first["row"] in rows


def test_missing_text_pii_in_base_dir_is_a_setup_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    done = run_tool("run", "--base-dir", str(empty), "--rows", "6", "--out", str(tmp_path / "out"))
    assert done.returncode == 2
    assert done.stderr.strip()
    assert len(done.stderr.strip().splitlines()) == 1
    assert done.stdout.strip() == ""


def test_bad_base_is_checked_before_the_corpus_is_written(tmp_path):
    out = tmp_path / "out"
    done = run_tool("run", "--base", "no-such-rev", "--rows", "6", "--out", str(out))
    assert done.returncode == 2
    assert not out.exists()


def test_base_rev_works_with_a_tree_dir_outside_git(tmp_path):
    copy = _copy_backend(tmp_path / "copy")
    out = tmp_path / "out"
    done = run_tool(
        "run",
        "--base",
        "HEAD",
        "--tree-dir",
        str(copy),
        "--rows",
        "24",
        "--cost-rows",
        "8",
        "--out",
        str(out),
    )
    assert done.returncode in (0, 1), done.stdout + done.stderr
    assert done.stdout.splitlines()[-1].startswith("RESULT: ")


def test_rows_below_one_is_rejected(tmp_path):
    done = run_tool("run", "--base-dir", str(TREE), "--rows", "0", "--out", str(tmp_path / "o"))
    assert done.returncode == 2
    done = run_tool("corpus", "--out", str(tmp_path / "c"), "--rows", "-1")
    assert done.returncode == 2
    done = run_tool(
        "run",
        "--base-dir",
        str(TREE),
        "--rows",
        "6",
        "--cost-rows",
        "0",
        "--out",
        str(tmp_path / "o2"),
    )
    assert done.returncode == 2


def test_out_that_is_a_file_is_a_setup_error(tmp_path):
    target = tmp_path / "not-a-dir"
    target.write_text("x", encoding="utf-8")
    done = run_tool(
        "run",
        "--base-dir",
        str(TREE),
        "--rows",
        "24",
        "--cost-rows",
        "8",
        "--out",
        str(target),
    )
    assert done.returncode == 2
    assert len(done.stderr.strip().splitlines()) == 1
    assert done.stdout.strip() == ""


def test_g5_passes_exactly_at_the_limit():
    pair = None
    for base in range(2, 300):
        for patch in range(base, base * 4):
            if patch > base * (patch / base):  # the rounded product falls below patch
                pair = (patch, base)
                break
        if pair:
            break
    assert pair is not None, "no float-rounding pair found"
    patch_count, base_count = pair
    limit = patch_count / base_count
    assert patch_count > base_count * limit  # the naive product formula fails here
    base_costs = {name: base_count for name in gates.COST_WORKLOADS}
    patch_costs = {name: patch_count for name in gates.COST_WORKLOADS}
    g5 = gates._gate5({"cost": base_costs}, {"cost": patch_costs}, limit)
    assert g5["pass"]
    assert all(info["pass"] for info in g5["workloads"].values())


def test_shared_tables_are_defined_once_and_match_the_spec():
    # Two copies could drift, and then the two sides of a gate would use other
    # class lists than the table says.
    assert gates.CLASS_LISTS == SPEC_CLASS_LISTS
    assert set(gates.COST_WORKLOADS) == SPEC_COST_WORKLOADS
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(Path(__file__).parent.glob("*.py"))
        if path.name != Path(__file__).name
    }
    for name in ("CLASS_LISTS", "EXPORT_CLASS_VALUES", "COST_WORKLOADS"):
        holders = [
            file for file, text in sources.items() if re.search(rf"^{name} = ", text, re.MULTILINE)
        ]
        assert holders == ["worker.py"], name
