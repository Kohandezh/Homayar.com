"""Tests for the sanitized support bundle (HOM-110, ADR 0064).

No database and no network: the database facts are monkeypatched for
``collect``, and ``scan`` works on planted files only. The canary values are
fake strings that exist nowhere outside this file and the planted files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.config import Settings
from backend.support_bundle.collect import PUBLIC_SETTINGS, collect_document
from backend.support_bundle.scan import find_secrets, scan_tree

CANARY_DB_PASSWORD = "canary-db-password-9f1a"
CANARY_JWT = "canary-jwt-secret-000000000000000000000011"
CANARY_S3_SECRET = "canary-s3-secret-8822"
CANARY_TOKEN = "canary-token-value-7733"
CANARY_RELEASE_SHA = "1f2e3d4c5b6a79880716253443566789abcdef01"


@pytest.fixture
def canary_settings() -> Settings:
    return Settings(
        environment="production",
        allow_mock_providers=False,
        deployment_mode="onprem",
        deployment_topology="single-host-p40",
        asr_profile="gpu-p40-large-v3",
        registration_enabled=False,
        log_level="INFO",
        secure_cookies=True,
        database_url=f"postgresql+psycopg://homayar:{CANARY_DB_PASSWORD}@postgres:5432/homayar",
        database_allow_insecure_transport=True,
        redis_allow_insecure_transport=True,
        jwt_secret=CANARY_JWT,
        token_pepper="canary-pepper-" + "0123456789abcdef0123456789",
        storage_backend="s3",
        storage_access_key="canary-access-key",
        storage_secret_key=CANARY_S3_SECRET,
        _env_file=None,
    )


def fake_database_facts(settings: Settings) -> dict[str, object]:
    return {"reachable": True, "server_version": "PostgreSQL 17.2", "alembic_version": "abc123def456"}


def fake_release_facts() -> dict[str, object]:
    return {
        "identity": {
            "version": "0.6.0",
            "source_sha": CANARY_RELEASE_SHA,
            "profile": "standard",
            "migration_head": "abc123def456",
            "python_version": "3.12.7",
        },
        "build_info": None,
    }


@pytest.fixture
def collected(monkeypatch: pytest.MonkeyPatch, canary_settings: Settings) -> dict[str, object]:
    monkeypatch.setattr("backend.support_bundle.collect.database_facts", fake_database_facts)
    monkeypatch.setattr("backend.support_bundle.collect.release_facts", fake_release_facts)
    return collect_document(settings=canary_settings)


class TestCollect:
    def test_is_json_serializable_and_has_schema(self, collected: dict[str, object]) -> None:
        document = json.loads(json.dumps(collected, sort_keys=True))
        assert document["schema_version"] == 1

    def test_contains_the_release_sha(self, collected: dict[str, object]) -> None:
        text = json.dumps(collected, sort_keys=True)
        assert CANARY_RELEASE_SHA in text

    def test_contains_allowlisted_public_settings(self, collected: dict[str, object]) -> None:
        public = collected["settings"]["public"]
        assert public["ENVIRONMENT"] == "production"
        assert public["DEPLOYMENT_MODE"] == "onprem"
        assert public["DEPLOYMENT_TOPOLOGY"] == "single-host-p40"
        assert public["ASR_PROFILE"] == "gpu-p40-large-v3"
        assert public["REGISTRATION_ENABLED"] is False
        assert public["LOG_LEVEL"] == "INFO"
    def test_public_allowlist_names_only_safe_settings(self) -> None:
        settings_fields = {name.upper() for name in Settings.model_fields}
        for name in PUBLIC_SETTINGS:
            assert name in settings_fields, f"PUBLIC_SETTINGS names an unknown setting: {name}"
        forbidden_substrings = ("URL", "KEY", "SECRET", "TOKEN", "PASSWORD", "DSN", "HOST", "EMAIL")
        for name in PUBLIC_SETTINGS:
            for part in forbidden_substrings:
                assert part not in name, f"{name} looks unsafe for the public allowlist"

    def test_other_settings_are_set_or_unset_only(self, collected: dict[str, object]) -> None:
        other = collected["settings"]["other"]
        assert other["DATABASE_URL"] == "set"
        assert other["JWT_SECRET"] == "set"
        assert other["STORAGE_SECRET_KEY"] == "set"
        assert other["SMTP_PASSWORD"] == "unset"

    def test_no_canary_secret_value_in_the_document(self, collected: dict[str, object]) -> None:
        text = json.dumps(collected, sort_keys=True)
        for canary in (CANARY_DB_PASSWORD, CANARY_JWT, CANARY_S3_SECRET, CANARY_TOKEN):
            assert canary not in text
        assert "canary-access-key" not in text
        assert "canary-pepper" not in text

    def test_database_facts_without_tenant_rows(self, collected: dict[str, object]) -> None:
        assert collected["database"] == {
            "reachable": True,
            "server_version": "PostgreSQL 17.2",
            "alembic_version": "abc123def456",
        }

    def test_runtime_and_package_facts(self, collected: dict[str, object]) -> None:
        runtime = collected["runtime"]
        assert runtime["platform_machine"]
        assert runtime["python_version"].startswith("3.")
        packages = collected["packages"]
        assert packages["fastapi"]
        assert packages["pydantic"]


class TestScanKinds:
    @pytest.fixture
    def env_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "onprem.env"
        path.write_text(
            "POSTGRES_PASSWORD=envfile-canary-pw-4455\n"
            f"DATABASE_URL=postgresql+psycopg://homayar:{CANARY_DB_PASSWORD}@postgres:5432/homayar\n"
            f"STORAGE_SECRET_KEY={CANARY_S3_SECRET}\n"
            "PUBLIC_ORIGIN=http://127.0.0.1:8080\n",
            encoding="utf-8",
        )
        return path

    @pytest.fixture
    def scan_dir(self, tmp_path: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "secrets.txt").write_text(
            f"the password is {CANARY_DB_PASSWORD} and the token {CANARY_TOKEN}\n"
            f"plus env file secret {CANARY_S3_SECRET} and envfile pw envfile-canary-pw-4455\n",
            encoding="utf-8",
        )
        (root / "url.txt").write_text(
            "postgres://admin:superpass@db.internal:5432/homayar\n", encoding="utf-8"
        )
        (root / "key.pem").write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----\n", encoding="utf-8"
        )
        (root / "jwt.txt").write_text(
            "Authorization: Bearer "
            + ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV"])
            + "\n",
            encoding="utf-8",
        )
        (root / "mail.txt").write_text("contact owner@example.com for details\n", encoding="utf-8")
        (root / "fa.txt").write_text("a transcript line: سلام دنیا\n", encoding="utf-8")
        (root / "note.txt").write_text("user@ is not an address\n", encoding="utf-8")
        (root / "clip.wav").write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
        (root / "net.txt").write_text("peer 10.1.2.3 refused\n", encoding="utf-8")
        monkeypatch.setenv("HOMAYAR_SCAN_TEST_TOKEN", CANARY_TOKEN)
        assert env_file.parent != root  # the env file itself is never inside the bundle
        return root

    def secrets(self, env_file: Path) -> list[str]:
        return find_secrets(env_file)

    def test_every_kind_fires_with_file_and_kind(self, scan_dir: Path, env_file: Path) -> None:
        findings = scan_tree(scan_dir, self.secrets(env_file))
        by_kind = {(f.path.name, f.kind) for f in findings}
        assert ("secrets.txt", "secret_value") in by_kind
        assert ("url.txt", "url_credentials") in by_kind
        assert ("key.pem", "private_key") in by_kind
        assert ("jwt.txt", "jwt") in by_kind
        assert ("mail.txt", "email") in by_kind
        assert ("fa.txt", "persian_text") in by_kind
        assert ("clip.wav", "audio") in by_kind
        assert ("net.txt", "ip_address") in by_kind

    def test_user_at_sign_alone_is_not_email(self, scan_dir: Path, env_file: Path) -> None:
        findings = scan_tree(scan_dir, self.secrets(env_file))
        assert not [f for f in findings if f.path.name == "note.txt" and f.kind == "email"]

    def test_never_reports_the_matched_text(self, scan_dir: Path, env_file: Path) -> None:
        findings = scan_tree(scan_dir, self.secrets(env_file))
        rendered = "\n".join(f"{f.kind} {f.path}" for f in findings)
        for planted in (
            CANARY_DB_PASSWORD,
            CANARY_TOKEN,
            CANARY_S3_SECRET,
            "superpass",
            "owner@example.com",
            "10.1.2.3",
        ):
            assert planted not in rendered

    def test_url_credentials_with_empty_user(self, tmp_path: Path) -> None:
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "redis.txt").write_text("redis://:s3cretpw@redis:6379/0\n", encoding="utf-8")
        findings = scan_tree(root, [])
        assert [(f.path.name, f.kind) for f in findings] == [("redis.txt", "url_credentials")]

    def test_persian_hidden_as_json_escapes_is_found(self, tmp_path: Path) -> None:
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "escaped.json").write_text(
            r'{"title": "\u0633\u0644\u0627\u0645"}' "\n", encoding="utf-8"
        )
        findings = scan_tree(root, [])
        assert [(f.path.name, f.kind) for f in findings] == [("escaped.json", "persian_text")]

    def test_plain_ascii_without_escapes_still_clean(self, tmp_path: Path) -> None:
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "paths.txt").write_text("C:\\users\\unknown\\file.txt\n", encoding="utf-8")
        assert scan_tree(root, []) == []

    def test_audio_by_size(self, tmp_path: Path, env_file: Path) -> None:
        big = tmp_path / "bundle-big"
        big.mkdir()
        (big / "large.bin").write_bytes(b"\x00" + b"x" * (5 * 1024 * 1024 + 1))
        findings = scan_tree(big, self.secrets(env_file))
        assert [(f.path.name, f.kind) for f in findings] == [("large.bin", "audio")]


class TestScanAllowControls:
    @pytest.fixture
    def clean_dir(self, tmp_path: Path) -> Path:
        root = tmp_path / "clean"
        root.mkdir()
        (root / "images.json").write_text(
            "api: sha256:3333333333333333333333333333333333333333333333333333333333333333\n"
            f"release: {CANARY_RELEASE_SHA}\n"
            "loopback: 127.0.0.1 and 0.0.0.0\n",
            encoding="utf-8",
        )
        return root

    def test_clean_bundle_scans_clean(self, clean_dir: Path) -> None:
        assert scan_tree(clean_dir, []) == []

    def test_clean_with_canary_free_env(self, clean_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STORAGE_SECRET_KEY", "short")
        monkeypatch.setenv("JWT_SECRET", "long-but-not-planted-000000000000")
        assert scan_tree(clean_dir, find_secrets(None)) == []


class TestScanFailsClosedOnUnreadableInput:
    """A permission problem must exit 2, never a false clean (review item 1)."""

    @pytest.fixture
    def clean_dir(self, tmp_path: Path) -> Path:
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "facts.txt").write_text("plain ascii facts\n", encoding="utf-8")
        return root

    @pytest.fixture
    def clean_env(self, tmp_path: Path) -> Path:
        path = tmp_path / "onprem.env"
        path.write_text("PUBLIC_ORIGIN=http://127.0.0.1:8080\n", encoding="utf-8")
        return path

    def _scan(self, directory: Path, env_file: Path | None) -> tuple[int, str]:
        import io
        import contextlib

        from backend.support_bundle.__main__ import main

        out, err = io.StringIO(), io.StringIO()
        argv = ["scan", str(directory)]
        if env_file is not None:
            argv += ["--env-file", str(env_file)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue() + err.getvalue()

    def test_missing_env_file_is_an_error(self, clean_dir: Path, tmp_path: Path) -> None:
        code, _ = self._scan(clean_dir, tmp_path / "missing.env")
        assert code == 2

    def test_readable_env_file_scans_clean(self, clean_dir: Path, clean_env: Path) -> None:
        code, _ = self._scan(clean_dir, clean_env)
        assert code == 0

    @pytest.mark.skipif(
        not hasattr(os, "geteuid") or os.geteuid() == 0,
        reason="permission checks do not apply when running as root",
    )
    def test_unreadable_env_file_is_an_error(self, clean_dir: Path, clean_env: Path) -> None:
        clean_env.chmod(0o000)
        try:
            code, _ = self._scan(clean_dir, clean_env)
            assert code == 2
        finally:
            clean_env.chmod(0o644)

    @pytest.mark.skipif(
        not hasattr(os, "geteuid") or os.geteuid() == 0,
        reason="permission checks do not apply when running as root",
    )
    def test_unreadable_file_is_an_error(self, clean_dir: Path, clean_env: Path) -> None:
        locked = clean_dir / "locked.txt"
        locked.write_text("nothing suspicious\n", encoding="utf-8")
        locked.chmod(0o000)
        try:
            code, _ = self._scan(clean_dir, clean_env)
            assert code == 2
        finally:
            locked.chmod(0o644)

    def test_readable_copy_scans_clean(self, clean_dir: Path, clean_env: Path) -> None:
        (clean_dir / "locked.txt").write_text("nothing suspicious\n", encoding="utf-8")
        code, _ = self._scan(clean_dir, clean_env)
        assert code == 0

    @pytest.mark.skipif(
        not hasattr(os, "geteuid") or os.geteuid() == 0,
        reason="permission checks do not apply when running as root",
    )
    def test_unreadable_folder_is_an_error(self, clean_dir: Path, clean_env: Path) -> None:
        closed = clean_dir / "closed"
        closed.mkdir()
        (closed / "inside.txt").write_text("plain ascii\n", encoding="utf-8")
        closed.chmod(0o000)
        try:
            code, _ = self._scan(clean_dir, clean_env)
            assert code == 2
        finally:
            closed.chmod(0o755)

    def test_readable_folder_scans_clean(self, clean_dir: Path, clean_env: Path) -> None:
        open_dir = clean_dir / "open"
        open_dir.mkdir()
        (open_dir / "inside.txt").write_text("plain ascii\n", encoding="utf-8")
        code, _ = self._scan(clean_dir, clean_env)
        assert code == 0


class TestScanCli:
    def _run(self, argv: list[str]) -> tuple[int, str]:
        import io
        import contextlib

        from backend.support_bundle.__main__ import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def test_collect_prints_one_json_document(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.support_bundle.collect.database_facts", fake_database_facts)
        monkeypatch.setattr("backend.support_bundle.collect.release_facts", fake_release_facts)
        code, out = self._run(["collect"])
        assert code == 0
        document = json.loads(out)
        assert document["schema_version"] == 1

    def test_collect_prints_non_ascii_unescaped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # LOG_LEVEL is a free-form public setting; ensure_ascii=False keeps
        # the Persian value literal instead of \uXXXX escapes
        from backend.config import reset_settings_cache

        monkeypatch.setattr("backend.support_bundle.collect.database_facts", fake_database_facts)
        monkeypatch.setattr("backend.support_bundle.collect.release_facts", fake_release_facts)
        monkeypatch.setenv("LOG_LEVEL", "اطلاعات")
        reset_settings_cache()
        try:
            code, out = self._run(["collect"])
        finally:
            reset_settings_cache()
        assert code == 0
        assert "اطلاعات" in out
        assert "\\u0627" not in out

    def test_scan_clean_exit_0(self, tmp_path: Path) -> None:
        (tmp_path / "ok.txt").write_text("plain ascii text\n", encoding="utf-8")
        code, out = self._run(["scan", str(tmp_path)])
        assert code == 0
        assert out == ""

    def test_scan_finding_exit_1_names_kind_and_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOMAYAR_SCAN_TEST_TOKEN", CANARY_TOKEN)
        (tmp_path / "leak.txt").write_text(f"token {CANARY_TOKEN}\n", encoding="utf-8")
        code, out = self._run(["scan", str(tmp_path)])
        assert code == 1
        assert "kind=secret_value" in out
        assert "leak.txt" in out
        assert CANARY_TOKEN not in out

    def test_scan_usage_error_exit_2(self, tmp_path: Path) -> None:
        code, _ = self._run(["scan", str(tmp_path / "missing")])
        assert code == 2
