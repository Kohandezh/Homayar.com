"""Turn a drill's evidence directory into the measured RPO/RTO summary (HOM-118).

Every number comes from recorded timestamps (drill-timeline.log, the
restore timeline, the manifest and the sealed reconciliation report); nothing
is estimated from a script's own duration.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
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


def main(evidence: str) -> int:
    root = Path(evidence)
    marks = _timeline(root / "drill-timeline.log")
    manifest = json.loads((root / "manifest.json").read_text())
    report = json.loads((root / "reconciliation-report.json").read_text())
    smoke = json.loads((root / "smoke.json").read_text())
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
            "t0_decision": datetime.fromtimestamp(t0).astimezone().isoformat(),
            "fetch_seconds": round(marks["fetch:END"] - marks["fetch:START"], 3),
            "restore_isolated_seconds": round(
                marks["restore_isolated:END"] - marks["restore_isolated:START"], 3
            ),
            "restore_phases_seconds": _restore_phases(root / "restore-timeline.log"),
            "reconciliation_seconds": report["timings_seconds"],
            "ingress_seconds": round(marks["ingress:END"] - marks["ingress:START"], 3),
            "smoke_seconds": round(marks["smoke:END"] - marks["smoke:START"], 3),
            "ready_at": datetime.fromtimestamp(ready).astimezone().isoformat(),
        },
        "reconciliation_status": report["status"],
        "reconciliation_statuses": report["statuses"],
        "smoke_passed": smoke["passed"],
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
