"""The gate measures, the pass/fail table and the reports (HOM-321).

Pure comparison: everything here works on the corpus rows and the two result
files the workers wrote, never on the code under test.  A gate fails only on
what the table shows; the D (differences) and recall lines are information
only and never change the exit code.
"""

from __future__ import annotations

from backend.tests.tools.privacy_gates.corpus import PRICE_BUCKET_NAMES
from backend.tests.tools.privacy_gates.worker import CLASS_LISTS, COST_WORKLOADS

MAX_EXAMPLES = 20


class SetupError(Exception):
    """A setup problem found while comparing: exit 2, no table."""


def _span_pairs(side: dict, corpus: str) -> dict[str, dict[str, list[tuple[int, int]]]]:
    """The detected spans of one side as (start, end) pairs, per row and list."""
    return {
        row_id: {name: [(span[0], span[1]) for span in lists[name]] for name in lists}
        for row_id, lists in side["detect"].get(corpus, {}).items()
    }


def _covered(spans: list[tuple[int, int]], positions: list[int]) -> bool:
    """Whether every position lies inside some span (spans are in text order,
    so one forward walk is enough)."""
    index = 0
    for position in positions:
        while index < len(spans) and spans[index][1] <= position:
            index += 1
        if index == len(spans) or not spans[index][0] <= position < spans[index][1]:
            return False
    return True


def _lost_positions(
    base_spans: list[tuple[int, int]], patch_spans: list[tuple[int, int]]
) -> tuple[int, list[int]]:
    """Positions inside a span on base and outside every span on patch, plus a
    few sample positions for the report."""
    lost = 0
    samples: list[int] = []
    skipped = 0
    for start, end in base_spans:
        while skipped < len(patch_spans) and patch_spans[skipped][1] <= start:
            skipped += 1
        cursor = start
        k = skipped
        while k < len(patch_spans) and patch_spans[k][0] < end:
            patch_start, patch_end = patch_spans[k]
            if patch_start > cursor:
                lost += patch_start - cursor
                samples.extend(range(cursor, patch_start)[:3])
                cursor = patch_start
            cursor = max(cursor, patch_end)
            k += 1
        if cursor < end:
            lost += end - cursor
            samples.extend(range(cursor, end)[:3])
    return lost, samples[:3]


def _gate1_and_recall(rows_by_corpus: dict, base: dict, patch: dict) -> tuple[dict, dict]:
    """G1: planted items covered on base and raw on patch.  Recall per side."""
    g1 = {"planted": 0, "base_covered": 0, "patch_covered": 0, "losses": 0, "examples": []}
    recall: dict[str, tuple[int, int, int]] = {}
    for corpus in sorted(rows_by_corpus):
        base_pairs = _span_pairs(base, corpus)
        patch_pairs = _span_pairs(patch, corpus)
        counts = [0, 0, 0]  # planted, covered on base, covered on patch
        for row in rows_by_corpus[corpus]:
            text = row["text"]
            for item in row["planted"]:
                positions = [i for i in range(item["start"], item["end"]) if text[i].isalnum()]
                for list_name, values in CLASS_LISTS.items():
                    if item["class"] not in values:
                        continue
                    counts[0] += 1
                    on_base = _covered(base_pairs[row["id"]][list_name], positions)
                    on_patch = _covered(patch_pairs[row["id"]][list_name], positions)
                    counts[1] += on_base
                    counts[2] += on_patch
                    if on_base and not on_patch:
                        g1["losses"] += 1
                        if len(g1["examples"]) < MAX_EXAMPLES:
                            g1["examples"].append(
                                {
                                    "corpus": corpus,
                                    "row": row["id"],
                                    "class_list": list_name,
                                    "class": item["class"],
                                    "raw": item["raw"],
                                    "start": item["start"],
                                    "end": item["end"],
                                }
                            )
        g1["planted"] += counts[0]
        g1["base_covered"] += counts[1]
        g1["patch_covered"] += counts[2]
        recall[corpus] = (counts[1], counts[2], counts[0])
    return g1, recall


def _gate2(rows_by_corpus: dict, base: dict, patch: dict) -> dict:
    """G2: positions inside a span on base and outside every span on patch."""
    g2 = {"base_positions": 0, "lost": 0, "examples": []}
    for corpus in sorted(rows_by_corpus):
        base_pairs = _span_pairs(base, corpus)
        patch_pairs = _span_pairs(patch, corpus)
        for row in rows_by_corpus[corpus]:
            for list_name in CLASS_LISTS:
                base_spans = base_pairs[row["id"]][list_name]
                patch_spans = patch_pairs[row["id"]][list_name]
                g2["base_positions"] += sum(end - start for start, end in base_spans)
                lost, samples = _lost_positions(base_spans, patch_spans)
                if lost:
                    g2["lost"] += lost
                    if len(g2["examples"]) < MAX_EXAMPLES:
                        g2["examples"].append(
                            {
                                "corpus": corpus,
                                "row": row["id"],
                                "class_list": list_name,
                                "positions": samples,
                                "positions_lost": lost,
                            }
                        )
    return g2


def _gate3(rows_by_corpus: dict, base: dict, patch: dict) -> dict:
    """G3: new false subject-export matches and lost true matches on dsar."""
    g3 = {"false_matches": 0, "misses": 0, "examples": []}
    for row in rows_by_corpus.get("dsar", []):
        on_base = set(base["export"].get(row["id"], []))
        on_patch = set(patch["export"].get(row["id"], []))
        subject_classes = {item["class"] for item in row["planted"] if item["subject"]}
        for item_class in sorted(on_patch - on_base):
            if item_class not in subject_classes:
                g3["false_matches"] += 1
                if len(g3["examples"]) < MAX_EXAMPLES:
                    g3["examples"].append(
                        {"row": row["id"], "class": item_class, "kind": "false match"}
                    )
        for item_class in sorted(on_base - on_patch):
            if item_class in subject_classes:
                g3["misses"] += 1
                if len(g3["examples"]) < MAX_EXAMPLES:
                    g3["examples"].append({"row": row["id"], "class": item_class, "kind": "miss"})
    return g3


def _gate4(rows_by_corpus: dict, base: dict, patch: dict) -> dict:
    """G4: the patch must not span more price rows than base, per bucket and list."""
    g4 = {"buckets": {}, "examples": [], "pass": True}
    prices = rows_by_corpus.get("prices", [])
    if not prices:
        return g4
    base_pairs = _span_pairs(base, "prices")
    patch_pairs = _span_pairs(patch, "prices")
    patch_detect = patch["detect"].get("prices", {})
    for bucket in PRICE_BUCKET_NAMES:
        bucket_rows = [row for row in prices if row["bucket"] == bucket]
        if not bucket_rows:
            continue
        shares = {}
        for list_name in CLASS_LISTS:
            base_count = sum(1 for row in bucket_rows if base_pairs[row["id"]][list_name])
            patch_count = sum(1 for row in bucket_rows if patch_pairs[row["id"]][list_name])
            size = len(bucket_rows)
            shares[list_name] = (base_count / size, patch_count / size)
        worst = max(shares, key=lambda name: shares[name][1] - shares[name][0])
        passed = all(patch_share <= base_share for base_share, patch_share in shares.values())
        g4["buckets"][bucket] = {"shares": shares, "worst": worst, "pass": passed}
        if not passed:
            g4["pass"] = False
        # Examples: price rows the patch spans and base does not (SPEC 3.6).
        if len(g4["examples"]) >= MAX_EXAMPLES:
            continue
        for list_name in CLASS_LISTS:
            for row in bucket_rows:
                if base_pairs[row["id"]][list_name] or not patch_pairs[row["id"]][list_name]:
                    continue
                g4["examples"].append(
                    {
                        "row": row["id"],
                        "bucket": bucket,
                        "class_list": list_name,
                        "patch_spans": patch_detect.get(row["id"], {}).get(list_name, []),
                    }
                )
                if len(g4["examples"]) >= MAX_EXAMPLES:
                    break
            if len(g4["examples"]) >= MAX_EXAMPLES:
                break
    return g4


def _gate5(base: dict, patch: dict, cost_limit: float) -> dict:
    """G5: lines run by the patch side at most cost_limit times the base side.
    The verdict compares the unrounded ratio, so G5 passes exactly at the
    limit (AC6): ``base * limit`` can round to just below ``patch``."""
    g5 = {"workloads": {}, "pass": True}
    for name in COST_WORKLOADS:
        base_count = base["cost"].get(name, 0)
        patch_count = patch["cost"].get(name, 0)
        if base_count <= 0:
            raise SetupError(f"base side counted 0 lines for the cost workload {name}")
        ratio = patch_count / base_count
        passed = ratio <= cost_limit
        g5["workloads"][name] = {
            "base": base_count,
            "patch": patch_count,
            "ratio": ratio,
            "pass": passed,
        }
        if not passed:
            g5["pass"] = False
    return g5


def _differences(rows_by_corpus: dict, base: dict, patch: dict) -> dict:
    """D (information only): rows whose spans or export classes differ."""
    spans = {name: 0 for name in CLASS_LISTS}
    export = 0
    for corpus in sorted(rows_by_corpus):
        base_detect = base["detect"].get(corpus, {})
        patch_detect = patch["detect"].get(corpus, {})
        for row in rows_by_corpus[corpus]:
            row_id = row["id"]
            for list_name in CLASS_LISTS:
                if base_detect.get(row_id, {}).get(list_name) != patch_detect.get(row_id, {}).get(
                    list_name
                ):
                    spans[list_name] += 1
    for row in rows_by_corpus.get("dsar", []):
        if base["export"].get(row["id"]) != patch["export"].get(row["id"]):
            export += 1
    return {"spans": spans, "export": export}


def evaluate(
    rows_by_corpus: dict[str, list[dict]], base: dict, patch: dict, cost_limit: float
) -> dict:
    """Run every gate and build the table rows and the verdict."""
    g1, recall = _gate1_and_recall(rows_by_corpus, base, patch)
    g2 = _gate2(rows_by_corpus, base, patch)
    g3 = _gate3(rows_by_corpus, base, patch)
    g4 = _gate4(rows_by_corpus, base, patch)
    g5 = _gate5(base, patch, cost_limit)
    differences = _differences(rows_by_corpus, base, patch)

    failed = []
    if g1["losses"]:
        failed.append("G1")
    if g2["lost"]:
        failed.append("G2")
    if g3["false_matches"] or g3["misses"]:
        failed.append("G3")
    if not g4["pass"]:
        failed.append("G4")
    if not g5["pass"]:
        failed.append("G5")
    passed = not failed

    # G1 to G3 count differences between the sides, so their Base cell is "-"
    # (a number there reads as a count of losses on base, SPEC 3.6).  The
    # scope counts stay in the Measure text and in report.json.
    table: list[tuple[str, str, object, object, str, str]] = [
        (
            "G1",
            f"planted items lost by the patch ({g1['planted']} planted in scope)",
            "-",
            g1["losses"],
            "0",
            "PASS" if not g1["losses"] else "FAIL",
        ),
        (
            "G2",
            f"positions base covered and patch left raw ({g2['base_positions']} on base)",
            "-",
            g2["lost"],
            "0",
            "PASS" if not g2["lost"] else "FAIL",
        ),
        (
            "G3",
            "new false subject-export matches",
            "-",
            g3["false_matches"],
            "0",
            "PASS" if not g3["false_matches"] else "FAIL",
        ),
        (
            "G3",
            "lost subject-export matches",
            "-",
            g3["misses"],
            "0",
            "PASS" if not g3["misses"] else "FAIL",
        ),
    ]
    for bucket, info in g4["buckets"].items():
        base_share, patch_share = info["shares"][info["worst"]]
        table.append(
            (
                "G4",
                f"prices {bucket} rows with a span (worst: {info['worst']})",
                f"{base_share:.4f}",
                f"{patch_share:.4f}",
                "<= base",
                "PASS" if info["pass"] else "FAIL",
            )
        )
    for name in COST_WORKLOADS:
        info = g5["workloads"][name]
        table.append(
            (
                "G5",
                f"lines run by {name}",
                info["base"],
                f"{info['patch']} ({info['ratio']:.2f}x)",
                f"{cost_limit:g}",
                "PASS" if info["pass"] else "FAIL",
            )
        )
    for list_name, count in differences["spans"].items():
        table.append(("D", f"rows with different spans ({list_name})", "-", count, "-", "info"))
    table.append(
        ("D", "dsar rows with different export classes", "-", differences["export"], "-", "info")
    )
    for corpus in sorted(recall):
        covered_base, covered_patch, planted = recall[corpus]
        table.append(
            (
                "R",
                f"planted recall, {corpus} corpus (covered/planted)",
                f"{covered_base}/{planted}",
                f"{covered_patch}/{planted}",
                "-",
                "info",
            )
        )

    return {
        "result": "PASS" if passed else "FAIL",
        "failed_gates": failed,
        "table": table,
        "gates": {
            "G1": g1,
            "G2": g2,
            "G3": g3,
            "G4": g4,
            "G5": g5,
            "D": differences,
            "recall": recall,
        },
    }


def result_line(report: dict) -> str:
    if report["result"] == "PASS":
        return "RESULT: PASS"
    return f"RESULT: FAIL ({', '.join(report['failed_gates'])})"


def format_table(table: list[tuple]) -> list[str]:
    """The plain-text table with padded columns."""
    header = ("Gate", "Measure", "Base", "Patch", "Limit", "Result")
    rows = [[str(value) for value in row] for row in table]
    widths = [
        max(len(row[column]) for row in rows + [list(header)]) for column in range(len(header))
    ]
    lines = [
        " | ".join(header[i].ljust(widths[i]) for i in range(len(header))).rstrip(),
        "-+-".join("-" * width for width in widths),
    ]
    lines.extend(
        " | ".join(row[i].ljust(widths[i]) for i in range(len(row))).rstrip() for row in rows
    )
    return lines


def markdown_table(table: list[tuple]) -> list[str]:
    """The same table as markdown, for report.md."""
    lines = [
        "| Gate | Measure | Base | Patch | Limit | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(" | ".join(str(value) for value in row) for row in table)
    return lines
