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


def test_retention_keeps_30_daily_and_12_monthly_and_always_the_newest():
    start = datetime(2025, 1, 1, 2, 0, tzinfo=UTC)
    nightly = _ids(start, 500, timedelta(days=1))
    extra_same_day = new_backup_id(start + timedelta(days=499, hours=5))
    keep = retained(nightly + [extra_same_day])
    newest = max(nightly + [extra_same_day])
    assert newest in keep and extra_same_day == newest
    assert nightly[-1] not in keep  # an older set of the same (newest) day
    daily = {b for b in keep if b[:8] >= nightly[-30][:8]}
    assert len(daily) == 30
    months = {b[:6] for b in keep}
    assert len(months) == 12 + 0 and len(keep) <= 30 + 12
    assert retained([]) == set()
    assert retained(nightly[:1]) == {nightly[0]}


def test_freshness_textfile_is_merged_atomically_and_only_known_targets(tmp_path):
    path = metrics.textfile_path(tmp_path, env={})
    metrics.record_success(path, {"postgres": 100.0, "objects": 100.0})
    metrics.record_success(path, {"offsite": 90.5})
    metrics.record_success(path, {"postgres": 200.0})
    assert metrics.read_values(path) == {"postgres": 200.0, "objects": 100.0, "offsite": 90.5}
    text = path.read_text()
    assert 'homayar_backup_last_success_timestamp_seconds{target="offsite"} 90.500' in text
    assert "# TYPE homayar_backup_last_success_timestamp_seconds gauge" in text
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    with pytest.raises(ValueError):
        metrics.record_success(path, {"redis": 1.0})
    custom = tmp_path / "textfile" / "x.prom"
    assert metrics.textfile_path(tmp_path, env={"HOMAYAR_BACKUP_TEXTFILE": str(custom)}) == custom


# --- off-site copies (S3 code paths against an in-memory stand-in) -------------------


class _FakeS3:
    """The few S3 calls the replication uses, kept in memory."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def upload_file(self, filename, bucket, key, ExtraArgs=None, Config=None):  # noqa: N803
        assert (ExtraArgs or {}).get("ChecksumAlgorithm") == "SHA256"
        self.objects[(bucket, key)] = Path(filename).read_bytes()

    def head_object(self, Bucket, Key):  # noqa: N803
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def download_file(self, bucket, key, filename):
        Path(filename).write_bytes(self.objects[(bucket, key)])

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop((Bucket, Key), None)

    def get_paginator(self, _name):
        return self

    def paginate(self, Bucket, Prefix=""):  # noqa: N803
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        yield {"Contents": [{"Key": k} for k in keys]}


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
    prune_s3("s3://offsite/p", daily=2, monthly=0, client=s3)
    assert list_s3_sets("s3://offsite/p", client=s3) == ids[2:]
    assert not [k for _, k in s3.objects if k.startswith(f"p/{ids[1]}/")]
