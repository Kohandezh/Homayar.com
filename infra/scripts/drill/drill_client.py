"""HTTP side of a restore drill (HOM-118): post-backup changes and smoke tests.

Standard library only; talks to the deployment through its public origin
(nginx), exactly as a browser would (session cookie + CSRF header).

    python drill_client.py mutate --base http://127.0.0.1:18080 --tenants tenants.json > changes.json
    python drill_client.py smoke  --base http://127.0.0.1:18081 --tenants tenants.json --changes changes.json

``mutate`` (after the backup): deletes two conversations of tenant 0 and one
of tenant 1 (journaled tombstones), revokes one share link of tenant 1 (a
post-backup revocation the restore cannot replay) and uploads one new
recording to tenant 0 (data inside the RPO window, lost by design).
``smoke`` (on the restored system): every owner signs in and lists its
conversations; deleted ones answer 404; another tenant's conversation
answers 404; a kept recording is served; the post-backup upload is absent.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import io
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )
        self.workspace_id = ""

    def _csrf(self) -> str:
        return next((c.value for c in self.jar if c.name == "homayar_csrf"), "")

    def request(self, method: str, path: str, *, body: bytes | None = None,
                content_type: str | None = None, headers: dict | None = None):
        request = urllib.request.Request(self.base + path, data=body, method=method)
        if content_type:
            request.add_header("Content-Type", content_type)
        if self.workspace_id:
            request.add_header("X-Workspace-ID", self.workspace_id)
        if method not in {"GET", "HEAD"} and self._csrf():
            request.add_header("X-CSRF-Token", self._csrf())
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def json(self, method: str, path: str, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        status, data = self.request(method, path, body=body,
                                    content_type="application/json" if body else None)
        return status, (json.loads(data) if data and data[:1] in b"[{" else data)

    def login(self, email: str, password: str, workspace_id: str) -> None:
        status, session = self.json("POST", "/api/v1/auth/login",
                                    {"identifier": email, "password": password})
        if status != 200:
            raise SystemExit(f"login failed for {email}: {status} {session}")
        self.workspace_id = workspace_id


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # report 30x instead of following
        return None


def _meetings(client: Client) -> list[dict]:
    status, payload = client.json("GET", "/api/v1/meetings?limit=100")
    if status != 200:
        raise SystemExit(f"listing failed: {status} {payload}")
    return payload.get("items", payload if isinstance(payload, list) else [])


def _wav(seconds: int = 30, rate: int = 48_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * rate * seconds)
    return buffer.getvalue()


def _multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _completed(meetings: list[dict]) -> list[dict]:
    return [m for m in meetings if m.get("status") in {"completed", "ready", "processed"}] or meetings


def mutate(args) -> int:
    tenants = json.load(open(args.tenants))
    password = tenants["password"]
    t0, t1 = tenants["tenants"][0], tenants["tenants"][1]
    changes: dict = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "deleted": [], "revoked_share": None}

    alpha = Client(args.base)
    alpha.login(t0["owner"], password, t0["workspace_id"])
    meetings = _completed(_meetings(alpha))
    for meeting in meetings[:2]:
        status, body = alpha.request("DELETE", f"/api/v1/meetings/{meeting['id']}")
        if status != 204:
            raise SystemExit(f"delete failed: {status} {body[:200]}")
        changes["deleted"].append({"tenant": 0, "meeting_id": meeting["id"]})
    changes["kept"] = {"tenant": 0, "meeting_id": meetings[2]["id"]}
    body, content_type = _multipart({"language": "fa", "title": "after-backup upload"},
                                    "after-backup.wav", _wav())
    status, payload = alpha.request("POST", "/api/v1/uploads", body=body, content_type=content_type,
                                    headers={"Idempotency-Key": "drill-" + secrets.token_hex(8)})
    if status != 202:
        raise SystemExit(f"upload failed: {status} {payload[:300]}")
    changes["created_after_backup"] = {"tenant": 0, "meeting_id": json.loads(payload)["meeting"]["id"]}

    beta = Client(args.base)
    beta.login(t1["owner"], password, t1["workspace_id"])
    meetings = _completed(_meetings(beta))
    status, body = beta.request("DELETE", f"/api/v1/meetings/{meetings[0]['id']}")
    if status != 204:
        raise SystemExit(f"delete failed: {status} {body[:200]}")
    changes["deleted"].append({"tenant": 1, "meeting_id": meetings[0]["id"]})
    for meeting in meetings[1:]:
        status, shares = beta.json("GET", f"/api/v1/meetings/{meeting['id']}/shares")
        active = [s for s in (shares if status == 200 else []) if not s.get("revoked_at")]
        if active:
            status, _ = beta.request("DELETE", f"/api/v1/meetings/{meeting['id']}/shares/{active[0]['id']}")
            if status == 204:
                changes["revoked_share"] = {"tenant": 1, "meeting_id": meeting["id"], "share_id": active[0]["id"]}
                break
    print(json.dumps(changes, indent=2))
    return 0


def smoke(args) -> int:
    tenants = json.load(open(args.tenants))
    changes = json.load(open(args.changes))
    password = tenants["password"]
    results: dict = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "checks": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        results["checks"].append({"check": name, "ok": bool(ok), "detail": detail})

    clients = {}
    for tenant in tenants["tenants"]:
        client = Client(args.base)
        client.login(tenant["owner"], password, tenant["workspace_id"])
        clients[tenant["index"]] = client
        listed = _meetings(client)
        check(f"tenant {tenant['index']} signs in and lists conversations", len(listed) > 0, f"{len(listed)} listed")
    for deleted in changes["deleted"]:
        status, _ = clients[deleted["tenant"]].request("GET", f"/api/v1/meetings/{deleted['meeting_id']}")
        check(f"deleted conversation {deleted['meeting_id'][:8]} stays deleted", status == 404, str(status))
    kept = changes["kept"]
    status, _ = clients[kept["tenant"]].request("GET", f"/api/v1/meetings/{kept['meeting_id']}")
    check("kept conversation is served to its tenant", status == 200, str(status))
    status, _ = clients[1].request("GET", f"/api/v1/meetings/{kept['meeting_id']}")
    check("another tenant cannot read it", status == 404, str(status))
    status, _ = clients[kept["tenant"]].request("GET", f"/api/v1/meetings/{kept['meeting_id']}/recording")
    check("kept recording is delivered (200 or presigned redirect)", status in {200, 206, 302, 307}, str(status))
    created = changes["created_after_backup"]
    status, _ = clients[created["tenant"]].request("GET", f"/api/v1/meetings/{created['meeting_id']}")
    check("post-backup upload is absent (inside the RPO window)", status == 404, str(status))
    results["passed"] = all(c["ok"] for c in results["checks"])
    print(json.dumps(results, indent=2))
    return 0 if results["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("mutate", "smoke"):
        p = sub.add_parser(name)
        p.add_argument("--base", required=True)
        p.add_argument("--tenants", required=True)
        if name == "smoke":
            p.add_argument("--changes", required=True)
    args = parser.parse_args()
    return mutate(args) if args.command == "mutate" else smoke(args)


if __name__ == "__main__":
    sys.exit(main())
