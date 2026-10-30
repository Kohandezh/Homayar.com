"""On-prem recording delivery streams through the API (HOM-340).

On-prem storage is the bundled MinIO at ``http://minio:9000``. A browser cannot resolve
``minio`` and cannot load ``http://`` from an ``https://`` page, and MinIO never gets a
published port. So with ``STORAGE_PRESIGNED_DELIVERY=false`` the download, inline playback
and share routes never answer ``307`` to a presigned URL: they stream the bytes themselves,
with ``Range`` served by a ranged ``GetObject`` (``206`` and the exact bytes), in chunks.
The default (``true``, cloud) keeps today's ``307`` and headers.

The routes run against the real ``S3ObjectStorage`` over a fake S3 client that answers like
MinIO: ``Range`` on ``GetObject``, presigned URLs on ``http://minio:9000``.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select

from backend.config import Settings
from backend.database.saas_models import Recording
from backend.services.download_service import CHUNK_BYTES, deliver_recording
from backend.storage import LocalObjectStorage, ObjectNotFoundError, S3ObjectStorage
from backend.storage.base import generate_object_key
from backend.storage.factory import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_playback_navigation import _Playable
from backend.tests.test_storage_contract import ContractS3Client, _client_error

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = REPO_ROOT / "infra" / "compose"

MINIO = "http://minio:9000"
# Larger than two delivery chunks, and every byte position is distinguishable.
OBJECT = bytes(i % 251 for i in range(2 * CHUNK_BYTES + 12_345))
SIZE = len(OBJECT)
TENANT = "tenant-onprem"


class _ReadLog(io.BytesIO):
    """An S3 body that records the size of every ``read``."""

    def __init__(self, data: bytes, reads: list[int | None]) -> None:
        super().__init__(data)
        self._reads = reads

    def read(self, size: int | None = -1) -> bytes:
        self._reads.append(size)
        return super().read(size)

    def seekable(self) -> bool:  # a botocore StreamingBody cannot seek
        return False

    def seek(self, *_args: Any, **_kwargs: Any) -> int:
        raise io.UnsupportedOperation("seek")


class MinioLikeClient(ContractS3Client):
    """``ContractS3Client`` plus ``Range`` on ``GetObject`` and MinIO-shaped presigned URLs."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[dict[str, Any]] = []
        self.reads: list[int | None] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self._enter("get")
        self.get_calls.append(kwargs)
        stored = self.objects.get(kwargs["Key"])
        if stored is None:
            raise _client_error("NoSuchKey", 404, "GetObject")
        body: bytes = stored["Body"]
        if "Range" in kwargs:
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", kwargs["Range"])
            assert match is not None, kwargs["Range"]
            start, end = int(match.group(1)), int(match.group(2))
            assert 0 <= start <= end < len(body), kwargs["Range"]
            body = body[start : end + 1]
        return {"Body": _ReadLog(body, self.reads), "ContentLength": len(body)}

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        params = kwargs["Params"]
        return f"{MINIO}/{params['Bucket']}/{params['Key']}?X-Amz-Signature=fake"


def _store(fake: MinioLikeClient, key: str, data: bytes = OBJECT) -> None:
    fake.objects[key] = {"Body": data, "Metadata": {}, "LastModified": datetime.now(UTC)}


def _inject(monkeypatch: pytest.MonkeyPatch, fake: MinioLikeClient) -> None:
    """``create_object_storage`` builds the real S3 adapter, over the fake client."""
    real = S3ObjectStorage

    def build(bucket: str, **kwargs: Any) -> S3ObjectStorage:
        return real(bucket, client=fake, **kwargs)

    monkeypatch.setattr("backend.storage.factory.S3ObjectStorage", build)


# --- route level ------------------------------------------------------------------------


class _OnS3:
    def __init__(self, client, email_outbox, settings, monkeypatch) -> None:
        self.playable = _Playable(client, email_outbox, settings, monkeypatch)
        self.client = client
        self.fake = MinioLikeClient()
        with inspection_session_factory()() as db:
            recording = db.scalar(
                select(Recording).where(Recording.meeting_id == self.playable.meeting_id)
            )
            recording.size_bytes = SIZE
            _store(self.fake, recording.storage_key)
            db.commit()
        _inject(monkeypatch, self.fake)
        monkeypatch.setattr(settings, "storage_backend", "s3")
        monkeypatch.setattr(settings, "storage_endpoint_url", MINIO)
        monkeypatch.setattr(settings, "storage_access_key", "unit-app-access-key")
        monkeypatch.setattr(settings, "storage_secret_key", "unit-app-secret-key")  # noqa: S106 - placeholder

    def get(self, route: str, range_header: str | None = None):
        headers = {"Range": range_header} if range_header else {}
        p = self.playable
        if route == "share":
            return self.client.get(
                f"/api/v1/shares/{p.token}/recording",
                cookies=p.guest_cookies,
                headers=headers,
                follow_redirects=False,
            )
        path = {
            "download": f"/api/v1/meetings/{p.meeting_id}/recording",
            "inline": f"/api/v1/workspaces/{p.workspace_id}/meetings/{p.meeting_id}/recording",
        }[route]
        return p.owner_get(path, headers=headers, follow_redirects=False)


@pytest.fixture
def onprem(client, email_outbox, settings, monkeypatch) -> _OnS3:
    on_s3 = _OnS3(client, email_outbox, settings, monkeypatch)
    monkeypatch.setattr(settings, "storage_presigned_delivery", False)
    return on_s3


@pytest.fixture
def cloud(client, email_outbox, settings, monkeypatch) -> _OnS3:
    # The default of the setting, not a value the test sets.
    assert Settings.model_fields["storage_presigned_delivery"].default is True
    return _OnS3(client, email_outbox, settings, monkeypatch)


ROUTES = ["download", "inline", "share"]
DISPOSITION = {"download": "attachment", "inline": "inline", "share": "attachment"}


def _no_storage_location(response) -> None:
    for name, value in response.headers.items():
        assert "minio" not in value.lower(), (name, value)
        assert "x-amz" not in value.lower(), (name, value)
    assert "location" not in response.headers


def _stream_headers(response, route: str) -> None:
    assert response.headers["content-disposition"].startswith(f"{DISPOSITION[route]};")
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-homayar-revocation"] == "immediate"


@pytest.mark.parametrize("route", ROUTES)
def test_onprem_streams_the_whole_recording_without_a_redirect(onprem, route) -> None:
    response = onprem.get(route)

    assert response.status_code == 200, response.text
    assert response.content == OBJECT
    assert response.headers["content-length"] == str(SIZE)
    assert "content-range" not in response.headers
    _stream_headers(response, route)
    _no_storage_location(response)
    assert [call.get("Range") for call in onprem.fake.get_calls] == [None]
    assert "generate_presigned_url" not in onprem.fake.operations


@pytest.mark.parametrize("route", ROUTES)
def test_onprem_range_is_206_with_the_exact_bytes(onprem, route) -> None:
    response = onprem.get(route, "bytes=10-19")

    assert response.status_code == 206, response.text
    assert response.content == OBJECT[10:20]
    assert response.headers["content-range"] == f"bytes 10-19/{SIZE}"
    assert response.headers["content-length"] == "10"
    _stream_headers(response, route)
    _no_storage_location(response)
    # One ranged GetObject: the API never reads the object from the start.
    assert [call.get("Range") for call in onprem.fake.get_calls] == ["bytes=10-19"]


@pytest.mark.parametrize(
    ("range_header", "start", "end"),
    [
        pytest.param(f"bytes={SIZE - 100}-", SIZE - 100, SIZE - 1, id="open-end"),
        pytest.param("bytes=-7", SIZE - 7, SIZE - 1, id="suffix"),
        pytest.param(f"bytes=5-{SIZE + 1000}", 5, SIZE - 1, id="end-past-size"),
        pytest.param("bytes=0-", 0, SIZE - 1, id="from-zero"),
    ],
)
def test_onprem_range_follows_parse_range(onprem, range_header, start, end) -> None:
    response = onprem.get("inline", range_header)

    assert response.status_code == 206, response.text
    assert response.content == OBJECT[start : end + 1]
    assert response.headers["content-range"] == f"bytes {start}-{end}/{SIZE}"
    assert response.headers["content-length"] == str(end - start + 1)
    assert [call.get("Range") for call in onprem.fake.get_calls] == [f"bytes={start}-{end}"]


@pytest.mark.parametrize("range_header", [f"bytes={SIZE}-", "bytes=9-3", "bytes=-0", "items=0-1"])
def test_onprem_unsatisfiable_range_keeps_the_416(onprem, range_header) -> None:
    response = onprem.get("inline", range_header)

    assert response.status_code == 416, response.text
    assert response.headers["content-range"] == f"bytes */{SIZE}"
    assert response.json()["error"]["code"] == "range_not_satisfiable"
    assert onprem.fake.get_calls == []


def test_onprem_streams_in_chunks_never_the_whole_object(onprem) -> None:
    whole = onprem.get("download")
    ranged = onprem.get("inline", f"bytes=100-{CHUNK_BYTES + 5000}")

    assert whole.content == OBJECT
    assert ranged.content == OBJECT[100 : CHUNK_BYTES + 5001]
    assert onprem.fake.reads, "the body was never read"
    for size in onprem.fake.reads:
        assert size is not None and 0 < size <= CHUNK_BYTES, onprem.fake.reads


@pytest.mark.parametrize("route", ROUTES)
def test_cloud_default_still_redirects_to_the_presigned_url(cloud, route) -> None:
    response = cloud.get(route)

    assert response.status_code == 307, response.text
    assert response.headers["location"].startswith(f"{MINIO}/")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-homayar-revocation"] == "expiry-bounded"
    assert cloud.fake.get_calls == []


# --- the factory carries the setting ----------------------------------------------------


def _recording(key: str) -> Any:
    return SimpleNamespace(
        storage_key=key,
        size_bytes=SIZE,
        media_type="audio/wav",
        container_format="wav",
        original_filename="call.wav",
    )


@pytest.mark.parametrize(
    ("overrides", "redirects"), [({}, True), ({"storage_presigned_delivery": False}, False)]
)
def test_create_object_storage_carries_the_delivery_setting(
    settings, monkeypatch, overrides, redirects
) -> None:
    fake = MinioLikeClient()
    _inject(monkeypatch, fake)
    key = generate_object_key(TENANT)
    _store(fake, key)
    s3_settings = settings.model_copy(
        update={
            "storage_backend": "s3",
            "storage_endpoint_url": MINIO,
            "storage_access_key": "unit-app-access-key",
            "storage_secret_key": "unit-app-secret-key",  # noqa: S106 - placeholder
            **overrides,
        }
    )

    storage = create_object_storage(s3_settings)
    response = deliver_recording(
        storage=storage,
        workspace_id=TENANT,
        recording=_recording(key),
        range_header=None,
        presign_ttl_seconds=60,
    )

    assert storage.supports_presigned_access is True
    assert storage.presigned_delivery is redirects
    assert isinstance(response, RedirectResponse) is redirects
    assert isinstance(response, StreamingResponse) is not redirects


def test_local_storage_is_unchanged_by_the_setting(settings) -> None:
    storage = create_object_storage(
        settings.model_copy(update={"storage_presigned_delivery": False})
    )
    assert isinstance(storage, LocalObjectStorage)
    assert storage.supports_presigned_access is False


# --- storage level: the ranged read -----------------------------------------------------


def test_s3_ranged_read_uses_get_object_range() -> None:
    fake = MinioLikeClient()
    storage = S3ObjectStorage("homayar-private", max_size_bytes=SIZE, client=fake)
    key = generate_object_key(TENANT)
    _store(fake, key)

    with storage.open_range_reader(TENANT, key, start=7, end=4096) as reader:
        assert reader.read() == OBJECT[7:4097]
    assert fake.get_calls == [{"Bucket": "homayar-private", "Key": key, "Range": "bytes=7-4096"}]


def test_s3_ranged_read_of_a_missing_object_is_not_found() -> None:
    fake = MinioLikeClient()
    storage = S3ObjectStorage("homayar-private", max_size_bytes=SIZE, client=fake)
    with pytest.raises(ObjectNotFoundError):
        storage.open_range_reader(TENANT, generate_object_key(TENANT), start=0, end=1)


def _local(tmp_path: Path) -> tuple[LocalObjectStorage, str]:
    storage = LocalObjectStorage(tmp_path / "objects", max_size_bytes=SIZE)
    stored = storage.put_stream(TENANT, io.BytesIO(OBJECT), content_type="audio/wav")
    return storage, stored.key


def test_local_ranged_read_returns_the_exact_bytes(tmp_path: Path) -> None:
    storage, key = _local(tmp_path)
    with storage.open_range_reader(
        TENANT, key, start=CHUNK_BYTES - 3, end=CHUNK_BYTES + 3
    ) as reader:
        assert reader.read(7) == OBJECT[CHUNK_BYTES - 3 : CHUNK_BYTES + 4]


def test_base_ranged_read_skips_forward_on_a_reader_that_cannot_seek(tmp_path: Path) -> None:
    reads: list[int | None] = []

    class NoSeek(LocalObjectStorage):
        def open_reader(self, tenant_id: str, key: str):  # type: ignore[override]
            with super().open_reader(tenant_id, key) as handle:
                return _ReadLog(handle.read(), reads)

    storage = NoSeek(tmp_path / "objects", max_size_bytes=SIZE)
    key = storage.put_stream(TENANT, io.BytesIO(OBJECT), content_type="audio/wav").key
    start = CHUNK_BYTES + 10
    reader = super(LocalObjectStorage, storage).open_range_reader(
        TENANT, key, start=start, end=SIZE - 1
    )
    with reader:
        assert reader.read(25) == OBJECT[start : start + 25]
    for size in reads[:-1]:  # the skip reads in bounded chunks, never the whole object
        assert size is not None and 0 < size <= CHUNK_BYTES, reads


# --- Compose sets the setting for the on-prem API ---------------------------------------


def test_onprem_compose_turns_presigned_delivery_off_for_the_app() -> None:
    compose_file = COMPOSE_DIR / "compose.onprem.yaml"
    if not compose_file.exists():
        pytest.skip("infra/compose is not part of this checkout")
    services = yaml.safe_load(compose_file.read_text(encoding="utf-8"))["services"]
    for name in ("api", "worker"):
        environment = services[name]["environment"]
        assert environment["STORAGE_ENDPOINT_URL"] == MINIO
        assert environment["STORAGE_PRESIGNED_DELIVERY"] == "false", name

    overlay = yaml.safe_load(
        (COMPOSE_DIR / "compose.onprem.app-server.yaml").read_text(encoding="utf-8")
    )
    for name, service in overlay.get("services", {}).items():
        assert "STORAGE_PRESIGNED_DELIVERY" not in str(service.get("environment", {})), name


# --- nginx: the recording routes never spool to the proxy's temp files ------------------

NGINX_CONF = REPO_ROOT / "infra" / "nginx" / "conf.d" / "homayar.conf"
APP_SERVER_DOC = REPO_ROOT / "docs" / "operations" / "app-server.md"


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _block(text: str, header: str) -> str:
    """The body of the first ``<header> { ... }`` block in ``text`` (comments removed)."""
    start = text.index(header + " {") + len(header) + 2
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index]
    raise AssertionError(f"unclosed block {header}")


def _directives(body: str) -> list[str]:
    """The directives of ``body`` itself, not those of its nested blocks."""
    flat: list[str] = []
    depth = 0
    for char in body:
        if char == "{":
            if depth == 0:  # drop the nested block's header ("location ~ ...")
                text = "".join(flat)
                flat = list(text[: text.rfind(";") + 1])
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0:
            flat.append(char)
    return [" ".join(part.split()) for part in "".join(flat).split(";") if part.strip()]


def _regex_location(body: str) -> tuple[str, str]:
    match = re.search(r"^\s*location ~ (\S+) \{", body, re.MULTILINE)
    assert match is not None, "no regex location"
    return match.group(1), _block(body, f"location ~ {match.group(1)}")


def _app_paths(settings) -> list[str]:
    """Every API path of the app, with each path parameter filled in."""
    from backend.main import create_app

    paths = []
    for route in create_app(settings).routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/v1/"):
            paths.append(re.sub(r"\{[^}]+\}", "x1y2-z3", path))
    return paths


def test_stack_nginx_streams_exactly_the_recording_routes(settings) -> None:
    if not NGINX_CONF.exists():
        pytest.skip("infra/nginx is not part of this checkout")
    api = _block(_strip_comments(NGINX_CONF.read_text(encoding="utf-8")), "location ^~ /api/v1/")
    pattern, recording = _regex_location(api)

    # Nested in the ^~ /api/v1/ prefix: nginx checks a prefix location's own regex
    # locations, while ^~ keeps the server-level ones away.
    assert _directives(recording) == [
        *(d for d in _directives(api) if d != "proxy_pass http://$api_upstream"),
        "proxy_max_temp_file_size 0",
        "proxy_pass http://$api_upstream",
    ]
    assert not any(d.startswith("proxy_max_temp_file_size") for d in _directives(api))

    paths = _app_paths(settings)
    served = sorted(path for path in paths if re.search(pattern, path))
    assert served == [
        "/api/v1/meetings/x1y2-z3/recording",
        "/api/v1/shares/x1y2-z3/recording",
        "/api/v1/workspaces/x1y2-z3/meetings/x1y2-z3/recording",
    ]
    # Every other API path stays on the old block.
    for path in (
        "/api/v1/meetings/x/playback",
        "/api/v1/meetings/x/recording/x",
        "/api/v1/meetings",
    ):
        assert not re.search(pattern, path), path


def test_host_tls_front_streams_the_same_recording_routes() -> None:
    if not (APP_SERVER_DOC.exists() and NGINX_CONF.exists()):
        pytest.skip("docs or infra/nginx are not part of this checkout")
    doc = APP_SERVER_DOC.read_text(encoding="utf-8")
    fence = re.search(r"```nginx\n(# HOM-328 app\.homayar\.com.*?)```", doc, re.S)
    assert fence is not None, "host front block not found"
    # The second server block is the TLS one (the first only redirects to https).
    servers = [m.start() for m in re.finditer(r"^server \{", fence.group(1), re.M)]
    assert len(servers) == 2
    tls_server = _block(_strip_comments(fence.group(1)[servers[1] :]), "server")
    pattern, recording = _regex_location(tls_server)

    stack_api = _block(
        _strip_comments(NGINX_CONF.read_text(encoding="utf-8")), "location ^~ /api/v1/"
    )
    assert pattern == _regex_location(stack_api)[0]
    assert _directives(recording) == [
        "proxy_max_temp_file_size 0",
        "proxy_pass http://127.0.0.1:8080",
    ]
    # Server-level, next to "location /": a regex location wins over a plain prefix.
    assert "location / {" in tls_server
    assert "client_max_body_size 16m" in _directives(tls_server)
