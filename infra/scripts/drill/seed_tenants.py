"""Seed N drill tenants for a restore drill (HOM-118) — drill hosts only.

Runs the demo workspace seed (``backend.database.demo_seed``: verified
accounts per role, ~30 meetings processed by the real job processor with
scripted providers, transcripts, analysis, share links, API key, audit) once
per tenant with a distinct organization slug and e-mail domain, and with a
higher sample rate so the recordings carry a realistic byte volume.

Usage (inside the API image, ENVIRONMENT=development, on the drill project):
    python /drill/seed_tenants.py --tenants 4 --sample-rate 48000

The demo seed refuses every environment except development/test, so this
can never touch a production deployment by accident.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenants", type=int, default=4)
    parser.add_argument("--sample-rate", type=int, default=48_000)
    args = parser.parse_args(argv)

    from backend.config import Settings
    from backend.database import demo_seed

    settings = Settings()
    demo_seed.SAMPLE_RATE_HZ = args.sample_rate
    tenants = []
    for index in range(args.tenants):
        demo_seed.DEMO_ORGANIZATION_SLUG = f"drill-org-{index:02d}"
        demo_seed.DEMO_EMAIL_DOMAIN = f"t{index:02d}.drill.example.com"
        demo_seed.DEMO_WORKSPACE_NAME = f"Drill tenant {index:02d}"
        result = demo_seed.seed_demo_workspace(settings)
        tenants.append(
            {
                "index": index,
                "workspace_id": result.workspace_id,
                "organization_id": result.organization_id,
                "owner": result.accounts["owner"].email,
                "meetings": result.meeting_count,
            }
        )
        print(f"seeded tenant {index}: {result.meeting_count} meetings", file=sys.stderr)
    print(json.dumps({"tenants": tenants, "password": demo_seed.DEMO_PASSWORD}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
