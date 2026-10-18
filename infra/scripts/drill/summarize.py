"""Turn a drill's evidence directory into the measured RPO/RTO summary (HOM-118).

Every number comes from recorded timestamps (drill-timeline.log, the
restore timeline, the manifest and the sealed reconciliation report); nothing
is estimated from a script's own duration.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

RPO_TARGET_SECONDS = 24 * 3600
RTO_TARGET_SECONDS = 8 * 3600


def _timeline(path: Path) -> dict[str, float]:
    marks: dict[str, float] = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 4:
            marks[f"{parts[2]}:{parts[3]}"] = float(parts[1])
    return marks


def _restore_phases(path: Path) -> dict[str, float]:
    starts: dict[str, datetime] = {}
    phases: dict[str, float] = {}
    for line in path.read_text().splitlines():
        stamp, phase, event = line.split()[:3]
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if event == "START":
            starts[phase] = moment
        elif phase in starts:
            phases[phase] = (moment - starts[phase]).total_seconds()
    return phases


def _restore_marks(path: Path) -> dict[str, datetime]:
    marks: dict[str, datetime] = {}
    for line in path.read_text().splitlines():
        stamp, phase, event = line.split()[:3]
        marks.setdefault(f"{phase}:{event}", datetime.fromisoformat(stamp.replace("Z", "+00:00")))
    return marks


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def main(evidence: str) -> int:
    root = Path(evidence)
    marks = _timeline(root / "drill-timeline.log")
    manifest = json.loads((root / "manifest.json").read_text())
    report = json.loads((root / "reconciliation-report.json").read_text())
    smoke = json.loads((root / "smoke.json").read_text())
    checks = report.get("checks", {})
    tombstones = checks.get("tombstones", {}).get("details", {})
    restore_marks = _restore_marks(root / "restore-timeline.log")
    probes_file = root / "gate-probes.jsonl"
    probes = (
        [json.loads(line) for line in probes_file.read_text().splitlines() if line.strip()]
        if probes_file.exists()
        else None
    )
    snapshot = datetime.fromisoformat(manifest["created_at"]).timestamp()
    t0 = marks["recovery_decision:T0"]
    ready = marks["recovery:READY"]
    failure = marks["failure:START"]
    rto = ready - t0
    loss_window = failure - snapshot
    summary = {
        "backup_id": manifest["backup_id"],
        "data": {
            "tenants": manifest["tenants"],
            "database_rows": manifest["database"]["rows"],
            "tables": manifest["database"]["tables"],
            "objects": manifest["stores"]["objects"]["objects"],
            "object_bytes": manifest["stores"]["objects"]["bytes"],
            "set_bytes_encrypted": manifest["size_bytes"],
        },
        "backup": {
            "start": manifest["started_at"],
            "end": manifest["finished_at"],
            "snapshot_time": manifest["created_at"],
            "tool_seconds": manifest["durations_seconds"],
            "backup_sh_seconds": round(marks["backup:END"] - marks["backup:START"], 3),
            "offsite_s3_seconds": round(marks["offsite_s3:END"] - marks["offsite_s3:START"], 3),
        },
        "recovery": {
            "failure_at": _utc(failure),
            "t0_decision": _utc(t0),
            "fetch_seconds": round(marks["fetch:END"] - marks["fetch:START"], 3),
            "restore_isolated_seconds": round(
                marks["restore_isolated:END"] - marks["restore_isolated:START"], 3
            ),
            "restore_phases_seconds": _restore_phases(root / "restore-timeline.log"),
            "reconciliation_seconds": report["timings_seconds"],
            "ingress_seconds": round(marks["ingress:END"] - marks["ingress:START"], 3),
            "smoke_seconds": round(marks["smoke:END"] - marks["smoke:START"], 3),
            "ready_at": _utc(ready),
        },
        "reconciliation_status": report["status"],
        "reconciliation_statuses": report["statuses"],
        "smoke_passed": smoke["passed"],
        "smoke_checks": smoke["checks"],
        "smoke_observations": smoke.get("observations", {}),
        "restore_gate": {
            "staging_state_before_reconcile": (
                checks.get("marker", {}).get("details", {}).get("state")
            ),
            "reconcile_end_before_promote_start": (
                "reconcile:END" in restore_marks
                and "promote:START" in restore_marks
                and restore_marks["reconcile:END"] <= restore_marks["promote:START"]
            ),
            "tombstone_replay": tombstones.get("replay"),
            "tombstone_verify": tombstones.get("verify"),
            "journal_records": tombstones.get("journal_records"),
            "journal_records_at_backup": tombstones.get("journal_records_at_backup"),
            "probes": probes,
            "probes_all_as_expected": bool(probes) and all(p["ok"] for p in probes),
        },
        "non_resurrection": checks.get("non_resurrection", {}).get("details"),
        "post_backup_state": checks.get("post_backup_state", {}).get("details"),
        "achieved_rto_seconds": round(rto, 3),
        "rto_target_seconds": RTO_TARGET_SECONDS,
        "rto_met": rto <= RTO_TARGET_SECONDS and smoke["passed"] and report["status"] == "PASS",
        "data_loss_window_seconds": round(loss_window, 3),
        "rpo_target_seconds": RPO_TARGET_SECONDS,
        "rpo_note": (
            "measured window = failure time - snapshot time of the last backup; with the daily "
            "schedule the worst case is the schedule interval plus one backup duration"
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
