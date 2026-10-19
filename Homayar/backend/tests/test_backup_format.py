"""HOM-116: the encrypted backup file format, manifest seal, retention and freshness
metric (no database needed; the PostgreSQL round trips are in test_backup_restore.py)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.backup import metrics
from backend.backup.crypto import (
    CHUNK_SIZE,
    HEADER_SIZE,
    TAG_SIZE,
    BackupKey,
    BackupKeyError,
    DecryptionError,
    EncryptingWriter,
    decrypt_to_bytes,
    generate_key,
    load_key,
    parse_key,
)
from backend.backup.manifest import (
    FORMAT,
    BackupInvalid,
    check_manifest_mac,
    new_backup_id,
    read_manifest,
    valid_file_name,
    write_manifest,
)
from backend.backup.retention import retained

BACKUP_ID = "20261017T120000Z-0a1b2c3d"


def _key(key_id: str = "drill-1", fill: int = 7) -> BackupKey:
    return BackupKey(key_id, bytes([fill]) * 32)


def _encrypt(tmp_path: Path, data: bytes, *, name: str = "db/meetings.copy.enc") -> Path:
    path = tmp_path / name
    with EncryptingWriter(path, _key(), backup_id=BACKUP_ID, name=name) as writer:
        # Uneven writes exercise the chunk buffer.
        for offset in range(0, len(data), 333_333):
            writer.write(data[offset : offset + 333_333])
    return path


@pytest.mark.parametrize(
    "size", [0, 1, CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 1, 3 * CHUNK_SIZE, 3 * CHUNK_SIZE + 17]
)
def test_round_trip_at_every_chunk_boundary(tmp_path, size):
    data = bytes((i * 31) % 251 for i in range(size))
    path = _encrypt(tmp_path, data)
    chunks = max(1, -(-size // CHUNK_SIZE))
    assert path.stat().st_size == HEADER_SIZE + size + chunks * TAG_SIZE
    assert decrypt_to_bytes(path, _key(), backup_id=BACKUP_ID, name="db/meetings.copy.enc") == data
    assert data[:64] not in path.read_bytes()[HEADER_SIZE:] or size < 64


def test_two_encryptions_of_the_same_bytes_differ(tmp_path):
    one = _encrypt(tmp_path, b"same content" * 100, name="db/a.copy.enc").read_bytes()
    two = _encrypt(tmp_path, b"same content" * 100, name="db/b.copy.enc").read_bytes()
    assert one[HEADER_SIZE:] != two[HEADER_SIZE:]


def _flip(path: Path, offset: int) -> None:
    data = bytearray(path.read_bytes())
    data[offset] ^= 0x01
    path.write_bytes(bytes(data))


@pytest.mark.parametrize(
    "tamper",
    ["bit_flip", "header", "truncate_at_boundary", "truncate_tail", "append", "drop_middle_chunk"],
)
def test_any_tampering_or_truncation_fails_authentication(tmp_path, tamper):
    data = bytes(range(256)) * (3 * CHUNK_SIZE // 256)
    path = _encrypt(tmp_path, data)
    block = CHUNK_SIZE + TAG_SIZE
    raw = path.read_bytes()
    if tamper == "bit_flip":
        _flip(path, HEADER_SIZE + block + 10)
    elif tamper == "header":
        _flip(path, 12)
    elif tamper == "truncate_at_boundary":
        path.write_bytes(raw[: HEADER_SIZE + 2 * block])
    elif tamper == "truncate_tail":
        path.write_bytes(raw[:-1])
    elif tamper == "append":
        path.write_bytes(raw + b"\0" * 32)
    else:
        path.write_bytes(raw[: HEADER_SIZE + block] + raw[HEADER_SIZE + 2 * block :])
    with pytest.raises(DecryptionError):
        decrypt_to_bytes(path, _key(), backup_id=BACKUP_ID, name="db/meetings.copy.enc")


@pytest.mark.parametrize(
    "key, backup_id, name",
    [
        (_key(fill=8), BACKUP_ID, "db/meetings.copy.enc"),
        (_key(), "20261017T120000Z-ffffffff", "db/meetings.copy.enc"),
        (_key(), BACKUP_ID, "db/recordings.copy.enc"),
    ],
)
def test_a_file_only_opens_with_its_key_backup_and_name(tmp_path, key, backup_id, name):
    path = _encrypt(tmp_path, b"tenant rows")
    with pytest.raises(DecryptionError):
        decrypt_to_bytes(path, key, backup_id=backup_id, name=name)


def test_key_lines_parse_and_never_print_the_secret(tmp_path):
    line = generate_key("site-a-2026")
    key = parse_key(line)
    assert key.key_id == "site-a-2026" and len(key.secret) == 32
    assert line.split(":", 1)[1] not in repr(key) and len(key.fingerprint) == 32
    key_file = tmp_path / "backup.key"
    key_file.write_text(line + "\n", encoding="ascii")
    assert load_key(env={"HOMAYAR_BACKUP_KEY_FILE": str(key_file)}) == key
    assert load_key(env={"HOMAYAR_BACKUP_KEY": line}) == key
    short = "k:" + base64.b64encode(b"x" * 16).decode()
    for bad in ("nokey", "k:not-base64!", short, ":" + line.split(":", 1)[1]):
        with pytest.raises(BackupKeyError):
            parse_key(bad)
    with pytest.raises(BackupKeyError):
        load_key(env={})


def _manifest() -> dict:
    key = _key()
    return {
        "format": FORMAT,
        "backup_id": BACKUP_ID,
        "created_at": "2026-10-17T12:00:00+00:00",
        "encryption": {
            "state": "encrypted",
            "algorithm": "AES-256-GCM/STREAM-1MiB/HKDF-SHA256",
            "key_id": key.key_id,
            "key_fingerprint": key.fingerprint,
        },
        "files": [
            {"name": "catalog.json.enc", "size": 10, "sha256": "0" * 64, "plaintext_size": 1}
        ],
    }


def test_manifest_seal_detects_any_edit_and_a_foreign_key(tmp_path):
    write_manifest(tmp_path, _manifest(), _key())
    manifest = read_manifest(tmp_path)
    check_manifest_mac(manifest, _key())
    with pytest.raises(BackupInvalid) as wrong:
        check_manifest_mac(manifest, _key("other-key", fill=9))
    assert wrong.value.code == "wrong_key"
    edited = json.loads((tmp_path / "manifest.json").read_text())
    edited["created_at"] = "2026-10-18T12:00:00+00:00"
    (tmp_path / "manifest.json").write_text(json.dumps(edited))
    with pytest.raises(BackupInvalid) as tampered:
        check_manifest_mac(read_manifest(tmp_path), _key())
    assert tampered.value.code == "manifest_tampered"


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda m: m.update(format="homayar-backup/9"), "format_unsupported"),
        (lambda m: m.update(backup_id="../etc"), "manifest_corrupt"),
        (lambda m: m.update(files=[]), "manifest_corrupt"),
        (lambda m: m["files"].append(dict(m["files"][0])), "manifest_corrupt"),
        (lambda m: m["files"][0].update(name="../../outside"), "manifest_corrupt"),
        (lambda m: m["encryption"].update(state="none"), "manifest_corrupt"),
        (lambda m: m.pop("mac"), "manifest_corrupt"),
    ],
)
def test_malformed_manifests_are_rejected_with_stable_codes(tmp_path, mutate, code):
    write_manifest(tmp_path, _manifest(), _key())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    mutate(manifest)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BackupInvalid) as error:
        read_manifest(tmp_path)
    assert error.value.code == code


def test_missing_or_unparsable_manifest(tmp_path):
    with pytest.raises(BackupInvalid) as missing:
        read_manifest(tmp_path)
    assert missing.value.code == "manifest_missing"
    (tmp_path / "manifest.json").write_bytes(b"\xff{")
    with pytest.raises(BackupInvalid) as corrupt:
        read_manifest(tmp_path)
    assert corrupt.value.code == "manifest_corrupt"


def test_file_names_cannot_escape_the_set():
    assert valid_file_name("db/meetings.copy.enc")
    assert valid_file_name("objects/ab/" + "ab" * 32 + ".enc")
    for bad in ("../x", "db/../../etc.copy.enc", "/abs", "db/Meetings.copy.enc", "manifest.json"):
        assert not valid_file_name(bad)


def _ids(start: datetime, count: int, step: timedelta) -> list[str]:
    return [new_backup_id(start + i * step) for i in range(count)]


def test_retention_keeps_30_daily_and_12_monthly_beyond_the_daily_window():
    start = datetime(2025, 1, 1, 2, 0, tzinfo=UTC)
    nightly = _ids(start, 500, timedelta(days=1))
    extra_same_day = new_backup_id(start + timedelta(days=499, hours=5))
    keep = retained(nightly + [extra_same_day])
    assert extra_same_day in keep  # the newest set
    assert nightly[-1] not in keep  # an older set of the same (newest) day
    window_start = nightly[-30][:8]
    daily = sorted(b for b in keep if b[:8] >= window_start)
    monthly = sorted(b for b in keep if b[:8] < window_start)
    assert len(daily) == 30
    # 12 monthly sets, all OLDER than the daily window, one per month, the
    # newest of each month: about 12 months of history beyond the 30 days.
    assert len(monthly) == 12 and len({b[:6] for b in monthly}) == 12
    assert all(
        b == max(n for n in nightly if n[:6] == b[:6] and n[:8] < window_start) for b in monthly
    )
    assert retained([]) == set()
    assert retained(nightly[:1]) == {nightly[0]}


def test_retention_prefers_a_full_verified_set_over_a_newer_partial_one():
    day = datetime(2026, 10, 17, tzinfo=UTC)
    full = new_backup_id(day.replace(hour=2))
    db_only = new_backup_id(day.replace(hour=18))
    yesterday = new_backup_id(day - timedelta(hours=20))
    keep = retained([yesterday, full, db_only], daily=30, monthly=12, qualifying={full, yesterday})
    assert full in keep  # the day's representative is the verified full set
    assert db_only in keep  # still the newest set overall
    keep = retained([yesterday, full], daily=30, monthly=0, qualifying=set())
    assert keep == {yesterday, full}  # nothing qualifies: newest per bucket


def test_freshness_textfile_is_merged_atomically_and_only_known_targets(tmp_path):
    path = metrics.textfile_path(tmp_path, env={})
    metrics.record_success(path, {"postgres": 100.0, "objects": 100.0})
    metrics.record_success(path, {"offsite": 90.5})
    metrics.record_success(path, {"postgres": 200.0})
    metrics.record_success(path, {"postgres": 150.0})  # an older set verified later
    assert metrics.read_values(path) == {"postgres": 200.0, "objects": 100.0, "offsite": 90.5}
    assert path.stat().st_mode & 0o777 == 0o644 and path.parent.stat().st_mode & 0o777 == 0o755
    text = path.read_text()
    assert 'homayar_backup_last_success_timestamp_seconds{target="offsite"} 90.500' in text
    assert "# TYPE homayar_backup_last_success_timestamp_seconds gauge" in text
    assert sorted(p.name for p in path.parent.iterdir() if not p.name.endswith(".lock")) == [
        path.name
    ]
    with pytest.raises(ValueError):
        metrics.record_success(path, {"redis": 1.0})
    custom = tmp_path / "textfile" / "x.prom"
    assert metrics.textfile_path(tmp_path, env={"HOMAYAR_BACKUP_TEXTFILE": str(custom)}) == custom
    shared = tmp_path / "shared-collector-dir"  # node_exporter's, possibly not ours
    shared.mkdir()
    shared.chmod(0o750)
    metrics.record_success(shared / "homayar_backup.prom", {"postgres": 1.0})
    assert shared.stat().st_mode & 0o777 == 0o750  # an existing directory keeps its mode


# --- off-site copies (S3 code paths against an in-memory stand-in) -------------------


class _FakeS3:
    """The S3 calls the replication uses, kept in memory.

    ``checksums`` makes HEAD report the SHA-256 of what is stored (as S3 does
    for an upload with ``ChecksumAlgorithm``); ``versioning="Enabled"`` keeps
    every version and turns a plain delete into a delete marker; keys in
    ``locked`` cannot have their versions deleted (GOVERNANCE retention until
    ``lock_until``), keys in ``unpermitted`` refuse version deletes with
    AccessDenied but carry no lock (a missing permission), keys in
    ``failing`` fail with another error; operations in ``denied`` answer
    AccessDenied; ``multipart`` reports a composite checksum (``-N``) for an
    object larger than one part, as S3 does for a multipart upload.  Keys in
    ``held`` carry a legal hold; ``retention`` holds per-object GOVERNANCE
    retain-until dates set with ``put_object_retention`` (never shortened
    without the bypass, as S3 refuses); ``raising`` maps an operation and key
    to an exception it raises (a network error, say); ``reads`` counts
    ``get_object`` calls per key.
    """

    def __init__(
        self, *, checksums: bool = True, versioning: str = "", multipart: bool = False
    ) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.modified: dict[tuple[str, str], datetime] = {}
        self.checksums = checksums
        self.versioning = versioning
        self.versions: list[dict] = []  # {"Key", "VersionId", "marker"}
        self.locked: set[str] = set()
        self.lock_until = datetime.now(UTC) + timedelta(days=1)
        self.unpermitted: set[str] = set()
        self.object_lock = False
        self.uploads: list[dict] = []
        self.aborted: list[str] = []
        self.failing: set[str] = set()
        self.denied: set[str] = set()
        self.multipart = multipart
        self.held: set[str] = set()
        self.retention: dict[str, datetime] = {}
        self.raising: dict[tuple[str, str], Exception] = {}
        self.reads: dict[str, int] = {}

    def _version_id(self) -> str:
        self.serial = getattr(self, "serial", 0) + 1
        return f"v{self.serial}"

    def _deny(self, operation: str, key: str = "") -> None:
        from botocore.exceptions import ClientError

        if operation in self.denied:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, operation)
        if (operation, key) in self.raising:
            raise self.raising[(operation, key)]

    def _store(self, bucket, key, data: bytes) -> None:
        self.objects[(bucket, key)] = data
        self.modified[(bucket, key)] = datetime.now(UTC)
        if self.versioning == "Enabled":
            self.versions.append(
                {
                    "Key": key,
                    "VersionId": self._version_id(),
                    "marker": False,
                    "LastModified": datetime.now(UTC),
                }
            )

    def upload_file(self, filename, bucket, key, ExtraArgs=None, Config=None):  # noqa: N803
        assert (ExtraArgs or {}).get("ChecksumAlgorithm") == "SHA256"
        self._store(bucket, key, Path(filename).read_bytes())

    def put_object(self, Bucket, Key, Body, ChecksumAlgorithm=None):  # noqa: N803
        assert ChecksumAlgorithm == "SHA256"
        self._store(Bucket, Key, bytes(Body))

    def head_object(self, Bucket, Key, ChecksumMode=None):  # noqa: N803
        import hashlib

        from botocore.exceptions import ClientError

        from backend.backup import replicate

        self._deny("head_object", Key)
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        data = self.objects[(Bucket, Key)]
        head = {"ContentLength": len(data), "ETag": f'"{hashlib.sha256(data).hexdigest()[:32]}"'}
        if self.checksums and ChecksumMode == "ENABLED":
            part = replicate._PART_SIZE
            if self.multipart and len(data) > part:
                blocks = [data[i : i + part] for i in range(0, len(data), part)]
                digests = b"".join(hashlib.sha256(block).digest() for block in blocks)
                combined = base64.b64encode(hashlib.sha256(digests).digest()).decode()
                head["ChecksumSHA256"] = f"{combined}-{len(blocks)}"
            else:
                head["ChecksumSHA256"] = base64.b64encode(hashlib.sha256(data).digest()).decode()
        return head

    def get_object(self, Bucket, Key):  # noqa: N803
        import io

        from botocore.exceptions import ClientError

        self._deny("get_object", Key)
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        self.reads[Key] = self.reads.get(Key, 0) + 1
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def download_file(self, bucket, key, filename):
        Path(filename).write_bytes(self.objects[(bucket, key)])

    def delete_object(self, Bucket, Key, VersionId=None):  # noqa: N803
        from botocore.exceptions import ClientError

        if VersionId is None:
            if self.objects.pop((Bucket, Key), None) is not None and self.versioning == "Enabled":
                version = self._version_id()
                self.versions.append(
                    {
                        "Key": Key,
                        "VersionId": version,
                        "marker": True,
                        "LastModified": datetime.now(UTC),
                    }
                )
            return
        version = next(v for v in self.versions if v["VersionId"] == VersionId)
        if Key in self.locked and not version["marker"]:  # a lock protects versions, not markers
            if self.lock_until > datetime.now(UTC):
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")
        retained_until = self.retention.get(Key, datetime.min.replace(tzinfo=UTC))
        if not version["marker"] and (Key in self.held or retained_until > datetime.now(UTC)):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")
        if Key in self.unpermitted:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")
        if Key in self.failing:
            raise ClientError({"Error": {"Code": "InternalError"}}, "DeleteObject")
        self.versions = [v for v in self.versions if v["VersionId"] != VersionId]

    def get_object_retention(self, Bucket, Key, VersionId=None):  # noqa: N803
        from botocore.exceptions import ClientError

        self._deny("get_object_retention")
        if Key in self.retention:
            return {"Retention": {"Mode": "GOVERNANCE", "RetainUntilDate": self.retention[Key]}}
        if Key not in self.locked:
            raise ClientError({"Error": {"Code": "NoSuchObjectLockConfiguration"}}, "Get")
        return {"Retention": {"Mode": "GOVERNANCE", "RetainUntilDate": self.lock_until}}

    def get_object_lock_configuration(self, Bucket):  # noqa: N803
        from botocore.exceptions import ClientError

        if not self.object_lock:
            raise ClientError({"Error": {"Code": "ObjectLockConfigurationNotFoundError"}}, "Get")
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def put_object_retention(self, Bucket, Key, Retention):  # noqa: N803
        from botocore.exceptions import ClientError

        self._deny("put_object_retention")
        assert Retention["Mode"] == "GOVERNANCE"
        current = self.retention.get(Key, self.lock_until if Key in self.locked else None)
        if current is not None and Retention["RetainUntilDate"] < current:
            # Shortening GOVERNANCE retention needs s3:BypassGovernanceRetention.
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObjectRetention")
        self.retention[Key] = Retention["RetainUntilDate"]

    def get_object_legal_hold(self, Bucket, Key, VersionId):  # noqa: N803
        from botocore.exceptions import ClientError

        if Key in self.held:
            return {"LegalHold": {"Status": "ON"}}
        raise ClientError({"Error": {"Code": "NoSuchObjectLockConfiguration"}}, "Get")

    def get_bucket_versioning(self, Bucket):  # noqa: N803
        self._deny("get_bucket_versioning")
        return {"Status": self.versioning} if self.versioning else {}

    def list_multipart_uploads(self, Bucket, Prefix="", **_):  # noqa: N803
        self._deny("list_multipart_uploads")
        return {"Uploads": [u for u in self.uploads if u["Key"].startswith(Prefix)]}

    def abort_multipart_upload(self, Bucket, Key, UploadId):  # noqa: N803
        self.aborted.append(UploadId)
        self.uploads = [u for u in self.uploads if u["UploadId"] != UploadId]

    def get_paginator(self, name):
        fake = self

        class _Pages:
            def paginate(self, Bucket, Prefix=""):  # noqa: N803
                if name == "list_object_versions":
                    fake._deny("list_object_versions")
                    latest = {v["Key"]: v["VersionId"] for v in fake.versions}
                    found = [
                        {
                            "Key": v["Key"],
                            "VersionId": v["VersionId"],
                            "marker": v["marker"],
                            "LastModified": v["LastModified"],
                            "IsLatest": latest[v["Key"]] == v["VersionId"],
                        }
                        for v in fake.versions
                        if v["Key"].startswith(Prefix)
                    ]
                    yield {
                        "Versions": [v for v in found if not v["marker"]],
                        "DeleteMarkers": [v for v in found if v["marker"]],
                    }
                    return
                keys = sorted(k for b, k in fake.objects if b == Bucket and k.startswith(Prefix))
                yield {
                    "Contents": [
                        {"Key": k, "LastModified": fake.modified[(Bucket, k)]} for k in keys
                    ]
                }

        return _Pages()


def _fake_set(root: Path, backup_id: str) -> Path:
    """A structurally complete set (catalog + one table) sealed with the test key."""
    directory = root / "sets" / backup_id
    entries = []
    for name, payload in (
        ("catalog.json.enc", json.dumps({"backup_id": backup_id}).encode()),
        ("db/meetings.copy.enc", b"row\t1\n" * 1000),
    ):
        with EncryptingWriter(directory / name, _key(), backup_id=backup_id, name=name) as writer:
            writer.write(payload)
        entries.append(
            {
                "name": name,
                "size": writer.size,
                "sha256": writer.sha256,
                "plaintext_size": writer.plaintext_size,
            }
        )
    manifest = {**_manifest(), "backup_id": backup_id, "files": entries}
    write_manifest(directory, manifest, _key())
    return directory


def test_s3_copy_lists_fetches_verifies_and_rejects_a_corrupt_download(tmp_path):
    from backend.backup.replicate import fetch_from_s3, list_s3_sets, replicate_to_s3
    from backend.backup.verify import verify_set

    backup_id = "20261017T020000Z-00000001"
    set_dir = _fake_set(tmp_path / "primary", backup_id)
    s3 = _FakeS3()
    result = replicate_to_s3(set_dir, "s3://offsite/site-a", client=s3)
    assert result["status"] == "copied" and result["files"] == 2
    assert ("offsite", f"site-a/{backup_id}/manifest.json") in s3.objects
    assert list_s3_sets("s3://offsite/site-a", client=s3) == [backup_id]

    fetched = fetch_from_s3("s3://offsite/site-a", backup_id, tmp_path / "restore-1", client=s3)
    assert verify_set(fetched, _key(), deep=False)["mac_verified"]

    key = ("offsite", f"site-a/{backup_id}/db/meetings.copy.enc")
    corrupted = bytearray(s3.objects[key])
    corrupted[-5] ^= 0x01
    s3.objects[key] = bytes(corrupted)
    with pytest.raises(BackupInvalid) as error:
        fetch_from_s3("s3://offsite/site-a", backup_id, tmp_path / "restore-2", client=s3)
    assert error.value.code == "copy_checksum_mismatch"
    assert not list((tmp_path / "restore-2" / "sets").iterdir())  # nothing half-fetched stays


def test_s3_copy_without_its_manifest_is_not_a_set_and_retention_applies_off_site(tmp_path):
    from backend.backup.replicate import list_s3_sets, prune_s3, replicate_to_s3

    s3 = _FakeS3()
    ids = [f"2026101{day}T020000Z-0000000{day}" for day in range(1, 5)]
    for backup_id in ids:
        replicate_to_s3(_fake_set(tmp_path / "primary", backup_id), "s3://offsite/p", client=s3)
    s3.delete_object(Bucket="offsite", Key=f"p/{ids[0]}/manifest.json")  # interrupted copy
    assert list_s3_sets("s3://offsite/p", client=s3) == ids[1:]

    dry = prune_s3("s3://offsite/p", daily=2, monthly=0, dry_run=True, client=s3)
    assert dry["expired"] == [ids[1]] and set(dry["kept"]) == {ids[2], ids[3]}
    assert list_s3_sets("s3://offsite/p", client=s3) == ids[1:]
    result = prune_s3("s3://offsite/p", daily=2, monthly=0, client=s3)
    assert list_s3_sets("s3://offsite/p", client=s3) == ids[2:]
    assert not [k for _, k in s3.objects if k.startswith(f"p/{ids[1]}/")]
    # The manifest-less copy is young: kept for now (an upload may be running) ...
    assert result["stale_partials"] == [] and [k for _, k in s3.objects if ids[0] in k]
    for location in list(s3.modified):
        if ids[0] in location[1]:
            s3.modified[location] -= timedelta(days=2)
    # ... and swept once it is older than the grace period, never listed as a set.
    result = prune_s3("s3://offsite/p", daily=2, monthly=0, client=s3)
    assert result["stale_partials"] == [ids[0]]
    assert not [k for _, k in s3.objects if ids[0] in k]


# --- required objects absent at copy time: explained or lost (C1) ------------------------


def test_only_the_row_that_set_the_expectation_explains_an_absent_object():
    from backend.backup.create import classify_missing

    snapshot = datetime(2026, 10, 18, 2, 0, tzinfo=UTC)
    after, before = snapshot + timedelta(minutes=5), snapshot - timedelta(hours=3)
    checked = {"media_object", "recording", "checkpoint"}
    references = {
        "purged": {"source": "media_object"},
        "media-deleted-later": {"source": "media_object"},
        "media-deleted-earlier": {"source": "media_object"},
        "media-still-active": {"source": "media_object"},
        "held": {"source": "media_object", "held": True},
        "held-then-deleted": {"source": "media_object", "held": True},
        "recording-deleted-later": {"source": "recording"},
        "recording-deleted-earlier": {"source": "recording"},
        "checkpoint-cleaned-later": {"source": "checkpoint"},
        "checkpoint-invalidated": {"source": "checkpoint"},
        "checkpoint-lost-then-swept": {"source": "checkpoint"},
        "media-deleted-skewed": {"source": "media_object"},
        "unknown-source": {"source": "something-else"},
    }
    live = {
        "media-deleted-later": [{"state": "DELETED", "removed_at": after}],
        "media-deleted-earlier": [{"state": "DELETED", "removed_at": before}],
        "media-still-active": [{"state": "ACTIVE", "removed_at": None}],
        # The legal-hold original keeps its row; the recording sharing its key
        # was deleted by the user, but that row never decides for media.
        "held": [{"state": "LEGAL_HOLD", "removed_at": None}],
        "held-then-deleted": [{"state": "DELETED", "removed_at": after}],
        "recording-deleted-later": [{"state": None, "removed_at": after}],
        "recording-deleted-earlier": [{"state": None, "removed_at": before}],
        "checkpoint-cleaned-later": [{"state": "ABANDONED", "removed_at": after}],
        # App-detected loss (corrupt or missing artifact) is not a deletion.
        "checkpoint-invalidated": [{"state": "artifact_missing", "removed_at": None}],
        # Found missing by the application, then swept: still a loss (HOM-214).
        "checkpoint-lost-then-swept": [{"state": "artifact_missing", "removed_at": after}],
        # Committed after the snapshot, stamped a little before it by the app's clock.
        "media-deleted-skewed": [
            {"state": "DELETED", "removed_at": snapshot - timedelta(minutes=5)}
        ],
    }
    keys = sorted(references) + ["not-referenced"]

    reasons = classify_missing(keys, references, live, checked, snapshot)

    assert reasons == {
        "purged": "media row removed after the snapshot",
        "media-deleted-later": "media deleted after the snapshot",
        "recording-deleted-later": "recording deleted after the snapshot",
        "checkpoint-cleaned-later": "checkpoint deleted after the snapshot",
        "media-deleted-skewed": "media deleted after the snapshot",
    }
    # A source that could not be checked (its table is gone) explains nothing.
    assert classify_missing(["purged"], references, {}, {"recording"}, snapshot) == {}


def test_a_legal_hold_on_any_row_of_a_key_sticks_to_its_reference():
    from backend.backup.objects import _merge

    references: dict = {}
    held = {"key": "k", "expectation": "required", "source": "media_object", "held": True}
    _merge(references, held)
    _merge(references, {"key": "k", "expectation": "required", "source": "media_object"})
    assert references["k"]["held"] is True
    later = {"key": "j", "expectation": "optional", "source": "media_object", "held": False}
    _merge(references, later)
    _merge(references, {"key": "j", "expectation": "required", "source": "media_object"})
    assert "held" not in references["j"] or not references["j"]["held"]
    _merge(references, {"key": "j", "expectation": "gone", "source": "media_object", "held": True})
    assert references["j"]["expectation"] == "required" and references["j"]["held"] is True


# --- retired databases (C3) ---------------------------------------------------------------


def test_a_retired_entry_keeps_the_real_name_and_flags_overdue_or_malformed_notes():
    from backend.backup.reconcile import RETIRED_PREFIX, _retired_entry

    now = datetime(2026, 10, 30, tzinfo=UTC)
    spoof = RETIRED_PREFIX + json.dumps(
        {"database": "homayar", "drop_after": "2026-11-20T00:00:00+00:00", "overdue": False}
    )
    entry = _retired_entry("other_db", spoof, now)
    assert entry["database"] == "other_db" and entry["overdue"] is False

    late = RETIRED_PREFIX + json.dumps({"drop_after": "2026-10-25T00:00:00+00:00"})
    assert _retired_entry("homayar_retired_20261018000000", late, now)["overdue"] is True
    for broken in ("{not json", "[1, 2]", '"text"', json.dumps({"drop_after": "soon"})):
        entry = _retired_entry("x_retired_20261018000000", RETIRED_PREFIX + broken, now)
        assert entry["database"] == "x_retired_20261018000000" and entry["overdue"] is True


# --- a freshness textfile that cannot be written never stops a backup (T1) ---------------


def _verifiable_set(root: Path, backup_id: str) -> Path:
    """A set that passes a deep verify: catalog + one table, sealed with the test key."""
    directory = root / "sets" / backup_id
    catalog = {"backup_id": backup_id, "database": {"tables": ["meetings"]}}
    catalog["objects"] = {"inventory": []}
    entries = []
    for name, payload in (
        ("catalog.json.enc", json.dumps(catalog).encode()),
        ("db/meetings.copy.enc", b"row\t1\n" * 10),
    ):
        with EncryptingWriter(directory / name, _key(), backup_id=backup_id, name=name) as writer:
            writer.write(payload)
        entries.append(
            {
                "name": name,
                "size": writer.size,
                "sha256": writer.sha256,
                "plaintext_size": writer.plaintext_size,
            }
        )
    manifest = {
        **_manifest(),
        "backup_id": backup_id,
        "files": entries,
        "stores": {"objects": {"included": False}},
    }
    write_manifest(directory, manifest, _key())
    return directory


def test_a_textfile_that_cannot_be_written_never_stops_verify_or_replicate(
    tmp_path, monkeypatch, capsys
):
    from backend.backup.__main__ import EXIT_GAUGE_NOT_WRITTEN
    from backend.backup.__main__ import main as cli
    from backend.backup.manifest import list_complete_sets
    from backend.backup.replicate import OFFSITE_MARKER

    root = tmp_path / "root"
    backup_id = "20261018T020000Z-0000000a"
    _verifiable_set(root, backup_id)
    blocked = tmp_path / "not-a-directory"  # fails for root too (no DAC override needed)
    blocked.write_text("")
    key = _key()
    monkeypatch.setenv(
        "HOMAYAR_BACKUP_KEY", f"{key.key_id}:{base64.b64encode(key.secret).decode()}"
    )
    monkeypatch.delenv("HOMAYAR_BACKUP_KEY_FILE", raising=False)
    monkeypatch.setenv("HOMAYAR_BACKUP_TEXTFILE", str(blocked / "homayar_backup.prom"))

    assert cli(["--root", str(root), "textfile-check"]) == EXIT_GAUGE_NOT_WRITTEN
    assert cli(["--root", str(root), "verify", backup_id]) == EXIT_GAUGE_NOT_WRITTEN
    assert (root / "sets" / f"{backup_id}.verified").is_file()  # the set still counts
    offsite = tmp_path / "offsite"
    offsite.mkdir()
    (offsite / OFFSITE_MARKER).write_text("provisioned\n")
    assert cli(["--root", str(root), "replicate", backup_id, str(offsite)]) == (
        EXIT_GAUGE_NOT_WRITTEN
    )
    assert [name for name, _ in list_complete_sets(offsite)] == [backup_id]  # copied anyway
    assert "freshness gauge not written" in capsys.readouterr().err

    textfile = tmp_path / "collector" / "homayar_backup.prom"
    monkeypatch.setenv("HOMAYAR_BACKUP_TEXTFILE", str(textfile))
    assert cli(["--root", str(root), "textfile-check"]) == 0
    assert cli(["--root", str(root), "verify", backup_id]) == 0
    assert set(metrics.read_values(textfile)) == {"postgres"}


_FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${FAKE_DOCKER_LOG}"
case " $* " in
  *" ps "*) printf 'postgres\\nminio\\n' ;;
  *" textfile-check "*) exit "${FAKE_TEXTFILE_STATUS:-0}" ;;
  *" create "*)
    printf '{\\n  "backup_id": "20261018T020000Z-0000000b",\\n'
    printf '  "stores": {"objects": {"integrity": "%s"}}\\n}\\n' "${FAKE_INTEGRITY:-complete}" ;;
  *" verify "*) exit "${FAKE_VERIFY_STATUS:-0}" ;;
  *" replicate "*) exit "${FAKE_REPLICATE_STATUS:-0}" ;;
esac
exit 0
"""


@pytest.mark.parametrize(
    ("statuses", "expected_exit", "pruned"),
    [
        ({}, 0, True),
        (
            {"FAKE_TEXTFILE_STATUS": "4", "FAKE_VERIFY_STATUS": "4", "FAKE_REPLICATE_STATUS": "4"},
            4,
            True,
        ),
        ({"FAKE_REPLICATE_STATUS": "4"}, 4, True),
        ({"FAKE_VERIFY_STATUS": "4", "FAKE_INTEGRITY": "degraded"}, 3, True),
        ({"FAKE_VERIFY_STATUS": "1"}, 1, False),
    ],
)
def test_backup_sh_finishes_copy_and_prune_when_only_the_gauge_fails(
    tmp_path, statuses, expected_exit, pruned
):
    import os
    import subprocess

    script = Path(__file__).resolve().parents[3] / "infra" / "scripts" / "backup.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(_FAKE_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    for name in ("root", "offsite", "collector"):
        (tmp_path / name).mkdir()
    (tmp_path / "backup.key").write_text("k:" + base64.b64encode(b"\x01" * 32).decode())
    log = tmp_path / "docker.log"
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("BACKUP_", "COMPOSE_", "NODE_EXPORTER_", "EGRESS_"))
    }
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "BACKUP_ROOT": str(tmp_path / "root"),
            "BACKUP_KEY_FILE": str(tmp_path / "backup.key"),
            "BACKUP_OFFSITE_PATH": str(tmp_path / "offsite"),
            "NODE_EXPORTER_TEXTFILE_DIR": str(tmp_path / "collector"),
            "FAKE_DOCKER_LOG": str(log),
            **statuses,
        }
    )
    result = subprocess.run(  # noqa: S603 - the repository's own script
        ["bash", str(script)],  # noqa: S607 - bash from PATH, as cron runs it
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected_exit, result.stderr
    calls = log.read_text()
    assert "textfile-check" in calls and "verify 20261018T020000Z-0000000b" in calls
    assert (" prune" in calls) is pruned
    assert ("replicate 20261018T020000Z-0000000b /offsite" in calls) is pruned
    if expected_exit == 4:
        assert "could not be written" in result.stderr


# --- journal identity: anchored, unanchored or no evidence (HOM-212) ---------------------


class _StubJournal:
    def __init__(self, directory: Path, records: list) -> None:
        self.directory = directory
        self.records = records

    def files(self) -> list[Path]:
        return sorted(self.directory.glob("tombstones-*.jsonl"))

    def read(self) -> tuple[list, int]:
        return self.records, 0


def _record(workspace_id: str):
    from types import SimpleNamespace

    return SimpleNamespace(workspace_id=workspace_id)


def test_journal_identity_needs_positive_evidence_and_accepts_post_backup_tenants(tmp_path):
    import hashlib

    from backend.backup.reconcile import _journal_identity

    replica = tmp_path / "replica"
    replica.mkdir()
    day = replica / "tombstones-20261018.jsonl"
    day.write_bytes(b'{"a":1}\n{"b":2}\n')
    first = b'{"a":1}\n'
    anchored = {
        "files": [
            {"name": day.name, "size": len(first), "sha256": hashlib.sha256(first).hexdigest()}
        ]
    }
    known, newcomer = _record("ws-known"), _record("ws-signed-up-later")

    problems, mode, info = _journal_identity(
        _StubJournal(replica, [known, newcomer]), anchored, ["ws-known"]
    )
    assert (problems, mode) == ([], "anchored")
    assert info["records_of_tenants_unknown_to_backup"] == 1

    # No fingerprint (backup before the first deletion): a known tenant's
    # record is the evidence; a tenant created later does not fail it.
    problems, mode, info = _journal_identity(
        _StubJournal(replica, [known, newcomer]), {"files": []}, ["ws-known"]
    )
    assert (problems, mode) == ([], "unanchored") and info["tenants_unknown_to_backup"] == 1

    # Only unknown tenants, or no records at all (blank or 0-byte files): no evidence.
    for records in ([newcomer], []):
        _, mode, _ = _journal_identity(_StubJournal(replica, records), {"files": []}, ["ws-known"])
        assert mode == "none"

    # A file that was empty at backup time anchors nothing, but must still be there.
    empty_anchor = {
        "files": [{"name": day.name, "size": 0, "sha256": hashlib.sha256().hexdigest()}]
    }
    _, mode, _ = _journal_identity(_StubJournal(replica, []), empty_anchor, ["ws-known"])
    assert mode == "none"
    gone = {"files": [{"name": "tombstones-20261001.jsonl", "size": 0, "sha256": "0" * 64}]}
    problems, _, _ = _journal_identity(_StubJournal(replica, [known]), gone, ["ws-known"])
    assert problems == ["tombstones-20261001.jsonl: missing (stale or foreign replica)"]


# --- off-site copies: verified by SHA-256, retention, interrupted uploads, versions (HOM-213)


def _offsite_source(root: Path, backup_id: str, *, full: bool, verified: bool) -> Path:
    """A set under ``root/sets`` whose manifest says whether objects are included."""
    directory = root / "sets" / backup_id
    entries = []
    for name, payload in (("catalog.json.enc", b"{}"), ("db/meetings.copy.enc", b"r\n" * 50)):
        with EncryptingWriter(directory / name, _key(), backup_id=backup_id, name=name) as writer:
            writer.write(payload)
        entries.append(
            {
                "name": name,
                "size": writer.size,
                "sha256": writer.sha256,
                "plaintext_size": writer.plaintext_size,
            }
        )
    stores = {"objects": {"included": full, "integrity": "complete"}}
    manifest = {**_manifest(), "backup_id": backup_id, "files": entries, "stores": stores}
    write_manifest(directory, manifest, _key())
    if verified:
        (root / "sets" / f"{backup_id}.verified").write_text("verified\n")
    return directory


def test_an_s3_copy_is_verified_by_sha256_before_it_counts(tmp_path):
    from backend.backup.replicate import replicate_to_s3

    backup_id = "20261017T020000Z-0000000c"
    set_dir = _offsite_source(tmp_path / "root", backup_id, full=True, verified=True)

    s3 = _FakeS3()
    result = replicate_to_s3(set_dir, "s3://offsite/p", client=s3)
    assert result["verified_by"] == {"checksum": 3, "download": 0}  # 2 files + manifest
    assert ("offsite", f"p/{backup_id}.verified") in s3.objects  # deep-verified at the source

    plain = _FakeS3(checksums=False)  # a store that reports no checksum: read it back
    assert replicate_to_s3(set_dir, "s3://offsite/p", client=plain)["verified_by"] == {
        "checksum": 0,
        "download": 3,
    }

    class _Garbling(_FakeS3):  # the bytes change on the way in
        def upload_file(self, filename, bucket, key, ExtraArgs=None, Config=None):  # noqa: N803
            super().upload_file(filename, bucket, key, ExtraArgs, Config)
            data = bytearray(self.objects[(bucket, key)])
            data[0] ^= 0x01
            self.objects[(bucket, key)] = bytes(data)

    for garbling in (_Garbling(), _Garbling(checksums=False)):
        with pytest.raises(BackupInvalid) as refused:
            replicate_to_s3(set_dir, "s3://offsite/p", client=garbling)
        assert refused.value.code == "copy_checksum_mismatch"


def test_off_site_retention_prefers_the_same_full_verified_sets_as_the_root(tmp_path):
    from backend.backup.replicate import (
        OFFSITE_MARKER,
        list_s3_sets,
        prune_s3,
        replicate_to_directory,
        replicate_to_s3,
    )
    from backend.backup.retention import prune

    root = tmp_path / "root"
    full_day1 = "20261016T010000Z-0000000a"
    db_only_day1 = "20261016T020000Z-0000000b"  # newer, but no objects and unverified
    full_day2 = "20261017T010000Z-0000000c"
    sources = [
        _offsite_source(root, full_day1, full=True, verified=True),
        _offsite_source(root, db_only_day1, full=False, verified=False),
        _offsite_source(root, full_day2, full=True, verified=True),
    ]
    s3 = _FakeS3()
    directory = tmp_path / "offsite-dir"
    directory.mkdir()
    (directory / OFFSITE_MARKER).write_text("provisioned\n")
    for set_dir in sources:
        replicate_to_s3(set_dir, "s3://offsite/p", client=s3)
        replicate_to_directory(set_dir, directory)

    result = prune_s3("s3://offsite/p", daily=2, monthly=0, client=s3)
    assert result["qualifying"] == [full_day1, full_day2]
    assert result["expired"] == [db_only_day1]
    assert list_s3_sets("s3://offsite/p", client=s3) == [full_day1, full_day2]
    assert ("offsite", f"p/{db_only_day1}.verified") not in s3.objects
    assert prune(directory, daily=2, monthly=0)["expired"] == [db_only_day1]
    assert prune(root, daily=2, monthly=0)["expired"] == [db_only_day1]


def test_s3_prune_aborts_stale_uploads_and_deletes_noncurrent_versions(tmp_path):
    from backend.backup.replicate import list_s3_sets, prune_s3, replicate_to_s3

    root = tmp_path / "root"
    old, new = "20261015T020000Z-0000000a", "20261017T020000Z-0000000b"
    s3 = _FakeS3(versioning="Enabled")
    for backup_id in (old, new):
        replicate_to_s3(
            _offsite_source(root, backup_id, full=True, verified=True), "s3://offsite/p", client=s3
        )
    now = datetime.now(UTC)
    s3.uploads = [
        {
            "Key": f"p/{new}/objects/big.enc",
            "UploadId": "stale",
            "Initiated": now - timedelta(days=2),
        },
        {"Key": f"p/{new}/objects/big2.enc", "UploadId": "live", "Initiated": now},
    ]
    s3.object_lock = True
    s3.locked = {f"p/{old}/catalog.json.enc"}  # object lock still protects one version

    dry = prune_s3("s3://offsite/p", daily=1, monthly=0, dry_run=True, client=s3)
    assert dry["aborted_uploads"] == [f"p/{new}/objects/big.enc"] and not s3.aborted

    result = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert result["expired"] == [old] and list_s3_sets("s3://offsite/p", client=s3) == [new]
    assert s3.aborted == ["stale"] and [u["UploadId"] for u in s3.uploads] == ["live"]
    assert result["versioning"] == "Enabled" and result["versions_locked"] == 1
    left = [(v["Key"], v["marker"]) for v in s3.versions if v["Key"].startswith(f"p/{old}")]
    # Only what the lock protects remains, hidden behind its delete marker.
    assert left == [(f"p/{old}/catalog.json.enc", False), (f"p/{old}/catalog.json.enc", True)]
    assert list_s3_sets("s3://offsite/p", client=s3) == [new]
    assert result["versions_deleted"] > 0


def _age_versions(s3: _FakeS3, backup_id: str) -> None:
    """Make a set's versions older than the 24 h grace period."""
    for version in s3.versions:
        if version["Key"].startswith(f"p/{backup_id}"):
            version["LastModified"] = datetime.now(UTC) - timedelta(days=2)


def test_a_multipart_s3_copy_is_verified_by_its_composite_checksum(tmp_path, monkeypatch):
    from backend.backup import replicate

    monkeypatch.setattr(replicate, "_PART_SIZE", 64)  # every file spans several parts
    set_dir = _offsite_source(
        tmp_path / "root", "20261017T020000Z-0000000c", full=True, verified=False
    )
    s3 = _FakeS3(multipart=True)
    result = replicate.replicate_to_s3(set_dir, "s3://offsite/p", client=s3)
    assert result["verified_by"] == {"checksum": 3, "download": 0}
    heads = [s3.head_object("offsite", key, "ENABLED") for _, key in s3.objects]
    composite = sum("-" in head["ChecksumSHA256"] for head in heads)
    assert composite >= 2  # the table file and the manifest were multipart

    class _OtherPartSize(_FakeS3):  # parts of another size: the checksum cannot be reproduced
        def head_object(self, Bucket, Key, ChecksumMode=None):  # noqa: N803
            head = super().head_object(Bucket, Key, ChecksumMode)
            if "-" in head.get("ChecksumSHA256", ""):
                head["ChecksumSHA256"] = head["ChecksumSHA256"].rsplit("-", 1)[0] + "-99"
            return head

    other = replicate.replicate_to_s3(
        set_dir, "s3://offsite/p", client=_OtherPartSize(multipart=True)
    )
    # A composite checksum of other part sizes is read back, not trusted.
    assert other["verified_by"] == {"checksum": 3 - composite, "download": composite}


def test_an_interrupted_s3_prune_leaves_no_orphaned_versions(tmp_path):
    from backend.backup.replicate import list_s3_sets, prune_s3, replicate_to_s3

    root = tmp_path / "root"
    first, second, kept = (
        "20261013T020000Z-0000000a",
        "20261014T020000Z-0000000b",
        "20261017T020000Z-0000000c",
    )
    s3 = _FakeS3(versioning="Enabled")
    for backup_id in (first, second, kept):
        replicate_to_s3(
            _offsite_source(root, backup_id, full=True, verified=True), "s3://offsite/p", client=s3
        )

    class _Crash(Exception):
        pass

    plain_delete = s3.delete_object

    def dying_delete(Bucket, Key, VersionId=None):  # noqa: N803
        if Key == f"p/{second}/manifest.json" and VersionId is None:
            raise _Crash  # the prune dies in the middle of the second set
        plain_delete(Bucket=Bucket, Key=Key, VersionId=VersionId)

    s3.delete_object = dying_delete
    with pytest.raises(_Crash):
        prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    # The first set's versions went right after the set, not in a later loop.
    assert not [v for v in s3.versions if v["Key"].startswith(f"p/{first}")]
    s3.delete_object = plain_delete

    # Versions an older prune left behind (current version = delete marker).
    gone = "20261012T020000Z-00000009"
    s3._store("offsite", f"p/{gone}/db/meetings.copy.enc", b"old")
    s3.delete_object(Bucket="offsite", Key=f"p/{gone}/db/meetings.copy.enc")
    _age_versions(s3, gone)

    result = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert result["expired"] == [second] and result["leftover_sets"] == [gone]
    assert list_s3_sets("s3://offsite/p", client=s3) == [kept]
    assert {v["Key"].split("/")[1].removesuffix(".verified") for v in s3.versions} == {kept}
    assert result["errors"] == [] and result["versions_failed"] == 0


def test_s3_prune_without_listing_permissions_reports_and_fails_without_purging(
    tmp_path, monkeypatch, capsys
):
    from backend.backup import replicate
    from backend.backup.__main__ import main

    root = tmp_path / "root"
    old, new = "20261015T020000Z-0000000a", "20261017T020000Z-0000000b"
    s3 = _FakeS3(versioning="Enabled")
    for backup_id in (old, new):
        replicate.replicate_to_s3(
            _offsite_source(root, backup_id, full=True, verified=True), "s3://offsite/p", client=s3
        )
    s3.denied = {"list_multipart_uploads", "get_bucket_versioning", "list_object_versions"}

    dry = replicate.prune_s3("s3://offsite/p", daily=1, monthly=0, dry_run=True, client=s3)
    assert "list_multipart_uploads: AccessDenied" in dry["errors"]

    monkeypatch.setattr(replicate, "s3_client", lambda env=None: s3)
    status = main(["prune", "--destination", "s3://offsite/p", "--daily", "1", "--monthly", "0"])
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["expired"] == [old] and report["versioning"] == "unknown"
    assert report["versions_deleted"] == 0  # never swept on "unknown"
    assert set(report["errors"]) == {
        "list_multipart_uploads: AccessDenied",
        "get_bucket_versioning: AccessDenied",
    }


def test_s3_version_delete_failures_other_than_a_lock_fail_the_prune(tmp_path):
    from backend.backup.replicate import prune_s3, replicate_to_s3

    root = tmp_path / "root"
    old, new = "20261015T020000Z-0000000a", "20261017T020000Z-0000000b"
    s3 = _FakeS3(versioning="Enabled")
    for backup_id in (old, new):
        replicate_to_s3(
            _offsite_source(root, backup_id, full=True, verified=True), "s3://offsite/p", client=s3
        )
    s3.failing = {f"p/{old}/catalog.json.enc"}
    result = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert result["versions_locked"] == 0 and result["versions_failed"] >= 1
    assert result["errors"] == ["delete_object_version: InternalError"]


def test_off_site_copies_made_before_markers_get_them_backfilled(tmp_path):
    from backend.backup.replicate import (
        OFFSITE_MARKER,
        replicate_to_directory,
        replicate_to_s3,
    )

    root = tmp_path / "root"
    old, new = "20261015T020000Z-0000000a", "20261017T020000Z-0000000b"
    old_set = _offsite_source(root, old, full=True, verified=True)
    s3 = _FakeS3()
    directory = tmp_path / "offsite-dir"
    directory.mkdir()
    (directory / OFFSITE_MARKER).write_text("provisioned\n")
    replicate_to_s3(old_set, "s3://offsite/p", client=s3)
    replicate_to_directory(old_set, directory)
    # As copied before markers were carried:
    del s3.objects[("offsite", f"p/{old}.verified")]
    (directory / "sets" / f"{old}.verified").unlink()

    new_set = _offsite_source(root, new, full=True, verified=True)
    assert replicate_to_s3(new_set, "s3://offsite/p", client=s3)["backfilled_markers"] == [old]
    assert ("offsite", f"p/{old}.verified") in s3.objects
    assert replicate_to_directory(new_set, directory)["backfilled_markers"] == [old]
    assert (directory / "sets" / f"{old}.verified").is_file()


def test_a_partial_s3_set_with_a_live_upload_is_not_stale(tmp_path):
    from backend.backup.replicate import prune_s3

    s3 = _FakeS3()
    writing = "20261016T020000Z-0000000a"
    s3._store("offsite", f"p/{writing}/catalog.json.enc", b"x")
    s3.modified[("offsite", f"p/{writing}/catalog.json.enc")] = datetime.now(UTC) - timedelta(
        days=2
    )
    s3.uploads = [
        {"Key": f"p/{writing}/db/big.enc", "UploadId": "live", "Initiated": datetime.now(UTC)}
    ]
    result = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert result["stale_partials"] == []
    assert ("offsite", f"p/{writing}/catalog.json.enc") in s3.objects


def test_s3_prune_retries_object_locked_versions_and_fails_on_a_missing_permission(tmp_path):
    from backend.backup.replicate import list_s3_sets, prune_s3, replicate_to_s3

    root = tmp_path / "root"
    old, new = "20261015T020000Z-0000000a", "20261017T020000Z-0000000b"
    s3 = _FakeS3(versioning="Enabled")
    for backup_id in (old, new):
        replicate_to_s3(
            _offsite_source(root, backup_id, full=True, verified=True), "s3://offsite/p", client=s3
        )
    # D15: GOVERNANCE retention still protects the whole old set.
    s3.object_lock = True
    s3.locked = {k for _, k in s3.objects if k.startswith(f"p/{old}")}
    first = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert first["expired"] == [old] and first["versions_locked"] == len(s3.locked)
    assert first["versions_failed"] == 0 and first["errors"] == []
    assert first["locked_until"] == s3.lock_until.isoformat()
    assert list_s3_sets("s3://offsite/p", client=s3) == [new]  # hidden, not resurrected

    # Still locked on the next run: retried and reported, still not a failure.
    _age_versions(s3, old)  # the leftover sweep waits out the grace period
    again = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert again["leftover_sets"] == [old] and again["versions_locked"] == len(s3.locked)
    assert again["errors"] == []

    # The lock expires: the next run deletes what it protected.
    s3.lock_until = datetime.now(UTC) - timedelta(seconds=1)
    _age_versions(s3, old)
    done = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert done["leftover_sets"] == [old] and done["versions_locked"] == 0
    assert not [v for v in s3.versions if v["Key"].startswith(f"p/{old}")]

    # AccessDenied on an unlocked version is a missing permission, never "locked".
    s3.unpermitted = {k for _, k in s3.objects if k.startswith(f"p/{new}")}
    newer = "20261018T020000Z-0000000c"
    replicate_to_s3(
        _offsite_source(root, newer, full=True, verified=True), "s3://offsite/p", client=s3
    )
    denied = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert denied["expired"] == [new] and denied["versions_locked"] == 0
    assert denied["versions_failed"] >= 1
    assert denied["errors"] == ["delete_object_version: AccessDenied"]
    assert list_s3_sets("s3://offsite/p", client=s3) == [newer]  # its marker stays


def test_a_hollow_off_site_copy_never_gets_a_backfilled_marker(tmp_path):
    from backend.backup.replicate import (
        OFFSITE_MARKER,
        list_s3_sets,
        prune_s3,
        replicate_to_directory,
        replicate_to_s3,
    )

    root = tmp_path / "root"
    complete, hollow, newest = (
        "20260910T020000Z-0000000b",
        "20260920T020000Z-0000000a",
        "20261017T020000Z-0000000c",
    )
    s3 = _FakeS3()
    directory = tmp_path / "offsite-dir"
    directory.mkdir()
    (directory / OFFSITE_MARKER).write_text("provisioned\n")
    for backup_id in (complete, hollow):
        source = _offsite_source(root, backup_id, full=True, verified=True)
        replicate_to_s3(source, "s3://offsite/p", client=s3)
        replicate_to_directory(source, directory)
    # Copied before markers were carried, then an old prune died after deleting
    # the data and before the manifest: only the manifest is left.
    del s3.objects[("offsite", f"p/{hollow}.verified")]
    for _, key in list(s3.objects):
        if key.startswith(f"p/{hollow}/") and not key.endswith("manifest.json"):
            del s3.objects[("offsite", key)]
    (directory / "sets" / f"{hollow}.verified").unlink()
    (directory / "sets" / hollow / "db" / "meetings.copy.enc").unlink()

    source = _offsite_source(root, newest, full=True, verified=True)
    assert replicate_to_s3(source, "s3://offsite/p", client=s3)["backfilled_markers"] == []
    assert replicate_to_directory(source, directory)["backfilled_markers"] == []
    result = prune_s3("s3://offsite/p", daily=1, monthly=1, client=s3)
    assert complete not in result["expired"]  # September's only complete copy stays
    assert complete in list_s3_sets("s3://offsite/p", client=s3)


def test_the_leftover_sweep_never_touches_a_set_being_uploaded_again(tmp_path):
    from backend.backup.replicate import prune_s3

    s3 = _FakeS3(versioning="Enabled")
    again = "20261001T020000Z-0000000d"
    for name in ("catalog.json.enc", "db/meetings.copy.enc"):
        s3._store("offsite", f"p/{again}/{name}", b"old")
        s3.delete_object(Bucket="offsite", Key=f"p/{again}/{name}")
    _age_versions(s3, again)
    real = s3.get_bucket_versioning

    def versioning_and_a_concurrent_upload(Bucket):  # noqa: N803
        s3._store("offsite", f"p/{again}/catalog.json.enc", b"new")  # a re-replicate starts
        return real(Bucket=Bucket)

    s3.get_bucket_versioning = versioning_and_a_concurrent_upload
    result = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert result["leftover_sets"] == []
    assert s3.objects[("offsite", f"p/{again}/catalog.json.enc")] == b"new"
    assert [v for v in s3.versions if v["Key"] == f"p/{again}/catalog.json.enc"]

    # A fresh delete marker is not a leftover yet either (grace period).
    fresh = "20261002T020000Z-0000000e"
    s3._store("offsite", f"p/{fresh}/catalog.json.enc", b"x")
    s3.delete_object(Bucket="offsite", Key=f"p/{fresh}/catalog.json.enc")
    s3.get_bucket_versioning = real
    assert prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)["leftover_sets"] == []


def test_access_denied_without_object_lock_is_a_failure_not_a_lock(tmp_path):
    from backend.backup.replicate import prune_s3, replicate_to_s3

    root = tmp_path / "root"
    old, new = "20261015T020000Z-0000000a", "20261017T020000Z-0000000b"
    s3 = _FakeS3(versioning="Enabled")  # no Object Lock on the bucket
    for backup_id in (old, new):
        replicate_to_s3(
            _offsite_source(root, backup_id, full=True, verified=True), "s3://offsite/p", client=s3
        )
    s3.unpermitted = {
        k for _, k in s3.objects if k.startswith(f"p/{old}")
    }  # no DeleteObjectVersion
    result = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert result["object_lock"] is False
    assert result["versions_locked"] == 0 and result["versions_failed"] == len(s3.unpermitted)
    assert result["errors"] == ["delete_object_version: AccessDenied"]


def test_stale_partials_are_kept_when_uploads_cannot_be_listed(tmp_path):
    from backend.backup.replicate import prune_s3

    s3 = _FakeS3()
    writing = "20261016T020000Z-0000000a"
    s3._store("offsite", f"p/{writing}/catalog.json.enc", b"x")
    s3.modified[("offsite", f"p/{writing}/catalog.json.enc")] = datetime.now(UTC) - timedelta(
        days=2
    )
    s3.denied = {"list_multipart_uploads"}
    result = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert result["stale_partials"] == []
    assert ("offsite", f"p/{writing}/catalog.json.enc") in s3.objects
    assert result["errors"] == ["list_multipart_uploads: AccessDenied"]


def test_s3_prune_reports_legal_holds_apart_from_retention_dates(tmp_path):
    from backend.backup.replicate import prune_s3, replicate_to_s3

    root = tmp_path / "root"
    old, new = "20261015T020000Z-0000000a", "20261017T020000Z-0000000b"
    s3 = _FakeS3(versioning="Enabled")
    for backup_id in (old, new):
        replicate_to_s3(
            _offsite_source(root, backup_id, full=True, verified=True), "s3://offsite/p", client=s3
        )
    s3.object_lock = True
    old_keys = sorted(k for _, k in s3.objects if k.startswith(f"p/{old}/"))
    s3.held = {old_keys[0]}  # a legal hold on one object
    s3.locked = set(old_keys[1:])  # GOVERNANCE retention on the others
    result = prune_s3("s3://offsite/p", daily=1, monthly=0, client=s3)
    assert result["versions_locked"] == len(old_keys) and result["errors"] == []
    assert result["legal_holds"] == 1
    assert result["locked_until"] == s3.lock_until.isoformat()  # a date, never "legal-hold"


def _unmarked_s3_copies(root: Path, s3: _FakeS3, *backup_ids: str) -> None:
    """Verified root sets copied to s3://offsite/p before markers were carried."""
    from backend.backup.replicate import replicate_to_s3

    for backup_id in backup_ids:
        source = _offsite_source(root, backup_id, full=True, verified=True)
        replicate_to_s3(source, "s3://offsite/p", client=s3)
    for backup_id in backup_ids:
        del s3.objects[("offsite", f"p/{backup_id}.verified")]


def test_a_rejected_backfill_candidate_is_reported_and_not_downloaded_every_night(tmp_path):
    from backend.backup.replicate import replicate_to_s3

    root = tmp_path / "root"
    corrupt, gone, new = (
        "20261014T020000Z-0000000a",
        "20261015T020000Z-0000000b",
        "20261017T020000Z-0000000c",
    )
    s3 = _FakeS3(checksums=False)  # no store checksum: the backfill must read back
    _unmarked_s3_copies(root, s3, corrupt, gone)
    data_key = f"p/{corrupt}/db/meetings.copy.enc"
    good = s3.objects[("offsite", data_key)]
    s3.objects[("offsite", data_key)] = bytes([good[0] ^ 1]) + good[1:]  # same size, bit rot
    del s3.objects[("offsite", f"p/{gone}/db/meetings.copy.enc")]
    s3.reads.clear()

    source = _offsite_source(root, new, full=True, verified=True)
    first = replicate_to_s3(source, "s3://offsite/p", client=s3)
    rejected = [
        {"backup_id": corrupt, "reason": "copy_checksum_mismatch: meetings.copy.enc"},
        {"backup_id": gone, "reason": "missing: db/meetings.copy.enc"},
    ]
    assert first["status"] == "copied" and first["backfilled_markers"] == []
    assert first["backfill_rejected"] == rejected and first["backfill_errors"] == []
    assert s3.reads.get(data_key) == 1
    assert not s3.reads.get(f"p/{gone}/catalog.json.enc")  # a missing object: HEAD only

    # The next nights report the same rejections without downloading the copy again.
    for _ in range(2):
        again = replicate_to_s3(source, "s3://offsite/p", client=s3)
        assert again["backfill_rejected"] == rejected
    assert s3.reads.get(data_key) == 1

    # A copy uploaded again (new ETags) is checked afresh and gets its marker.
    s3.objects[("offsite", data_key)] = good
    fixed = replicate_to_s3(source, "s3://offsite/p", client=s3)
    assert fixed["backfilled_markers"] == [corrupt]
    assert ("offsite", f"p/{corrupt}.verified") in s3.objects


def test_a_network_error_in_the_backfill_never_fails_the_copy_or_the_gauge(
    tmp_path, monkeypatch, capsys
):
    from botocore.exceptions import ReadTimeoutError, ResponseStreamingError

    from backend.backup import replicate
    from backend.backup.__main__ import main as cli

    root = tmp_path / "root"
    old, other, new = (
        "20261014T020000Z-0000000a",
        "20261015T020000Z-0000000b",
        "20261017T020000Z-0000000c",
    )
    s3 = _FakeS3(checksums=False)
    _unmarked_s3_copies(root, s3, old, other)
    s3.raising = {
        ("get_object", f"p/{old}/db/meetings.copy.enc"): ReadTimeoutError(endpoint_url="s3"),
        ("head_object", f"p/{other}/catalog.json.enc"): ResponseStreamingError(error="reset"),
    }
    _offsite_source(root, new, full=True, verified=True)
    textfile = tmp_path / "collector" / "homayar_backup.prom"
    monkeypatch.setenv("HOMAYAR_BACKUP_TEXTFILE", str(textfile))
    monkeypatch.setattr(replicate, "s3_client", lambda env=None: s3)

    assert cli(["--root", str(root), "replicate", new, "s3://offsite/p"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "copied" and report["backfilled_markers"] == []
    assert report["backfill_errors"] == [
        {"backup_id": old, "error": "ReadTimeoutError"},
        {"backup_id": other, "error": "ResponseStreamingError"},
    ]
    assert report["backfill_rejected"] == []  # a transient error is not a verdict
    assert ("offsite", f"p/{new}.verified") in s3.objects
    assert "offsite" in metrics.read_values(textfile)  # the gauge moved

    # The next run, with the network back, backfills both.
    s3.raising = {}
    assert cli(["--root", str(root), "replicate", new, "s3://offsite/p"]) == 0
    assert json.loads(capsys.readouterr().out)["backfilled_markers"] == [old, other]
