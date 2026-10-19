"""Upload a recording, wait for its job, and print the summary.

Usage:

    export HOMAYAR_BASE_URL=https://api.example.com
    export HOMAYAR_API_KEY=hk_...        # scopes: meeting:create and meeting:read
    python upload_and_wait.py path/to/call.wav

To rerun safely after a crash, set the key the first run printed:

    export HOMAYAR_IDEMPOTENCY_KEY=sdk-...
    python upload_and_wait.py path/to/call.wav
"""

from __future__ import annotations

import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

from homayar_client import HomayarAPIError, HomayarClient, new_idempotency_key


def run(
    client: HomayarClient, audio_path: Path, *, idempotency_key: str | None = None
) -> dict[str, Any]:
    """Upload ``audio_path``, wait for the job, print and return the summary.

    Without ``idempotency_key`` a new key is made. The key is printed: a rerun
    with that same key and file gets the first upload back, not a second job.
    """
    idempotency_key = idempotency_key or new_idempotency_key()
    print(f"Uploading {audio_path.name} (Idempotency-Key: {idempotency_key})")
    accepted = client.upload_recording(
        audio_path.read_bytes(),
        filename=audio_path.name,
        content_type=mimetypes.guess_type(audio_path.name)[0],
        idempotency_key=idempotency_key,
    )
    meeting_id = accepted["meeting"]["id"]
    print(
        f"Accepted: meeting {meeting_id}, job {accepted['job']['id']}, "
        f"replayed: {accepted['idempotent_replay']}"
    )

    job = client.wait_for_job(meeting_id)
    print(f"Job state: {job['state']}")
    if job["state"] != "completed":
        raise RuntimeError(f"Job ended as {job['state']}: {job.get('failure_code')}")

    summary = client.get_summary(meeting_id)
    print(f"Summary: {summary['overview']}")
    return summary


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python upload_and_wait.py <audio file>", file=sys.stderr)
        return 2
    base_url = os.environ.get("HOMAYAR_BASE_URL")
    api_key = os.environ.get("HOMAYAR_API_KEY")
    if not base_url or not api_key:
        print("Set HOMAYAR_BASE_URL and HOMAYAR_API_KEY first.", file=sys.stderr)
        return 2
    with HomayarClient(base_url, api_key) as client:
        try:
            run(
                client,
                Path(argv[1]),
                idempotency_key=os.environ.get("HOMAYAR_IDEMPOTENCY_KEY") or None,
            )
        except HomayarAPIError as error:
            print(f"API error {error}, request id {error.request_id}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
