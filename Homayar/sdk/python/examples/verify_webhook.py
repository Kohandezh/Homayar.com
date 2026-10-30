"""Receive Homayar webhooks: verify the signature, then drop duplicates.

Usage:

    export HOMAYAR_WEBHOOK_SECRETS=whk_...:whsec_...   # key_id:secret, comma separated
    python verify_webhook.py --port 8080

During a key rotation, list both keys until the old one expires:

    export HOMAYAR_WEBHOOK_SECRETS=whk_new:whsec_new,whk_old:whsec_old

The server uses only the standard library. Put it behind your HTTPS proxy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from homayar_client import WebhookDeduplicator, WebhookVerificationError, verify_webhook

MAX_BODY_BYTES = 1024 * 1024


def parse_secrets(value: str) -> dict[str, str]:
    """``key_id:secret`` entries, comma separated."""
    secrets: dict[str, str] = {}
    for entry in filter(None, (part.strip() for part in value.split(","))):
        key_id, _, secret = entry.partition(":")
        if not key_id or not secret:
            raise ValueError("HOMAYAR_WEBHOOK_SECRETS entries must be key_id:secret")
        secrets[key_id] = secret
    return secrets


def process(event: dict[str, Any]) -> None:
    """Your work goes here. Keep it fast: queue slow work and answer at once."""
    print(f"event {event['id']} type {event['type']}")


def handle(
    headers: Any,
    body: bytes,
    *,
    secrets: Mapping[str, str],
    deduplicator: WebhookDeduplicator,
    now: float | None = None,
) -> tuple[int, dict[str, Any]]:
    """Return the HTTP status and JSON answer for one request.

    Verify first: an unverified request must not touch the deduplicator, or a
    forged request could block the real event. A duplicate gets 2xx, so
    Homayar stops sending it, and does nothing.
    """
    try:
        event = verify_webhook(headers=headers, body=body, secrets=secrets, now=now)
    except WebhookVerificationError as exc:
        return 400, {"error": exc.code}
    if deduplicator.seen_before(event["id"], now=now):
        return 200, {"status": "duplicate"}
    # In a real receiver, record the id and do the work in one database
    # transaction, so a crash in between cannot lose or repeat the work.
    process(event)
    return 200, {"status": "processed"}


def make_handler(secrets: Mapping[str, str], deduplicator: WebhookDeduplicator):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the http.server method name
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY_BYTES:
                self._answer(413, {"error": "body_too_large"})
                return
            body = self.rfile.read(length)  # the raw bytes, never re-encoded
            status, answer = handle(self.headers, body, secrets=secrets, deduplicator=deduplicator)
            self._answer(status, answer)

        def _answer(self, status: int, answer: dict[str, Any]) -> None:
            data = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    secrets = parse_secrets(os.environ.get("HOMAYAR_WEBHOOK_SECRETS", ""))
    if not secrets:
        print("Set HOMAYAR_WEBHOOK_SECRETS to key_id:secret", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(secrets, WebhookDeduplicator())
    )
    print(f"Listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
