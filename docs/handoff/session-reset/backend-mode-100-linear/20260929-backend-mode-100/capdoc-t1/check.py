#!/usr/bin/env python3
"""capdoc-t1 acceptance oracle (phase A, written by capdoc-t1-rev before any patch was visible).

Usage
    python3 check.py NEW_APPVISION.md BASE_APPVISION.md [options]

    NEW   path to the AppVision.md under review (the maker's file)
    BASE  path to the base AppVision.md (commit 7112407) OR to a checkout root containing Homayar/AppVision.md

Options
    --repo DIR         git checkout used to verify cited file paths / commit SHAs (default: the git root of BASE)
    --maker-repo DIR   maker worktree; with --base-sha checks that ONLY Homayar/AppVision.md changed (AC file ownership)
    --base-sha SHA     base commit for --maker-repo (default 7112407)
    --json             machine readable output
    --dump-blocks      print the parsed block + fields of every N item (for the human review)
    -q                 hide PASS/INFO lines

Levels
    PASS     check satisfied
    INFO     facts for the reviewer, never blocks
    WARN     heuristic doubt; the reviewer must read it and decide (does not change the exit code)
    REVIEW   the script cannot decide; the reviewer MUST adjudicate it in the review file (does not change the exit code)
    FAIL     acceptance criterion violated
    MISSING  an N item (or a whole required artefact) is not present at all

Exit code: 0 = no FAIL/MISSING; 1 = at least one FAIL/MISSING; 2 = usage/parse error.
Only the stdlib is used; no network access.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import os
import re
import subprocess
import sys

# --------------------------------------------------------------------------------------
# Static contract data (from SPEC.md, owner-approved-capabilities-N01-N15.md, Linear read-back)
# --------------------------------------------------------------------------------------
NIDS = [f"N{i:02d}" for i in range(1, 16)]
HOM_OF = {n: 141 + int(n[1:]) for n in NIDS}  # N01=HOM-142 ... N15=HOM-156 (HOM-141 is the [CAP] parent)
LINEAR_SLUG = {  # verified against Linear (list_issues parentId=HOM-141), 2026-09-30
    "N01": "n01-customer-onboarding-backend",
    "N02": "n02-data-import-and-migration-reuses-v01",
    "N03": "n03-recording-consent-policy-engine-and-enforcement",
    "N04": "n04-data-quality-backend-reuses-f26",
    "N05": "n05-reprocessing-workflow",
    "N06": "n06-feedback-to-evaluation-loop-reuses-f81",
    "N07": "n07-notification-preferences-reuses-f59",
    "N08": "n08-provider-budget-kill-switch-reuses-f18",
    "N09": "n09-support-entitlement-reuses-x16",
    "N10": "n10-privacy-preserving-product-telemetry",
    "N11": "n11-migration-and-legacy-retirement-reuses-f01",
    "N12": "n12-disaster-recovery-objectives-reuses-f79",
    "N13": "n13-accessibility-backend-support-reuses-f72",
    "N14": "n14-browserdevice-support-backend-contract-reuses-f12",
    "N15": "n15-model-and-prompt-rollback-reuses-f20",
}
REUSE = {"N02": "V01", "N04": "F26", "N06": "F81", "N07": "F59", "N08": "F18", "N09": "X16",
         "N11": "F01", "N12": "F79", "N13": "F72", "N14": "F12", "N15": "F20"}
NEW_ITEMS = ["N01", "N03", "N05", "N10"]

TITLE_RE = {  # every regex must match the item's name (row 2nd cell or heading text)
    "N01": [r"onboarding"], "N02": [r"import", r"migration"], "N03": [r"consent"], "N04": [r"quality"],
    "N05": [r"reprocess"], "N06": [r"feedback", r"evaluation"], "N07": [r"notification"], "N08": [r"budget"],
    "N09": [r"support entitlement"], "N10": [r"telemetry"], "N11": [r"legacy", r"retire|retirement"],
    "N12": [r"disaster recovery|recovery objective"], "N13": [r"accessibility"], "N14": [r"browser", r"device"],
    "N15": [r"rollback"],
}

CANDIDATES = {  # owner section 10.x "Candidate repositories/tools/reference"; None = the owner lists none
    "N01": [],
    "N02": [r"\bdlt\b", r"meltano", r"airbyte"],
    "N03": [r"openfgc"],
    "N04": [r"evidently", r"great expectations", r"soda"],
    "N05": [r"temporal", r"prefect", r"dagster"],
    "N06": [r"label studio", r"argilla", r"langfuse"],
    "N07": [r"apprise", r"novu"],
    "N08": [r"litellm", r"helicone", r"portkey"],
    "N09": [r"\blago\b", r"kill ?bill", r"flagd"],
    "N10": [r"opentelemetry", r"posthog", r"plausible"],
    "N11": [r"semgrep", r"jscodeshift", r"ast-grep"],
    "N12": [r"pgbackrest", r"restic"],
    "N13": [r"axe-core", r"pa11y"],
    "N14": [r"playwright", r"browserslist", r"\bmdn\b"],
    "N15": [r"mlflow", r"bentoml", r"kserve"],
}

# Owner "Implement:" bullets, as tolerant regex anchors. FAIL if < 70 % are present in the item block, WARN for each missing one.
ANCHORS = {
    "N01": ["tenant provision", "workspace", r"owner|admin", "invitation", "expir", r"revo(?:ke|cation)", "provider config",
            r"deployment[- ]profile", "polic", r"first[- ]upload", "resum", "idempoten", "audit", r"rollback|clean-?up",
            r"cross-tenant|tenant isolation"],
    "N02": [r"source definition|import source", "stream", "mapping", "preview", r"dry[- ]run", "validat", "rejection",
            "resum", "checkpoint", "idempoten", "provenance", "tenant", "conflict", "rollback", "usage", "audit", "legacy"],
    "N03": ["jurisdiction", "version", r"lawful|legal basis|approved basis", "announcement", "receipt", "record",
            "processing", "analysis", "export", "withdraw", r"opt-?out", "expir", "retention", "channel", "audit",
            "enforc", "outbound"],
    "N04": [r"validation metric|media validation", r"codec|container", "audio quality", "clipping", "silence", "duration",
            "channel", "confidence", "diarization", r"partial|missing", "provider fail", "invalid", "threshold", "aggregat",
            r"history|quality[- ]event", "unknown", "fabricat"],
    "N05": [r"prior result|previous result|select", "version", "immutab", "attempt", "lineage", "estimate", "authoriz",
            "approval", "idempoten", "retr", "cancel", "fenc", "checkpoint", "compar", r"accept|reject", "rollback",
            r"settle"],
    "N06": ["supervisor", "transcript", "speaker", "override", "reason code", "revision", r"consent|data[- ]right", "eligib",
            "export", "dataset", "train", "lineage", "deletion", r"silent|production training", r"unauthori[sz]ed"],
    "N07": ["preference", r"tenant polic", "channel", "event", "quiet hours", r"time ?zone", "escalat", "suppress",
            "acknowledg", "attempt", "idempoten", "fallback", "offline", "audit", r"unsubscribe|stop"],
    "N08": ["tenant", "workspace", "user", "provider", "workflow", "model", r"time[- ]window|window", "reserv", "atomic",
            "warning", "hard stop", "cancel", r"refund|release", "retry", "fallback", "override", "approval", "audit", "unknown"],
    "N09": ["plan", "contract", r"support tier|tier", r"\bSLA\b", r"bundle", "diagnostic", "feature access", "deployment",
            "expir", "grace", "suspen", "renew", "audit", "enforc", r"secret|raw[- ]content"],
    "N10": [r"feature[- ]usage", r"operation event|backend operation", "audio", "transcript", "secret", r"opt-?out", "user",
            r"deployment[- ]mode", "offline", "aggregat", "retention", r"deletion|delete", "pseudonym", "export", "audit",
            "sampling", "schema version"],
    "N11": ["inventory", "replacement", "dependenc", "migration", "compatibility", "dual", "checkpoint", "rollback", "gate",
            "forbidden", "route", r"unsafe|graph", r"retired path|evidence"],
    "N12": ["RPO", "RTO", "postgres", "storage", r"artifact|config", "encrypt", "restore", "verif", "tenant",
            r"deletion[- ]journal|tombston|reappl", r"legal[- ]hold", "credential", "drill", "partial"],
    "N13": ["error code", r"locali[sz]ed", r"field identifier|field id", r"semantic|validation", "RTL", "locale", "export",
            "evidence", "frontend"],
    "N14": ["versioned", r"upload|recording", r"codec|container", r"maximum|max", "compatibility", "negotiat", "rejection",
            r"audit|history", "frontend"],
    "N15": ["immutab", "prompt", "schema", "artifact", "checksum", r"licen[sc]e", "qualification", "deployment", "provider",
            "tenant", "active", r"staged|promotion", "rollback", r"previous|prior", "lineage", "reprocess", "audit",
            r"recovery|failure", "latest"],
}

# Owner constraint sentences that should survive into the record: (regexes that must all match, message)
CONSTRAINTS = {
    "N02": ([r"native", r"\bdlt\b", r"scale"], "prefer native Homayar or dlt unless connector scale justifies a larger platform"),
    "N03": ([r"enforcement authority|enforce"], "Homayar remains the enforcement authority (OpenFGC is a reference only)"),
    "N04": ([r"native"], "audio-specific quality logic remains native to Homayar"),
    "N05": ([r"celery", r"temporal", r"not mandatory|candidates?"], "keep Celery/outbox/lease/checkpoint; Temporal/Prefect/Dagster are candidates, not mandatory"),
    "N08": ([r"usage ledger", r"authorit"], "the Homayar usage ledger stays the authority"),
    "N09": ([r"billing.{0,120}(not|never|no).{0,80}authori"], "billing systems must not become authorization authorities"),
    "N13": ([r"frontend", r"backend"], "backend responsibilities only; no frontend change; axe-core/Pa11y are future verification"),
    "N14": ([r"frontend", r"backend"], "backend responsibilities only; no frontend change"),
    "N15": ([r"registry", r"prefer|existing"], "prefer the existing Homayar model/provider registry if it can satisfy the contract"),
}

STATUS_VOCAB = ["PLANNED", "IN_PROGRESS", "PARTIAL", "VALIDATED", "RELEASED", "BLOCKED_EXTERNAL", "NOT_RUN",
                "NEEDS_RECONCILIATION", "DEFERRED", "SUPERSEDED", "EXCLUDED"]
STATUS_RE = re.compile(r"\b(" + "|".join(STATUS_VOCAB) + r")\b")
N11_SHA = "d5bbe5d2fc794666977c436279629b48d73526c9"

# field specs: key, human name, label regex (searched in the label), min value length
FIELDS = [
    ("purpose", "purpose", r"\bpurpose\b", 12),
    ("contract", "backend contract", r"backend contract|\bcontract\b", 20),
    ("modes", "deployment modes (S/P/H/O)", r"deployment|applicab|\bmodes?\b", 8),
    ("deps", "dependencies", r"dependenc", 4),
    ("acceptance", "acceptance criteria", r"acceptance criteri", 20),
    ("evidence", "completion / acceptance evidence", r"(completion|acceptance) evidence", 12),
    ("security", "security and privacy", r"security|privacy", 12),
    ("tenant", "tenant boundary", r"tenant (boundary|isolation|scope)", 8),
    ("audit", "audit requirements", r"\baudit", 8),
    ("failure", "failure behavior", r"failure", 12),
    ("status", "status", r"^\W*status\b", 4),
    ("external", "external qualification requirements", r"external qualification|qualification req|external (prerequisite|dependenc)", 8),
    ("offline", "offline implications", r"offline|air-?gap", 12),
    ("commercial", "commercial / usage implications", r"commercial|usage", 12),
    ("candidates", "repository candidates", r"candidate|repositor", 4),
    ("linear", "Linear issue ID and URL", r"linear", 8),
]
PLACEHOLDER = re.compile(r"^\W*(tbd|todo|to be (defined|determined|decided)|n/?a|\.\.\.|-)\W*$", re.I)

ID_ALT = r"(?:GR\d{2}|RG\d{2}|[FVPACGKXND]\d{2})"
ID_EXACT = re.compile(r"^" + ID_ALT + r"$")
NID_RE = re.compile(r"\bN(?:0[1-9]|1[0-5])\b")
ANYN_RE = re.compile(r"(?<![A-Za-z0-9_])N(\d{1,4})(?![0-9A-Za-z_])")
SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
MODE_VALUES = {"Y", "L", "C", "—"}  # legend of section 3; the base uses the em dash

# --------------------------------------------------------------------------------------
# Report plumbing
# --------------------------------------------------------------------------------------


class Report:
    def __init__(self):
        self.items = []  # (cid, level, msg, detail)

    def add(self, cid, level, msg, detail=None):
        self.items.append((cid, level, msg, list(detail or [])))

    def counts(self):
        c = collections.Counter(level for _, level, _, _ in self.items)
        return c

    def failed(self):
        return any(l in ("FAIL", "MISSING") for _, l, _, _ in self.items)


# --------------------------------------------------------------------------------------
# Markdown helpers
# --------------------------------------------------------------------------------------


def norm_cell(text: str) -> str:
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\s+", " ", t).strip()


def split_row(line: str):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if c == "|":  # GFM splits on every unescaped pipe, even inside code spans
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def is_sep(line: str) -> bool:
    return "|" in line and bool(SEP_RE.match(line)) and "-" in line


def slugify(text: str) -> str:
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).strip().lower()
    t = re.sub(r"[^\w\- ]", "", t, flags=re.UNICODE)
    return t.replace(" ", "-")


def fence_mask(lines):
    mask, inside = [], False
    for l in lines:
        s = l.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            mask.append(True)
            inside = not inside
        else:
            mask.append(inside)
    return mask, inside


class Table:
    def __init__(self, start, header, sep_cols, rows, has_sep):
        self.start, self.header, self.sep_cols, self.rows, self.has_sep = start, header, sep_cols, rows, has_sep


class Doc:
    def __init__(self, path):
        raw = open(path, encoding="utf-8", newline="").read()
        self.path = path
        self.crlf = "\r" in raw
        self.final_nl = raw.endswith("\n")
        text = raw.replace("\r\n", "\n")
        lines = text.split("\n")
        if self.final_nl:
            lines.pop()
        self.lines = [l.rstrip() for l in lines]
        self.mask, self.open_fence = fence_mask(self.lines)
        self._parse()

    # -- structure ----------------------------------------------------------------------
    def _parse(self):
        L, M = self.lines, self.mask
        self.headings = []  # (idx, level, text)
        for i, l in enumerate(L):
            if M[i]:
                continue
            m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", l)
            if m:
                self.headings.append((i, len(m.group(1)), m.group(2)))
        # section number in force at every line
        self.sec = [""] * len(L)
        cur = ""
        hi = {i: t for i, _, t in self.headings}
        for i in range(len(L)):
            if i in hi:
                mm = re.match(r"^(\d+(?:\.\d+)*)[.\s]", norm_cell(hi[i]))
                if mm:
                    cur = mm.group(1)
            self.sec[i] = cur
        # tables
        self.tables = []
        i = 0
        while i < len(L):
            if not M[i] and L[i].lstrip().startswith("|"):
                j = i
                while j < len(L) and not M[j] and L[j].lstrip().startswith("|"):
                    j += 1
                run = list(range(i, j))
                header = split_row(L[run[0]])
                has_sep = len(run) > 1 and is_sep(L[run[1]])
                sep_cols = len(split_row(L[run[1]])) if has_sep else -1
                body = run[2:] if has_sep else run[1:]
                rows = [(k, split_row(L[k])) for k in body if not is_sep(L[k])]
                self.tables.append(Table(run[0], header, sep_cols, rows, has_sep))
                i = j
            else:
                i += 1
        # keys (identity of a line for the preservation rule)
        self.keys = [None] * len(L)
        in_contents, cur_f = False, None
        for i, l in enumerate(L):
            s = l.strip()
            if M[i] or not s or is_sep(l):
                continue
            m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", l)
            if m:
                lvl, txt = len(m.group(1)), norm_cell(m.group(2))
                in_contents = lvl == 2 and txt.lower() == "contents"
                mm = re.match(r"^(" + ID_ALT + r")\s*[—–-]", txt)
                if mm:
                    self.keys[i] = ("H", mm.group(1))
                    cur_f = mm.group(1)
                else:
                    mm = re.match(r"^(\d+(?:\.\d+)*)[.\s]", txt)
                    self.keys[i] = ("H", "§" + mm.group(1)) if mm else ("H", txt.lower())
                    cur_f = None
                continue
            if s.startswith("|"):
                cells = split_row(s)
                self.keys[i] = ("R", norm_cell(cells[0]) if cells else "")
                continue
            if in_contents:
                mm = re.match(r"^(\d+)\.\s+\[", s)
                if mm:
                    self.keys[i] = ("T", mm.group(1))
                    continue
            if cur_f:
                mm = re.match(r"^-\s+\*\*(.+?):\*\*", s)
                if mm:
                    self.keys[i] = ("B", cur_f, mm.group(1))

    def contents_range(self):
        start = end = None
        for idx, lvl, txt in self.headings:
            if start is None and lvl == 2 and norm_cell(txt).lower() == "contents":
                start = idx
            elif start is not None and lvl <= 2:
                end = idx
                break
        return start, end

    def range_of_h2(self, prefix):
        start = end = None
        for idx, lvl, txt in self.headings:
            if start is None and lvl == 2 and norm_cell(txt).startswith(prefix):
                start = idx
            elif start is not None and lvl <= 2:
                end = idx
                break
        if start is not None and end is None:
            end = len(self.lines)
        return start, end

    def slugs(self):
        seen, out = collections.Counter(), []
        for _, _, txt in self.headings:
            s = slugify(norm_cell(txt))
            out.append(s if seen[s] == 0 else f"{s}-{seen[s]}")
            seen[s] += 1
        return set(out), out

    def id_rows(self):
        """[(id, section, cells, lineidx)] for table rows whose first cell is an ID."""
        out = []
        for t in self.tables:
            for k, cells in t.rows:
                if cells:
                    c0 = norm_cell(cells[0])
                    if ID_EXACT.match(c0):
                        out.append((c0, self.sec[k], cells, k))
        return out


def is_subseq(a: str, b: str) -> bool:
    it = iter(b)
    return all(ch in it for ch in a)


# --------------------------------------------------------------------------------------
# Line-level preservation engine (AC5)
# --------------------------------------------------------------------------------------


def diff_engine(base: Doc, new: Doc):
    """Returns dict(lost=[...], edits=[...], added=set(new idx), moved=[...])."""
    sm = difflib.SequenceMatcher(None, base.lines, new.lines, autojunk=False)
    lost, added = [], set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            for i in range(i1, i2):
                if base.lines[i].strip() and not is_sep(base.lines[i]):
                    lost.append((i, (j1, j2) if tag == "replace" else None))
        if tag in ("insert", "replace"):
            for j in range(j1, j2):
                added.add(j)
    used, pairs, unpaired, moved = set(), [], [], []

    def ratio(a, b):
        return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()

    for i, blk in lost:
        kb = base.keys[i]
        best, best_r, in_block = None, -1.0, False
        cand = []
        if blk:
            cand = [(j, True) for j in range(blk[0], blk[1])]
        cand += [(j, False) for j in sorted(added) if not blk or not (blk[0] <= j < blk[1])]
        for j, inb in cand:
            if j in used or not new.lines[j].strip() or is_sep(new.lines[j]):
                continue
            kn = new.keys[j]
            if kb is not None:
                if kn != kb:
                    continue
            else:
                if kn is not None:
                    continue
            r = ratio(base.lines[i], new.lines[j])
            if kb is None and r < (0.6 if inb else 0.85):
                continue
            score = r + (1.0 if inb else 0.0)  # prefer a partner from the same replace block
            if score > best_r:
                best, best_r, in_block = j, score, inb
        if best is None:
            unpaired.append(i)
        else:
            used.add(best)
            pairs.append((i, best))
            if not in_block:
                moved.append((i, best))
    return {"pairs": pairs, "unpaired": unpaired, "added": added, "moved": moved, "lost": lost}


def eval_edit(old: str, new: str):
    """Decide whether new is old plus only cross-references / count updates.
    Returns (ok, kind, why)."""
    if old == new:
        return True, "SAME", ""
    old_n, new_n = collections.Counter(NID_RE.findall(old)), collections.Counter(NID_RE.findall(new))
    extra_n = new_n - old_n
    if is_subseq(old, new):
        if extra_n:
            return True, "XREF", "inserted cross-reference " + ",".join(sorted(extra_n))
        sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
        ins = "".join(new[b1:b2] for tag, a1, a2, b1, b2 in sm.get_opcodes() if tag == "insert")
        if ins and re.fullmatch(r"[\d.,\s]+", ins):
            return True, "COUNT", f"digits inserted ({ins.strip()})"
        return False, "OTHER", "insertion with no N-ID cross-reference and not a count: " + repr(ins[:120])
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    bad, counts = [], []
    for tag, a1, a2, b1, b2 in sm.get_opcodes():
        if tag == "equal":
            continue
        o, n = old[a1:a2], new[b1:b2]
        if tag == "replace" and re.fullmatch(r"[\d.,\s]+", o) and re.fullmatch(r"[\d.,\s]+", n):
            counts.append(f"{o.strip()}->{n.strip()}")
        elif tag == "insert":
            continue
        else:
            bad.append(f"{tag}:{o[:50]!r}->{n[:50]!r}")
    if bad:
        return False, "ALTERED", "existing text altered: " + "; ".join(bad[:4])
    return True, "COUNT", "count update " + ",".join(counts)


# --------------------------------------------------------------------------------------
# Field extraction from an item block
# --------------------------------------------------------------------------------------
LABEL_LINE = re.compile(r"^(\s*)(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*|__)?([A-Za-z][A-Za-z0-9 /&,()'’\-]{1,80}?)(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.*)$")


def extract_fields(block, tables_in_block):
    """block: list of raw lines. Returns list of (label, value)."""
    out = []
    n = len(block)
    i = 0
    while i < n:
        l = block[i]
        s = l.strip()
        if not s or s.startswith("|"):
            i += 1
            continue
        hm = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", l)
        if hm:
            j, buf = i + 1, []
            while j < n and not re.match(r"^#{1,6}\s+", block[j]):
                buf.append(block[j].strip())
                j += 1
            out.append((norm_cell(hm.group(2)), " ".join(b for b in buf if b)))
            i += 1
            continue
        m = LABEL_LINE.match(l) or BOLD_LABEL.match(l)
        if m and not s.lower().startswith("http"):
            indent = len(m.group(1))
            label, val = norm_cell(m.group(2)), m.group(3).strip()
            j, buf = i + 1, [val] if val else []
            while j < n:
                lj = block[j]
                sj = lj.strip()
                if not sj:
                    j += 1
                    continue
                if lj.startswith("#") or sj.startswith("|"):
                    break
                ind = len(lj) - len(lj.lstrip())
                is_item = bool(re.match(r"^\s*([-*+]|\d+[.)])\s+", lj)) or bool(LABEL_LINE.match(lj)) or bool(BOLD_LABEL.match(lj))
                if ind <= indent and is_item:
                    break
                buf.append(sj)
                j += 1
            out.append((label, " ".join(buf)))
            i = j
            continue
        i += 1
    for hdr, rows in tables_in_block:
        for cells in rows:
            for k, c in enumerate(cells):
                if k < len(hdr) and hdr[k]:
                    out.append((norm_cell(hdr[k]), c))
            if len(cells) >= 2:
                out.append((norm_cell(cells[0]), " ".join(cells[1:])))
    return out


BOLD_LABEL = re.compile(r"^(\s*)(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*|__)([^*_]{2,80}?)[.:\u2014\u2013-]?(?:\*\*|__)\s*[.:\u2014\u2013-]?\s*(.*)$")


class Block:
    def __init__(self, nid):
        self.nid = nid
        self.rows = []  # (lineidx, cells, table)
        self.heads = []  # (lineidx, level, text)
        self.paras = []  # lineidx
        self.anchors = 0
        self.lines = []  # raw lines of the union (section text + row lines)
        self.section_lines = []
        self.name = ""
        self.name_sources = []

    @property
    def text(self):
        return "\n".join(self.lines)


def find_blocks(doc: Doc):
    blocks = {n: Block(n) for n in NIDS}
    L, M = doc.lines, doc.mask
    # rows
    for t in doc.tables:
        for k, cells in t.rows:
            if not cells:
                continue
            m = re.match(r"^(N\d{2})\b", norm_cell(cells[0]))
            if m and m.group(1) in blocks:
                blocks[m.group(1)].rows.append((k, cells, t))
    # headings
    hs = doc.headings
    for hi, (idx, lvl, txt) in enumerate(hs):
        m = re.match(r"^(N\d{2})\b", norm_cell(txt))
        if m and m.group(1) in blocks:
            end = len(L)
            for idx2, lvl2, _ in hs[hi + 1:]:
                if lvl2 <= lvl:
                    end = idx2
                    break
            b = blocks[m.group(1)]
            b.heads.append((idx, lvl, txt))
            b.section_lines = L[idx:end]
    # bold-label paragraphs
    para_starts = []
    for i, l in enumerate(L):
        if M[i]:
            continue
        m = re.match(r"^\s*(?:[-*+]\s+)?(?:\*\*|__)(N\d{2})\b", l)
        if m and m.group(1) in blocks:
            para_starts.append((i, m.group(1)))
    hidx = [i for i, _, _ in hs]
    for k, (i, nid) in enumerate(para_starts):
        nxt = [j for j in hidx if j > i] + [j for j, _ in para_starts if j > i] + [len(L)]
        end = min(nxt)
        b = blocks[nid]
        b.paras.append(i)
        if not b.section_lines:
            b.section_lines = L[i:end]
    for i, l in enumerate(L):
        for m in re.finditer(r"<a\s+(?:id|name)=[\"'](n\d{2})[\"']", l, re.I):
            nid = m.group(1).upper()
            if nid in blocks:
                blocks[nid].anchors += 1
    for b in blocks.values():
        b.lines = list(b.section_lines) + [L[k] for k, _, _ in b.rows]
        parts = []
        if b.rows:
            cells = b.rows[0][1]
            parts += [norm_cell(c) for c in cells[:2]]
        if b.heads:
            parts.append(norm_cell(b.heads[0][2]))
        if b.paras and not b.heads:
            parts.append(norm_cell(L[b.paras[0]]))
        b.name = " | ".join(p for p in parts if p)
        b.name_sources = []
        if b.rows:
            b.name_sources.append(("matrix row", " ".join(norm_cell(c) for c in b.rows[0][1][:2])))
        if b.heads:
            b.name_sources.append(("heading", norm_cell(b.heads[0][2])))
        if b.paras and not b.heads:
            b.name_sources.append(("label paragraph", norm_cell(L[b.paras[0]])))
    return blocks


def block_fields(b: Block, doc: Doc):
    tabs = []
    for k, cells, t in b.rows:
        tabs.append((t.header, [cells]))
    # tables inside the section text
    if b.section_lines:
        sec = b.section_lines
        j = 0
        while j < len(sec):
            if sec[j].lstrip().startswith("|"):
                k = j
                while k < len(sec) and sec[k].lstrip().startswith("|"):
                    k += 1
                run = sec[j:k]
                if len(run) >= 2 and is_sep(run[1]):
                    hdr = split_row(run[0])
                    tabs.append((hdr, [split_row(r) for r in run[2:] if not is_sep(r)]))
                j = k
            else:
                j += 1
    return extract_fields(b.section_lines, tabs)


# --------------------------------------------------------------------------------------
# Repository helpers
# --------------------------------------------------------------------------------------


def git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout.strip()
    except Exception as e:  # noqa
        return 1, str(e)


def resolve_path(repo, tok):
    tok = tok.split(":")[0]
    for pre in ("", "Homayar/", "Homayar/backend/"):
        if os.path.exists(os.path.join(repo, pre + tok)):
            return True
    return False


PLANNED_WORDS = re.compile(r"\b(planned|new|to be created|proposed|not yet|future|target|will|would|candidate|does not exist|not exist|to be added|missing)\b", re.I)


# --------------------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------------------


def trunc(s, n=110):
    s = s.strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def check_scope(rep, args, new: Doc):
    if args.maker_repo:
        base_sha = args.base_sha
        rc1, o1 = git(args.maker_repo, "diff", "--name-only", base_sha)
        rc2, o2 = git(args.maker_repo, "ls-files", "--others", "--exclude-standard")
        rc3, o3 = git(args.maker_repo, "diff", "--name-only", "--cached")
        if rc1 or rc2 or rc3:
            rep.add("A1", "REVIEW", "could not run git in --maker-repo", [o1, o2, o3])
        else:
            changed = sorted({x for x in (o1 + "\n" + o2 + "\n" + o3).split("\n") if x})
            if changed == ["Homayar/AppVision.md"]:
                rep.add("A1", "PASS", "only Homayar/AppVision.md differs from the base (tracked, staged and untracked)")
            else:
                rep.add("A1", "FAIL", "files other than Homayar/AppVision.md changed (or nothing changed)", changed or ["<no change>"])
    else:
        rep.add("A1", "INFO", "file-ownership check skipped (no --maker-repo); run: git -C <maker-wt> diff --stat 7112407 && git -C <maker-wt> status --porcelain")
    if new.crlf:
        rep.add("A2", "FAIL", "new file contains CR characters (base is LF only)")
    elif not new.final_nl:
        rep.add("A2", "FAIL", "new file does not end with a newline (base does)")
    else:
        rep.add("A2", "PASS", "LF line endings and final newline as in the base")


def check_preservation(rep, base: Doc, new: Doc, eng):
    pairs, unpaired, moved = eng["pairs"], eng["unpaired"], eng["moved"]
    fails, accepted, review = [], [], []
    for i, j in pairs:
        old, nw = base.lines[i], new.lines[j]
        ok, kind, why = eval_edit(old, nw)
        key = base.keys[i]
        if key and key[0] == "R":
            if len(split_row(old)) != len(split_row(nw)):
                fails.append(f"L{i + 1} {key}: column count changed {len(split_row(old))}->{len(split_row(nw))}")
                continue
        if not ok:
            fails.append(f"L{i + 1} {key or 'prose'}: {why} | old: {trunc(old, 80)}")
            continue
        if re.match(r"^\s*Version:", old) or re.match(r"^\s*(Version|Recorded)\b", old):
            review.append(f"L{i + 1} version/date line edited ({kind}): {trunc(nw, 100)}")
        accepted.append(f"L{i + 1} {key or 'prose'} {kind}: {why}")
    for i in unpaired:
        fails.append(f"L{i + 1} {base.keys[i] or 'prose'}: base line removed / no replacement carrying the same ID | {trunc(base.lines[i], 90)}")
    if fails:
        rep.add("B1", "FAIL", f"{len(fails)} base line(s) removed, renumbered or altered beyond counts/cross-references", fails)
    else:
        rep.add("B1", "PASS", f"every base line is unchanged or replaced by a same-ID line that only adds an N-ID cross-reference or updates a count ({len(accepted)} edited lines)")
    if accepted:
        rep.add("B1a", "INFO", "edited existing lines the reviewer should eyeball (all passed the mechanical rule)", accepted)
    if moved:
        rep.add("B1b", "REVIEW", f"{len(moved)} line(s) moved to a different place in the file (same key)", [f"base L{i + 1} -> new L{j + 1}: {trunc(base.lines[i], 80)}" for i, j in moved])
    if review:
        rep.add("B1c", "REVIEW", "version/date line edited: not covered by AC5's allowed edit list; Foreman must accept", review)

    # B2: ID inventory + order + cell-level
    b_rows = [r for r in base.id_rows()]
    n_rows = new.id_rows()
    n_index = collections.defaultdict(list)
    for r in n_rows:
        n_index[(r[0], r[1])].append(r)
    problems = []
    for rid, sec, cells, k in b_rows:
        cand = n_index.get((rid, sec), [])
        if len(cand) != 1:
            problems.append(f"{rid} (section {sec}): expected exactly 1 row, found {len(cand)}")
            continue
        ncells = cand[0][2]
        if len(ncells) != len(cells):
            problems.append(f"{rid}: column count {len(cells)} -> {len(ncells)}")
            continue
        for ci, (bc, nc) in enumerate(zip(cells, ncells)):
            if not nc.startswith(bc):
                problems.append(f"{rid} col {ci + 1}: base cell text not preserved at the start ({trunc(bc, 40)!r} vs {trunc(nc, 40)!r})")
            else:
                resid = nc[len(bc):]
                if resid.strip() and not NID_RE.search(resid):
                    problems.append(f"{rid} col {ci + 1}: appended text carries no N-ID cross-reference: {trunc(resid, 60)!r}")
    # order of base ids preserved
    b_seq = [r[0] for r in b_rows]
    n_seq = [r[0] for r in n_rows if r[0] in set(b_seq)]
    if b_seq != n_seq:
        problems.append("relative order of the original ID rows changed")
    fh_b = [(k, base.lines[k]) for k, v in enumerate(base.keys) if v and v[0] == "H" and re.match(ID_ALT + r"$", v[1])]
    fh_n = {new.keys[k][1]: new.lines[k] for k in range(len(new.lines)) if new.keys[k] and new.keys[k][0] == "H" and re.match(ID_ALT + r"$", new.keys[k][1])}
    for k, line in fh_b:
        fid = base.keys[k][1]
        if fid not in fh_n:
            problems.append(f"section heading for {fid} missing")
        else:
            ok, kind, why = eval_edit(line, fh_n[fid])
            if not ok:
                problems.append(f"heading {fid}: {why}")
    cnt = collections.Counter()
    for rid, sec, cells, k in b_rows:
        cnt[re.match(r"[A-Z]+", rid).group(0)] += 1
    if problems:
        rep.add("B2", "FAIL", "original ID rows / F-section headings not fully preserved (F01-F95, V/P/A/C/G/K/X, GR, RG, D)", problems[:40])
    else:
        rep.add("B2", "PASS", f"all {len(b_rows)} original ID rows ({dict(cnt)}) and {len(fh_b)} F-section headings present once, same section, same order, cell text preserved")

    # B3: section headings (numbers + titles), i.e. no renumbering
    nh = [(l, norm_cell(t)) for _, l, t in new.headings]
    bh = [(l, norm_cell(t)) for _, l, t in base.headings]
    pos, miss = 0, []
    for lvl, txt in bh:
        found = False
        for q in range(pos, len(nh)):
            if nh[q][0] == lvl and nh[q][1].startswith(txt) and (nh[q][1] == txt or NID_RE.search(nh[q][1][len(txt):])):
                pos, found = q + 1, True
                break
        if not found:
            miss.append(f"h{lvl} {txt}")
    if miss:
        rep.add("B3", "FAIL", "original headings removed, renumbered, renamed or reordered", miss[:30])
    else:
        rep.add("B3", "PASS", f"all {len(bh)} original headings (numbering and titles) present in the same order")

    # B4: technology catalog section 10
    bs, be = base.range_of_h2("10.")
    ns, ne = new.range_of_h2("10.")
    if bs is None or ns is None:
        rep.add("B4", "FAIL", "section 10 (technology catalog) not found")
    else:
        b_first = [norm_cell(split_row(base.lines[k])[0]) for k in range(bs, be) if base.lines[k].lstrip().startswith("|") and not is_sep(base.lines[k])]
        n_first = collections.Counter(norm_cell(split_row(new.lines[k])[0]) for k in range(ns, ne) if new.lines[k].lstrip().startswith("|") and not is_sep(new.lines[k]))
        lost_c = [c for c in collections.Counter(b_first) if n_first[c] < collections.Counter(b_first)[c]]
        # catalog lines that changed are already judged by B1 (xref-only); here we insist on identity of the candidate set
        if lost_c:
            rep.add("B4", "FAIL", "technology catalog rows removed", lost_c[:30])
        else:
            rep.add("B4", "PASS", f"technology catalog (section 10) preserved: {len(b_first)} table rows in base, {sum(n_first.values())} in new; every base row present")
        n_sub = [norm_cell(t) for i, l, t in new.headings if ns <= i < ne]
        b_sub = [norm_cell(t) for i, l, t in base.headings if bs <= i < be]
        if [x for x in b_sub if x not in n_sub]:
            rep.add("B4h", "FAIL", "catalog sub-headings 10.1-10.12 lost", [x for x in b_sub if x not in n_sub])
        else:
            rep.add("B4h", "PASS", f"catalog sub-headings ({len(b_sub)}) preserved")

    # B5: status vocabulary + deployment-mode tables exact
    for name, prefix in (("B5s", "4."), ("B5m", "3.")):
        bs, be = base.range_of_h2(prefix)
        ns, ne = new.range_of_h2(prefix)
        if bs is None or ns is None:
            rep.add(name, "FAIL", f"section {prefix} not found")
            continue
        bl = [base.lines[k] for k in range(bs, be)]
        nl = [new.lines[k] for k in range(ns, ne)]
        sm = difflib.SequenceMatcher(None, bl, nl, autojunk=False)
        bad = [f"{tag} base[{i1 + 1}:{i2}]" for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag in ("delete", "replace")]
        if bad:
            rep.add(name, "FAIL", f"section {prefix} (status vocabulary / deployment modes) text altered", bad[:10])
        else:
            rep.add(name, "PASS", f"section {prefix} text unchanged (insertions only)")


def check_tables(rep, base: Doc, new: Doc, eng):
    def problems(doc):
        out = {}
        for t in doc.tables:
            n = len(t.header)
            if not t.has_sep:
                out[("nosep", doc.lines[t.start])] = f"L{t.start + 1}: table without a separator row"
                continue
            if t.sep_cols != n:
                out[("sepcols", doc.lines[t.start])] = f"L{t.start + 1}: separator has {t.sep_cols} columns, header {n}"
            for k, cells in t.rows:
                if len(cells) != n:
                    out[("cols", doc.lines[k])] = f"L{k + 1}: {len(cells)} cells, header has {n}: {trunc(doc.lines[k], 70)}"
                elif not doc.lines[k].strip().startswith("|") or not doc.lines[k].strip().endswith("|"):
                    out[("edge", doc.lines[k])] = f"L{k + 1}: row does not start and end with '|'"
        return out

    bp, np_ = problems(base), problems(new)
    new_bad = [v for k, v in np_.items() if k not in bp]
    if new_bad:
        rep.add("C1", "FAIL", f"{len(new_bad)} table integrity violation(s) not present in the base", new_bad[:40])
    else:
        rep.add("C1", "PASS", f"all {len(new.tables)} tables have a separator row and a constant column count (base: {len(base.tables)} tables, {len(bp)} pre-existing violations)")
    # C2: touched tables formatting (blank line before/after)
    touched, added = [], eng["added"]
    for t in new.tables:
        idxs = [t.start] + [k for k, _ in t.rows]
        if any(k in added for k in idxs):
            touched.append(t)
    fmt = []
    for t in touched:
        first, last = t.start, max([t.start] + [k for k, _ in t.rows] + [t.start + 1])
        if first > 0 and new.lines[first - 1].strip() and not new.lines[first - 1].lstrip().startswith("|"):
            fmt.append(f"L{first + 1}: table not preceded by a blank line")
        if last + 1 < len(new.lines) and new.lines[last + 1].strip() and not new.lines[last + 1].lstrip().startswith("|"):
            fmt.append(f"L{last + 1}: table not followed by a blank line")
        for k, cells in t.rows:
            if any(c == "" for c in cells[:2]):
                fmt.append(f"L{k + 1}: empty ID/name cell")
    if fmt:
        rep.add("C2", "FAIL", "touched tables are malformed (blank lines / empty cells)", fmt[:20])
    else:
        rep.add("C2", "PASS", f"{len(touched)} touched table(s) well formed (blank line before/after, no empty ID/name cells)")
    # C3: fences
    if new.open_fence:
        rep.add("C3", "FAIL", "unbalanced code fence in the new file")
    else:
        bn = sum(1 for m in base.mask if m)
        nn = sum(1 for m in new.mask if m)
        rep.add("C3", "PASS", f"code fences balanced (fenced lines: base {bn}, new {nn})")


def check_ids(rep, base: Doc, new: Doc, blocks):
    # D1 uniqueness per N item
    miss, dup, info = [], [], []
    for n in NIDS:
        b = blocks[n]
        rc, hc = len(b.rows), len(b.heads)
        pc = len(b.paras) if not hc else 0  # a bold "**Nxx**" bullet elsewhere is a mention when a heading defines the item
        if rc + hc + pc == 0:
            miss.append(n)
            continue
        if rc != 1:
            dup.append(f"{n}: {rc} matrix rows (need exactly 1)")
        if hc + pc > 1:
            dup.append(f"{n}: {hc} headings + {pc} label paragraphs (need at most 1 detail block)")
        if b.anchors > 1:
            dup.append(f"{n}: {b.anchors} HTML anchors")
        info.append(f"{n}: rows={rc} heads={hc} paras={pc}")
    if miss:
        rep.add("D1", "MISSING", f"{len(miss)}/15 N items have no definition at all", miss)
    if dup:
        rep.add("D1u", "FAIL", "N-ID defined more than once (or no matrix row)", dup)
    if not miss and not dup:
        rep.add("D1", "PASS", "each of N01..N15 is defined exactly once as a matrix row and at most once as a detail block", info)
    # D2 stray N ids in the whole new doc
    stray = []
    for i, l in enumerate(new.lines):
        for m in ANYN_RE.finditer(l):
            num = m.group(1)
            if not (len(num) == 2 and 1 <= int(num) <= 15):
                # skip ranges like N01-N15 handled by regex; skip math like N1/N2? still stray
                stray.append(f"L{i + 1}: N{num}: {trunc(l, 70)}")
    if stray:
        rep.add("D2", "FAIL", "IDs outside N01-N15 or not zero-padded (fabricated / malformed capability IDs)", stray[:20])
    else:
        rep.add("D2", "PASS", "no N-ID other than N01..N15 (two digits) appears anywhere")
    # D3 duplicates of original ids (multiplicity equal to base)
    def mult(doc):
        c = collections.Counter()
        for rid, sec, cells, k in doc.id_rows():
            c[(rid, sec)] += 1
        return c
    bm, nm = mult(base), mult(new)
    bad = [f"{k}: base {bm[k]}, new {nm.get(k, 0)}" for k in bm if nm.get(k, 0) != bm[k]]
    extra_dups = [f"{rid} (section {sec}) appears {c} times" for (rid, sec), c in nm.items() if c > 1 and not rid.startswith("N") and bm.get((rid, sec), 0) < c]
    if bad or extra_dups:
        rep.add("D3", "FAIL", "original ID row multiplicity changed / duplicated", (bad + extra_dups)[:20])
    else:
        rep.add("D3", "PASS", f"{sum(bm.values())} original ID rows each appear exactly as often as in the base")
    # D4 anchors / slugs: no new duplicate heading slugs
    bs, blist = base.slugs()
    ns, nlist = new.slugs()
    dups_b = collections.Counter(re.sub(r"-\d+$", "", s) for s in blist if re.search(r"-\d+$", s))
    dups_n = collections.Counter(re.sub(r"-\d+$", "", s) for s in nlist if re.search(r"-\d+$", s))
    newdup = [s for s in dups_n if dups_n[s] > dups_b.get(s, 0)]
    linked = set()
    for l in new.lines:
        for m in re.finditer(r"\]\(#([^)\s]+)\)", l):
            linked.add(re.sub(r"-\d+$", "", m.group(1)))
    hard = [s for s in newdup if s in linked]
    if newdup and not hard:
        rep.add("D4i", "INFO", "repeated sub-heading anchors exist but nothing links to them", newdup[:10])
        newdup = []
    else:
        newdup = hard
    if newdup:
        rep.add("D4", "FAIL", "new duplicate heading anchors (links to them become ambiguous)", newdup)
    else:
        rep.add("D4", "PASS", "no new duplicate heading anchors")


def check_hygiene(rep, base: Doc, new: Doc, eng, repo):
    added = sorted(eng["added"])
    lines = [(j, new.lines[j]) for j in added if new.lines[j].strip()]
    # A3 non-English scripts
    bad = [f"L{j + 1}: {trunc(l, 70)}" for j, l in lines if re.search(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]", l)]
    rep.add("A3", "FAIL" if bad else "PASS", "Persian/Arabic-script text in added lines (AppVision body is English-only)" if bad else "no Persian/Arabic-script text in added lines", bad[:10])
    # A4 secrets / local paths
    pat = re.compile(r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE|(?i:password|secret|token|api[_-]?key)\s*[:=]\s*[\"']?[A-Za-z0-9/+_=-]{16,})")
    paths = re.compile(r"(/root/|/home/user|/tmp/|/Users/|claude\.ai/code/session|foreman/2026)")
    bad = [f"L{j + 1}: {trunc(l, 70)}" for j, l in lines if pat.search(l)]
    rep.add("A4", "FAIL" if bad else "PASS", "credential-like strings in added lines" if bad else "no credential-like strings in added lines", bad[:10])
    bad = [f"L{j + 1}: {trunc(l, 70)}" for j, l in lines if paths.search(l)]
    rep.add("A4p", "FAIL" if bad else "PASS", "local/session paths leaked into the product document" if bad else "no local/session paths in added lines", bad[:10])
    # D5 dangling references
    universe = set()
    for rid, sec, cells, k in new.id_rows():
        universe.add(rid)
    for k, v in enumerate(new.keys):
        if v and v[0] == "H" and re.match(ID_ALT + "$", v[1]):
            universe.add(v[1])
    backlog = set()
    if repo:
        bp = os.path.join(repo, "docs", "planning", "backlog.json")
        if os.path.exists(bp):
            try:
                d = json.load(open(bp, encoding="utf-8"))

                def walk(o):
                    if isinstance(o, dict):
                        for k, v in o.items():
                            if k in ("id", "task_id") and isinstance(v, str):
                                backlog.add(v)
                            walk(v)
                    elif isinstance(o, list):
                        for v in o:
                            walk(v)
                walk(d)
            except Exception:  # noqa
                pass
    ref = re.compile(r"(?<![A-Za-z0-9_/.-])(WP\d{2}-T\d{2}|WP\d{2}|" + ID_ALT.replace("[FVPACGKXND]", "[FVPACGKXN]") + r")(?![A-Za-z0-9_])")
    dangling = []
    for j, l in lines:
        for m in ref.finditer(l):
            tok = m.group(1)
            if tok.startswith("WP"):
                if "-T" in tok:
                    if backlog and tok not in backlog:
                        dangling.append(f"L{j + 1}: {tok} not in docs/planning/backlog.json")
                elif not (1 <= int(tok[2:]) <= 24):
                    dangling.append(f"L{j + 1}: {tok} (work packages are WP01-WP24)")
                continue
            if re.fullmatch(r"P(?:[6-9]\d)", tok):
                continue  # percentile such as P95
            if tok in NIDS or tok in universe:
                continue
            if re.fullmatch(r"[FVPACGKX]\d\d", tok) and tok not in universe:
                dangling.append(f"L{j + 1}: {tok} does not exist in the document")
            elif re.fullmatch(r"(GR|RG|D)\d\d", tok) and tok not in universe:
                dangling.append(f"L{j + 1}: {tok} does not exist in the document")
    if dangling:
        rep.add("D5", "FAIL", "dangling / fabricated ID references in added lines", dangling[:25])
    else:
        rep.add("D5", "PASS", "every F/V/P/A/C/G/K/X/N/GR/RG/D/WP id cited in added lines exists")
    # G4 links
    base_slugs, _ = base.slugs()
    new_slugs, _ = new.slugs()
    link_bad = []
    for j, l in lines:
        for m in re.finditer(r"\]\(#([^)\s]+)\)", l):
            if m.group(1) not in new_slugs:
                link_bad.append(f"L{j + 1}: anchor #{m.group(1)} does not resolve")
        for m in re.finditer(r"\]\((?!https?:|#|mailto:)([^)\s]+)\)", l):
            target = m.group(1).split("#")[0]
            if target and not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(new.path), target))):
                link_bad.append(f"L{j + 1}: relative link {target} does not exist next to the document")
    if link_bad:
        rep.add("G4", "FAIL", "broken in-document/relative links in added lines", link_bad[:20])
    else:
        rep.add("G4", "PASS", "in-document anchors and relative links added by the patch resolve")
    hom_bad = sorted({f"HOM-{m}" for _, l in lines for m in re.findall(r"\bHOM-(\d+)\b", l) if int(m) > 159})
    if hom_bad:
        rep.add("D6", "WARN", "Linear ids above HOM-159 (highest known on 2026-09-30); verify each exists", hom_bad)
    urls = sorted({u for _, l in lines for u in re.findall(r"https?://[^\s)>\]|]+", l)})
    if urls:
        rep.add("G4u", "INFO", f"{len(urls)} URL(s) added; the reviewer spot-checks at least 3 (no network in the script)", urls[:60])
    # AC6 bad modes
    mode_bad = []
    negw = re.compile(r"\b(not|never|nor|no|neither|isn't|is not|without)\b", re.I)
    for j, l in lines:
        for sent in re.split(r"(?<=[.;])\s+", l):
            if re.search(r"BYOK[ -](mode|profile|deployment|tier)|\b(five|5|fifth) (deployment )?(modes|profiles)\b|\bfifth (deployment )?mode\b", sent, re.I) and not negw.search(sent):
                mode_bad.append(f"L{j + 1}: {trunc(sent, 90)}")
            if re.search(r"(offline|air-?gapped)\s*(=|is|means|equals)\s*(the same as )?on-?prem", sent, re.I) and not negw.search(sent):
                mode_bad.append(f"L{j + 1}: offline conflated with on-premises: {trunc(sent, 90)}")
    for t in new.tables:
        hdr = [norm_cell(h) for h in t.header]
        if "S" in hdr and "P" in hdr and "H" in hdr and "O" in hdr and any(h.upper().startswith("BYOK") for h in hdr):
            mode_bad.append(f"L{t.start + 1}: a BYOK column in a deployment matrix")
    if mode_bad:
        rep.add("F5", "FAIL", "BYOK treated as a deployment mode / mode conflation (owner 18.19)", mode_bad)
    else:
        rep.add("F5", "PASS", "no fifth deployment mode and no offline/on-prem conflation in added lines")
    alltext = "\n".join(l for _, l in lines)
    if re.search(r"BYOK[^.\n]{0,200}credential ownership|credential ownership[^.\n]{0,200}BYOK", alltext, re.I):
        rep.add("F5a", "PASS", "the rule 'BYOK describes credential ownership, not a fifth mode' is stated in the added text")
    else:
        rep.add("F5a", "FAIL", "added text never states that BYOK is credential ownership, not a deployment mode (SPEC AC2 / owner 18.19)")
    if re.search(r"egress[^.\n]{0,220}(fallback|retr)[^.\n]{0,220}telemetry|telemetry[^.\n]{0,220}(fallback|retr)[^.\n]{0,220}egress|(fallback|retr)[^.\n]{0,160}telemetry[^.\n]{0,160}egress", alltext, re.I):
        rep.add("F5b", "PASS", "the offline rule 'no public egress incl. fallback, retries and telemetry' is stated in the added text")
    else:
        rep.add("F5b", "FAIL", "added text never states that fully offline denies public egress including fallback, retries and telemetry (SPEC AC2 / owner 18.19)")
    if re.search(r"hybrid stage routing", alltext, re.I) and re.search(r"local AI", alltext, re.I):
        rep.add("F5c", "PASS", "on-premise routing sentence (local AI, customer BYOK, approved online providers, hybrid stage routing) present")
    else:
        rep.add("F5c", "WARN", "owner 18.19 on-premise routing sentence (local AI, customer BYOK, approved online providers, hybrid stage routing where policy permits) not found in added text")
    # sha / path evidence
    if repo:
        shas, badsha = set(), []
        for j, l in lines:
            for m in re.finditer(r"(?<![0-9A-Za-z_/.-])([0-9a-f]{7,40})(?![0-9A-Za-z_])", l):
                s = m.group(1)
                if re.search(r"\d", s) and re.search(r"[a-f]", s):
                    shas.add((s, j))
        for s, j in sorted(shas):
            rc, _ = git(repo, "cat-file", "-e", s + "^{commit}")
            if rc != 0:
                badsha.append(f"L{j + 1}: {s} is not a commit in the checkout")
        if badsha:
            rep.add("F6c", "FAIL", "cited commit SHA(s) not found (fabricated evidence, AC6)", badsha)
        else:
            rep.add("F6c", "PASS", f"{len({s for s, _ in shas})} cited commit SHA(s) all exist in the checkout")
        pbad, pwarn = [], []
        ptn = re.compile(r"(?<![A-Za-z0-9_])((?:Homayar/|backend/|docs/|infra/)[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,5}(?::\d+(?:-\d+)?)?)")
        for j, l in lines:
            for m in ptn.finditer(l):
                tok = m.group(1)
                if any(ch in tok for ch in "*{}<>"):
                    continue
                if not resolve_path(repo, tok):
                    if PLANNED_WORDS.search(l):
                        continue
                    (pbad if re.search(r"evidence|status|implemented|exists|currently|today|delivered", l, re.I) else pwarn).append(f"L{j + 1}: {tok}")
        if pbad:
            rep.add("F6p", "FAIL", "cited file path does not exist at base and the line presents it as existing evidence", pbad[:20])
        elif pwarn:
            rep.add("F6p", "WARN", "cited paths that do not exist at base; each must be labelled planned/new", pwarn[:20])
        else:
            rep.add("F6p", "PASS", "every cited repository path exists at base (or is labelled planned/new)")
    else:
        rep.add("F6c", "INFO", "no git checkout found: SHA/path evidence not verified")


def status_of(fields):
    for label, val in fields:
        if re.search(r"^\W*status\b", label, re.I) and not re.search(r"status (evidence|rule)", label, re.I):
            return label, val
    return None, None


def check_items(rep, base: Doc, new: Doc, blocks, repo, dump=False):
    found = [n for n in NIDS if blocks[n].rows or blocks[n].heads or blocks[n].paras]
    if len(found) < 15:
        rep.add("E-ALL", "MISSING", f"{15 - len(found)} of 15 N items missing", [n for n in NIDS if n not in found])
    else:
        rep.add("E-ALL", "PASS", "all 15 N items present")
    for n in NIDS:
        b = blocks[n]
        if n not in found:
            rep.add(f"E-{n}", "MISSING", f"{n} ({LINEAR_SLUG[n].split('-', 1)[1].replace('-', ' ')}) is not defined anywhere in AppVision.md")
            continue
        text = b.text
        fields = block_fields(b, new)
        if dump:
            print(f"\n===== {n} rows={len(b.rows)} heads={len(b.heads)} paras={len(b.paras)} name={b.name!r}")
            for lab, val in fields:
                print(f"  [{lab}] {trunc(val, 100)}")
        # name: every place that defines the item must carry the owner's title words
        bad_names = []
        for src, txt in b.name_sources:
            miss = [r for r in TITLE_RE[n] if not re.search(r, txt.lower())]
            if miss:
                bad_names.append(f"{src} {txt!r} lacks {miss}")
        if bad_names:
            rep.add(f"E-{n}-name", "FAIL", "name does not match the owner title: " + "; ".join(bad_names))
        else:
            rep.add(f"E-{n}-name", "PASS", f"name {b.name!r}")
        # matrix membership / modes
        if b.rows:
            k, cells, t = b.rows[0]
            hdr = [norm_cell(h) for h in t.header]
            sec = new.sec[k]
            if all(m in hdr for m in "SPHO"):
                vals = {m: norm_cell(cells[hdr.index(m)]) for m in "SPHO" if hdr.index(m) < len(cells)}
                bad = {m: v for m, v in vals.items() if v not in MODE_VALUES}
                if bad or len(vals) != 4:
                    rep.add(f"E-{n}-modes", "FAIL", f"S/P/H/O cells must each be one of Y/L/C/— (section 3 legend): {vals}")
                else:
                    rep.add(f"E-{n}-modes", "PASS", f"matrix row in section {sec}: S={vals['S']} P={vals['P']} H={vals['H']} O={vals['O']}")
                    if n in ("N07", "N10") and vals["O"] == "Y":
                        rep.add(f"E-{n}-modesO", "REVIEW", f"{n} O=Y although the capability involves outbound delivery/telemetry; confirm it is local-only in O")
            else:
                mt = {m: bool(re.search(p, text)) for m, p in (("S", r"\bS\b|saas|hosted"), ("P", r"\bP\b|on-?prem"), ("H", r"\bH\b|hybrid"), ("O", r"\bO\b|offline|air-?gap"))}
                if all(mt.values()):
                    rep.add(f"E-{n}-modes", "REVIEW", f"row is in a table without S/P/H/O columns (section {sec}); modes are described in text - reviewer confirms all four are stated individually")
                else:
                    rep.add(f"E-{n}-modes", "FAIL", f"no S/P/H/O columns and the text does not cover every mode: {mt}")
        else:
            rep.add(f"E-{n}-matrix", "FAIL", "no matrix row: the item is not added to a capability matrix")
        # required fields
        missing, prose = [], []
        for key, human, lre, minlen in FIELDS:
            if key == "modes":
                continue
            ok = False
            for lab, val in fields:
                if re.search(lre, lab, re.I) and len(val.strip()) >= minlen and not PLACEHOLDER.match(val.strip()):
                    if key == "security" and not re.search(r"privacy", lab + " " + val, re.I):
                        continue
                    ok = True
                    break
            if not ok:
                if re.search(lre.replace("^\\W*", ""), text, re.I):
                    prose.append(human)
                else:
                    missing.append(human)
        if missing:
            rep.add(f"E-{n}-fields", "FAIL", f"required field(s) absent: {', '.join(missing)}")
        if prose:
            rep.add(f"E-{n}-fieldsp", "FAIL", "field not recorded as a labelled field with content (the word occurs only in other prose). Label each field so it can be verified; a reviewer may downgrade after reading the block and logging why: " + ", ".join(prose))
        if not missing and not prose:
            rep.add(f"E-{n}-fields", "PASS", "all required fields present as labelled fields with content")
        # anchors
        anc = ANCHORS[n]
        got = [a for a in anc if re.search(a, text, re.I)]
        miss_a = [a for a in anc if a not in got]
        pct = 100 * len(got) // len(anc)
        if pct < 70:
            rep.add(f"E-{n}-anchors", "FAIL", f"only {len(got)}/{len(anc)} of the owner's 'Implement:' bullets are visible in the record ({pct} %)", miss_a)
        elif miss_a:
            rep.add(f"E-{n}-anchors", "WARN", f"{len(got)}/{len(anc)} owner bullets visible; not found: {miss_a}")
        else:
            rep.add(f"E-{n}-anchors", "PASS", f"all {len(anc)} owner bullets visible")
        # constraint
        if n in CONSTRAINTS:
            pats, msg = CONSTRAINTS[n]
            if all(re.search(p, text, re.I | re.S) for p in pats):
                rep.add(f"E-{n}-owner", "PASS", "owner constraint kept: " + msg)
            else:
                rep.add(f"E-{n}-owner", "WARN", "owner constraint not evident: " + msg)
        # candidates
        cands = CANDIDATES[n]
        if cands:
            miss_c = [c for c in cands if not re.search(c, text, re.I)]
            if miss_c:
                rep.add(f"E-{n}-cand", "FAIL", f"owner's candidate(s) not recorded: {miss_c}")
            else:
                rep.add(f"E-{n}-cand", "PASS", "all owner candidates recorded")
        else:
            cf = [v for lab, v in fields if re.search(r"candidate|repositor", lab, re.I)]
            if cf and any(re.search(r"none|native|no (external )?candidate|not applicable", v, re.I) for v in cf):
                rep.add(f"E-{n}-cand", "PASS", "no owner candidates; the record says so explicitly")
            else:
                rep.add(f"E-{n}-cand", "FAIL", "owner lists no candidate repositories for this item: the record must say 'none' explicitly (or native) instead of inventing or omitting")
        # linear
        urls = re.findall(r"https://linear\.app/homayar/issue/HOM-(\d+)(?:/([A-Za-z0-9-]+))?", text)
        own = HOM_OF[n]
        errs = []
        if not any(int(i) == own for i, _ in urls):
            errs.append(f"missing https://linear.app/homayar/issue/HOM-{own}")
        for i, slug in urls:
            if 142 <= int(i) <= 156 and int(i) != own:
                errs.append(f"URL of another N item (HOM-{i}) inside {n}'s record")
            if int(i) == own and slug and slug != LINEAR_SLUG[n]:
                errs.append(f"slug {slug!r} differs from Linear's {LINEAR_SLUG[n]!r}")
        if not re.search(rf"\bHOM-{own}\b", text):
            errs.append(f"issue id HOM-{own} not written out")
        if errs:
            rep.add(f"E-{n}-linear", "FAIL", "; ".join(errs))
        else:
            rep.add(f"E-{n}-linear", "PASS", f"HOM-{own} + URL present")
        # reuse cross-references
        if n in REUSE:
            r = REUSE[n]
            fwd = bool(re.search(rf"(?<![A-Za-z0-9]){r}(?![0-9])", text))
            rev = []
            for rid, sec, cells, k in new.id_rows():
                if rid == r:
                    line = new.lines[k]
                    if re.search(rf"\b{n}\b", line):
                        rev.append(f"row L{k + 1}")
            hk = [i for i, v in enumerate(new.keys) if v == ("H", r)]
            for i in hk:
                end = len(new.lines)
                for idx2, lvl2, _ in new.headings:
                    if idx2 > i:
                        end = idx2
                        break
                if any(re.search(rf"\b{n}\b", x) for x in new.lines[i:end]):
                    rev.append(f"section L{i + 1}")
            if fwd and rev:
                rep.add(f"E-{n}-reuse", "PASS", f"reuses {r}: forward reference present; reverse reference in {', '.join(rev)}")
            elif not fwd:
                rep.add(f"E-{n}-reuse", "REVIEW", f"SPEC mapping {n}->{r} not referenced in the record: is this a stated deviation? Foreman decides (handoff reason required)")
            else:
                rep.add(f"E-{n}-reuse", "FAIL", f"{r} row/section carries no reverse cross-reference to {n} (AC1 'cross-referenced both ways')")
        else:
            others = sorted(set(re.findall(r"\b(?:[FVXKGAC]\d{2})\b", text)))
            rep.add(f"E-{n}-reuse", "INFO", f"new entry (no reused ID in the SPEC mapping); related IDs cited: {', '.join(others[:12]) or 'none'}")
        # offline text
        off = " ".join(v for lab, v in fields if re.search(r"offline|air-?gap", lab, re.I))
        if off and not re.search(r"egress|public (network|internet)|outbound|air-?gap|local|internal|file|denied|unavailable|—", off, re.I):
            rep.add(f"E-{n}-offline", "WARN", "offline implications field does not say what happens to public egress in mode O")
        if n == "N08" and not re.search(r"fallback|retr", off or text, re.I):
            rep.add(f"E-{n}-offlineb", "WARN", "N08 offline text does not mention fallback/retry egress")
        if n == "N10" and not re.search(r"telemetry", off or text, re.I):
            rep.add(f"E-{n}-offlineb", "WARN", "N10 offline text does not mention telemetry")
        # status rules (AC3 / AC6)
        check_status(rep, n, b, fields, new, repo)
        # claims scan
        sents = re.split(r"(?<=[.;])\s+|\n|\|", text)
        neg = re.compile(r"\b(not|no|nothing|none|never|without|until|before|unless|must|require[sd]?|planned|target|intended|if|when|once|only after|cannot|to be|will|should|needs?|awaiting|pending|blocked|unverified|design|acceptance|criteria|evidence)\b", re.I)
        strong = re.compile(r"\b(production[- ]ready|fully implemented|already implemented|is implemented|are implemented|now (enabled|live|released|licensed)|is (licensed|enabled|released|deployed|in production)|are (licensed|enabled|released|deployed))\b", re.I)
        sfail = [trunc(s, 120) for s in sents if strong.search(s) and not neg.search(s)]
        if sfail and n != "N11":
            rep.add(f"E-{n}-claim", "FAIL", "unsupported implemented/enabled/licensed/production-ready claim (AC6, AppVision line 7)", sfail[:5])
        weak = re.compile(r"\b(implemented|delivered|shipped|deployed|validated|verified|passing|passed|green|released|done)\b", re.I)
        swarn = [trunc(s, 120) for s in sents if weak.search(s) and not neg.search(s)]
        if swarn:
            rep.add(f"E-{n}-claimw", "REVIEW" if n == "N11" else "WARN", "sentences with delivery/validation wording; reviewer checks each against source evidence", swarn[:5])
        nums = re.findall(r"\b\d+ (?:passed|tests?|failed)\b|CI run \d{6,}|run[_ ]id[: ]+\d{6,}", text, re.I)
        if nums:
            rep.add(f"E-{n}-num", "REVIEW", "numeric test/CI evidence cited; verify each number against a recorded run", nums[:5])


def check_status(rep, n, b, fields, new, repo):
    label, val = status_of(fields)
    text = b.text
    if val is None:
        rep.add(f"E-{n}-status", "FAIL", "no Status field/column with a value")
        return
    clean = re.sub(r"\b(?:not|never|nor|neither|no|without)\b[^.;,]*", " ", val, flags=re.I)
    toks = STATUS_RE.findall(clean)
    if not toks:
        rep.add(f"E-{n}-status", "FAIL", f"status value {trunc(val, 60)!r} contains no token from the section 4 vocabulary ({', '.join(STATUS_VOCAB)})")
        return
    primary = toks[0]
    others = sorted(set(toks) - {primary})
    ev_text = val + " " + " ".join(v for lab, v in fields if re.search(r"status (evidence|note|rule)|evidence", lab, re.I))
    has_path = bool(re.search(r"(Homayar/|backend/|docs/)[\w./\-]+\.\w+", ev_text))
    has_sha = bool(re.search(r"\b[0-9a-f]{7,40}\b", ev_text) and re.search(r"\bcommit\b|\b[0-9a-f]{7,40}\b", ev_text))
    if n != "N11":
        if primary == "PLANNED" and not (set(others) - {"PLANNED"}):
            rep.add(f"E-{n}-status", "PASS", "PLANNED (documentation-only addition, owner 18.16)")
        elif primary in ("VALIDATED", "RELEASED") or set(others) & {"VALIDATED", "RELEASED"}:
            rep.add(f"E-{n}-status", "FAIL", f"{n} is not delivered at base 7112407 (only N11 is): {primary} {others} is unsupported (AC3/AC6)")
        elif primary == "PARTIAL" and (has_path or has_sha) and re.search(r"gap|missing|remaining|not yet|not met|absent|lacks?", ev_text, re.I):
            rep.add(f"E-{n}-status", "REVIEW", "PARTIAL claimed: cites source evidence and states gaps; reviewer must verify each cited file:line against the base checkout and that the specific acceptance criteria are really met")
        elif primary == "IN_PROGRESS":
            rep.add(f"E-{n}-status", "FAIL", "IN_PROGRESS requires a named owner and branch (section 4); none is running for this item")
        else:
            rep.add(f"E-{n}-status", "FAIL", f"{primary}: allowed only with cited source evidence (file path or commit) AND recorded gaps; otherwise the item is PLANNED (SPEC AC3, owner 18.16)")
        return
    # N11
    problems = []
    if primary in ("PLANNED",):
        problems.append("N11 has delivered evidence (commit d5bbe5d): PLANNED under-reports it (AC3 requires the delivered evidence to be recorded with an exact status)")
    if primary == "RELEASED" or "RELEASED" in others:
        problems.append("RELEASED claimed: not merged to main, CI blocked, release gates (RG01-RG10) not run")
    if primary == "VALIDATED":
        rep.add("E-N11-status-v", "REVIEW", "VALIDATED claimed for N11: allowed only if scoped to an exact revision, mode and environment (section 4) AND the record states CI was not run/blocked and the merge did not happen; reviewer adjudicates")
    if primary not in ("PARTIAL", "BLOCKED_EXTERNAL", "VALIDATED", "PLANNED", "RELEASED") :
        problems.append(f"N11 status {primary}: expected PARTIAL (or BLOCKED_EXTERNAL for the CI/merge item)")
    if not re.search(r"d5bbe5d", text):
        problems.append("commit d5bbe5d not cited")
    else:
        for m in re.finditer(r"\bd5bbe5d[0-9a-f]*\b", text):
            if not N11_SHA.startswith(m.group(0)):
                problems.append(f"cited SHA {m.group(0)} is not a prefix of {N11_SHA}")
    if not re.search(r"claude/inspiring-tesla-ie06gi|integration branch", text, re.I):
        problems.append("integration branch (claude/inspiring-tesla-ie06gi) not named")
    if not re.search(r"(not|never|un)[- ]?merged|not (yet )?(been )?merged|(has|have) not been merged|\bnot on main\b|main (does not|doesn'?t) (yet )?(contain|include)", text, re.I):
        problems.append("does not state that the commit is not merged to main")
    if not re.search(r"\bCI\b[^.\n]{0,160}(block|unavailable|not run|billing|spending|fail)|(block|unavailable|billing|spending)[^.\n]{0,160}\bCI\b|Actions[^.\n]{0,120}(billing|spending|block)", text, re.I):
        problems.append("does not state that CI is blocked (GitHub Actions billing/spending limit)")
    if re.search(r"\b(merged (in)?to main|is on main|in production|deployed|released)\b(?![^.\n]{0,40}\bnot\b)", re.sub(r"\b(not|never|un)[- ]?merged[^.\n]*", "", text, flags=re.I), re.I):
        rep.add("E-N11-claims", "REVIEW", "N11 text contains merged/deployed/released wording: confirm it is negated or scoped")
    if re.search(r"HOM-152[^.\n]{0,60}\b(Done|Closed|Completed)\b|\b(Done|Closed|Completed)\b[^.\n]{0,40}HOM-152", text):
        problems.append("Linear HOM-152 is In Progress (not Done); the record must not say otherwise")
    if len(val.strip()) < 40:
        problems.append("status has no 'why' (exact status and reason are required by AC3)")
    if not re.search(r"legacy-retirement\.md", text):
        rep.add("E-N11-evd", "WARN", "docs/architecture/legacy-retirement.md (the per-path retirement evidence delivered by d5bbe5d) is not cited")
    if problems:
        rep.add("E-N11-status", "FAIL", "; ".join(problems))
    else:
        rep.add("E-N11-status", "PASS" if primary in ("PARTIAL", "BLOCKED_EXTERNAL") else "REVIEW", f"N11 status {primary} {others or ''} with commit, branch, not-merged and CI-blocked statements")


def check_counts_toc(rep, base: Doc, new: Doc, blocks):
    # G1 coverage statements in section 12
    s, e = new.range_of_h2("12.")
    sec12 = "\n".join(new.lines[s:e]) if s is not None else ""
    bs, be = base.range_of_h2("12.")
    base12 = "\n".join(base.lines[bs:be])
    added12 = [l for l in new.lines[s:e] if l not in set(base.lines[bs:be])]
    txt12 = "\n".join(added12)
    if s is None:
        rep.add("G1", "FAIL", "section 12 not found")
        return
    # the completeness-scope paragraph (12.2) is where the coverage counts live
    c22 = None
    for k, (idx, lvl, txt) in enumerate(new.headings):
        if s <= idx < e and norm_cell(txt).startswith("12.2"):
            nxt = next((i2 for i2, _, _ in new.headings[k + 1:] if i2 > idx), e)
            c22 = new.lines[idx:nxt]
    if c22 is None:
        rep.add("G1", "FAIL", "section 12.2 (completeness scope / coverage counts) not found")
    elif re.search(r"N01[^\n]{0,24}N15|15 (approved|missing)", "\n".join(c22)):
        rep.add("G1", "PASS", "section 12.2 coverage statement includes the 15 N items")
    else:
        rep.add("G1", "FAIL", "section 12.2 (coverage counts) was not updated to include the 15 N items (N01-N15 / '15 approved ...')")
    if re.search(r"N01[^\n]{0,24}N15|15 (approved|missing)", txt12) and not re.search(r"N01[^\n]{0,24}N15", "\n".join(l for l in added12 if "12.4" in l or "Structural verification" in l)):
        rep.add("G1b", "WARN", "12.4 verification record does not mention the N items (its 'present once in their matrices' claim now understates the document)")
    # recounts
    cnt = collections.Counter()
    for rid, sec, cells, k in new.id_rows():
        pre = re.match(r"[A-Z]+", rid).group(0)
        if pre in "VPACGKX" and sec.startswith("6"):
            cnt["expanded"] += 1
        elif pre == "N" and sec.startswith("6"):
            cnt["n_in_6"] += 1
        elif pre == "N":
            cnt["n_other"] += 1
        elif pre == "F" and sec.startswith("11.2"):
            cnt["F"] += 1
        elif pre == "GR":
            cnt["GR"] += 1
        elif pre == "RG":
            cnt["RG"] += 1
        elif pre == "D":
            cnt["D"] += 1
    stated = []
    for m in re.finditer(r"\b(\d+)\s+(expanded capability IDs|historical feature IDs|guardrail IDs|guardrails|release-gate IDs|release gates|decision IDs|unresolved decision records|baseline features)", sec12):
        stated.append((int(m.group(1)), m.group(2), m.start()))
    bad = []
    exp_map = {"expanded capability IDs": {cnt["expanded"]}, "historical feature IDs": {cnt["F"]}, "guardrail IDs": {cnt["GR"]}, "guardrails": {cnt["GR"]},
               "release-gate IDs": {cnt["RG"]}, "release gates": {cnt["RG"]}, "decision IDs": {cnt["D"]}, "unresolved decision records": {cnt["D"]}, "baseline features": {cnt["F"]}}
    if cnt["n_in_6"]:
        exp_map["expanded capability IDs"].add(cnt["expanded"] + cnt["n_in_6"])
    for num, what, pos in stated:
        line = sec12[max(0, sec12.rfind("\n", 0, pos)): sec12.find("\n", pos)]
        ok = num in exp_map[what]
        if what == "expanded capability IDs" and not ok and num == cnt["expanded"] and re.search(r"N01|15 approved", line):
            ok = True  # explicit separate statement of the N items in the same line
        if not ok:
            bad.append(f"'{num} {what}' but the document recount is {sorted(exp_map[what])}")
    if bad:
        rep.add("G1c", "FAIL", "stated coverage counts disagree with a recount of the document", bad)
    else:
        rep.add("G1c", "PASS", f"stated counts agree with the recount (F={cnt['F']}, expanded V/P/A/C/G/K/X={cnt['expanded']}, GR={cnt['GR']}, RG={cnt['RG']}, D={cnt['D']}, N rows in section 6={cnt['n_in_6']}, N rows elsewhere={cnt['n_other']})")
    if re.search(r"N01[^\n]{0,20}N15|approved (missing )?backend capabilit", "\n".join(new.lines[s:e]), re.I):
        pass
    # TOC
    cs, ce = new.contents_range()
    bcs, bce = base.contents_range()
    if cs is None:
        rep.add("G2", "FAIL", "Contents section not found")
        return
    toc_new = new.lines[cs:ce]
    toc_base = base.lines[bcs:bce]
    slugs, _ = new.slugs()
    unresolved, entries = [], []
    for l in toc_new:
        m = re.match(r"^\s*(?:\d+\.|[-*])\s+\[(.+?)\]\(#([^)]+)\)", l)
        if m:
            entries.append((m.group(1), m.group(2)))
            if m.group(2) not in slugs:
                unresolved.append(f"{trunc(l, 80)} -> #{m.group(2)} does not resolve")
    if toc_new == toc_base:
        rep.add("G2", "FAIL", "Contents block is identical to the base: the TOC was not updated for the N items (AC4)")
    elif unresolved:
        rep.add("G2", "FAIL", "Contents entries do not resolve to headings", unresolved)
    elif not any(re.search(r"approved|N01|backend capabilit", t, re.I) for t, _ in entries):
        rep.add("G2", "WARN", "Contents changed but no entry names the approved backend capabilities / N01-N15")
    else:
        rep.add("G2", "PASS", "Contents updated, every entry resolves, an entry names the N items")
    nums = [int(x) for x in re.findall(r"^\s*(\d+)\.\s+\[", "\n".join(toc_new), re.M)]
    if nums != list(range(1, len(nums) + 1)):
        rep.add("G2n", "FAIL", f"Contents numbering is not 1..n sequential: {nums}")
    else:
        rep.add("G2n", "PASS", f"Contents numbering sequential 1..{len(nums)}")
    # consistency of the blanket status sentences
    line237 = [i for i, l in enumerate(new.lines) if l.startswith("Each row is an independently trackable acceptance unit.")]
    if line237 and blocks["N11"].rows:
        rep.add("G5", "REVIEW", "section 6 intro says 'All rows are PLANNED / current implementation unverified'; if N rows sit in a section 6 matrix and N11 is not PLANNED, the sentence must be qualified (insertion only)", [trunc(new.lines[line237[0]], 160)])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("new")
    ap.add_argument("base")
    ap.add_argument("--repo")
    ap.add_argument("--maker-repo")
    ap.add_argument("--base-sha", default="7112407")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dump-blocks", action="store_true")
    ap.add_argument("-q", action="store_true")
    args = ap.parse_args()
    base_path = args.base
    if os.path.isdir(base_path):
        base_path = os.path.join(base_path, "Homayar", "AppVision.md")
    for p in (args.new, base_path):
        if not os.path.isfile(p):
            print(f"error: {p} is not a file", file=sys.stderr)
            return 2
    args.base = base_path
    repo = args.repo
    if not repo:
        rc, top = git(os.path.dirname(os.path.abspath(base_path)), "rev-parse", "--show-toplevel")
        repo = top if rc == 0 else None
    try:
        base, new = Doc(base_path), Doc(args.new)
    except Exception as e:  # noqa
        print(f"error: cannot parse: {e}", file=sys.stderr)
        return 2
    rep = Report()
    check_scope(rep, args, new)
    eng = diff_engine(base, new)
    check_preservation(rep, base, new, eng)
    check_tables(rep, base, new, eng)
    blocks = find_blocks(new)
    check_ids(rep, base, new, blocks)
    check_hygiene(rep, base, new, eng, repo)
    check_items(rep, base, new, blocks, repo, dump=args.dump_blocks)
    check_counts_toc(rep, base, new, blocks)

    if args.json:
        print(json.dumps([{"id": c, "level": l, "msg": m, "detail": d} for c, l, m, d in rep.items], indent=1))
    else:
        order = {"MISSING": 0, "FAIL": 1, "REVIEW": 2, "WARN": 3, "INFO": 4, "PASS": 5}
        print(f"NEW  = {args.new}\nBASE = {base_path}\nREPO = {repo}\n")
        for cid, lvl, msg, det in rep.items:
            if args.q and lvl in ("PASS", "INFO"):
                continue
            print(f"[{lvl:7}] {cid}: {msg}")
            for d in det[:25]:
                print(f"            - {d}")
        c = rep.counts()
        found = [n for n in NIDS if blocks[n].rows or blocks[n].heads or blocks[n].paras]
        print("\nSUMMARY " + " ".join(f"{k}={c.get(k, 0)}" for k in ("PASS", "INFO", "WARN", "REVIEW", "FAIL", "MISSING")))
        print(f"N items present: {len(found)}/15; missing: {', '.join(n for n in NIDS if n not in found) or 'none'}")
        pres = [i for i in rep.items if i[0].startswith("B")]
        print("Preservation checks (B*): " + ("ALL PASS" if all(l in ("PASS", "INFO", "REVIEW", "WARN") for _, l, _, _ in pres) else "FAILURES"))
        print("VERDICT: " + ("FAIL" if rep.failed() else "PASS (reviewer must still clear every REVIEW/WARN)"))
    return 1 if rep.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
