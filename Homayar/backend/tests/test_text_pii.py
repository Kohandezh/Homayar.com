"""WP07-T05: Persian/English text PII detection, configurable classes and
restricted original-to-redacted mappings (ADR 0023).

Synthetic fixtures only (no customer text).  The evaluation below measures the
detector on a small synthetic, annotated set of the supported classes; it is
an engineering regression gate, not evidence of accuracy on real calls.
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select, text

from backend.config import REVEAL_SECRETS_CONTEXT, Settings
from backend.database.saas_models import (
    AuditEvent,
    PiiMapping,
    RedactedSegment,
    TranscriptImmutableError,
    TranscriptRedaction,
    Workspace,
)
from backend.jobs.processor import process_job
from backend.privacy.mapping_crypto import (
    MappingCipher,
    MappingIntegrityError,
    MappingKeyUnavailable,
    parse_keyring,
)
from backend.privacy.redaction_service import (
    RedactionError,
    redact_revision,
    redacted_segments,
    reveal_placeholder,
)
from backend.privacy.text_pii import (
    DETECTOR_VERSION,
    PiiClass,
    Pseudonymizer,
    detect,
    parse_classes,
    redact,
    valid_iban,
    valid_luhn,
)
from backend.providers.mocks import MockASRProvider
from backend.services.transcript_service import current_revision
from backend.tests import test_row_level_security as _rls
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_processor import _providers

probe_role = _rls.probe_role

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def national_id(prefix9: str) -> str:
    total = sum(int(prefix9[i]) * (10 - i) for i in range(9)) % 11
    return prefix9 + str(total if total < 2 else 11 - total)


def card(prefix15: str) -> str:
    return next(prefix15 + d for d in "0123456789" if valid_luhn(prefix15 + d))


def iban(bban: str, country: str = "IR") -> str:
    return next(
        f"{country}{check:02d}{bban}"
        for check in range(100)
        if valid_iban(f"{country}{check:02d}{bban}")
    )


NID = national_id("001234567")
NID2 = national_id("274659381")
CARD = card("603799112345678")
CARD2 = card("627412345678901")
IBAN = iban("0190000000101234567890")


def _found(sample: str, classes=None) -> list[tuple[str, str]]:
    spans = detect(sample, classes or list(PiiClass))
    return [(s.pii_class.value, sample[s.start : s.end]) for s in spans]


# --- detector: formatting variants -------------------------------------------


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        (f"کد ملی {NID}", [("ir_national_id", NID)]),
        (f"کد ملی {NID.translate(FA)}", [("ir_national_id", NID.translate(FA))]),
        (f"کد ملی {NID.translate(AR)}", [("ir_national_id", NID.translate(AR))]),
        (
            f"کد ملی {NID[:3]}-{NID[3:9]}-{NID[9]}",
            [("ir_national_id", f"{NID[:3]}-{NID[3:9]}-{NID[9]}")],
        ),
        ("موبایل ۰۹۱۲۳۴۵۶۷۸۹", [("phone", "۰۹۱۲۳۴۵۶۷۸۹")]),
        ("موبایل ۰۹۱۲ ۳۴۵ ۶۷۸۹", [("phone", "۰۹۱۲ ۳۴۵ ۶۷۸۹")]),
        ("mobile +98 912 345 6789.", [("phone", "+98 912 345 6789")]),
        ("mobile +989123456789", [("phone", "+989123456789")]),
        ("mobile 0098-912-345-6789", [("phone", "0098-912-345-6789")]),
        ("تلفن ۰۲۱-۸۸۷۷۶۶۵۵", [("phone", "۰۲۱-۸۸۷۷۶۶۵۵")]),
        ("تلفن (۰۲۱) ۸۸۷۷ ۶۶۵۵", [("phone", "(۰۲۱) ۸۸۷۷ ۶۶۵۵")]),
        ("call +1 202 555 0143 today", [("phone", "+1 202 555 0143")]),
        (f"card {CARD}", [("bank_card", CARD)]),
        (
            f"کارت {CARD[:4]} {CARD[4:8]} {CARD[8:12]} {CARD[12:]}".translate(FA),
            [("bank_card", f"{CARD[:4]} {CARD[4:8]} {CARD[8:12]} {CARD[12:]}".translate(FA))],
        ),
        (f"شبا {IBAN}", [("iban", IBAN)]),
        (
            "شبا " + " ".join(IBAN[i : i + 4] for i in range(0, 26, 4)),
            [("iban", " ".join(IBAN[i : i + 4] for i in range(0, 26, 4)))],
        ),
        (f"iban {IBAN.lower()}", [("iban", IBAN.lower())]),
        ("mail Ali.Rezaei+crm@Example.co.ir now", [("email", "Ali.Rezaei+crm@Example.co.ir")]),
        ("ایمیل: sara_k@mail.example.com", [("email", "sara_k@mail.example.com")]),
        ("کد پستی ۱۴۳۵۶۷۸۹۱۲ است", [("ir_postal_code", "۱۴۳۵۶۷۸۹۱۲")]),
        ("postal code: 14356-78912", [("ir_postal_code", "14356-78912")]),
        # A keyword names the number even if it passes the national-ID check.
        (f"کدپستی {NID}", [("ir_postal_code", NID)]),
    ],
)
def test_supported_classes_and_formatting_variants(sample, expected):
    assert _found(sample) == expected


@pytest.mark.parametrize(
    "sample",
    [
        "مبلغ ۱۲۳۴۵۶۷۸۹۰ ریال پرداخت شد",  # ten digits, no check digit, no keyword
        "تاریخ ۱۴۰۳/۰۵/۱۲ ساعت ۱۰:۳۰",
        "سفارش INV1234567890 ثبت شد",  # identifier glued to letters
        "number 1234567890123456 is not a card",  # fails Luhn
        "0000000000 and 1111111111",  # repeated digits pass no validator
        "version 2.4.1 build 20260923",
        "قیمت ۴۵,۰۰۰,۰۰۰ تومان",
        "the IBAN IR000190000000101234567890 is mistyped",  # bad check digits
        "user@localhost is not a routable address",
        "۹۱۲۳۴۵۶۷۸۹",  # a bare mobile without prefix is too ambiguous
        f"x{NID}y",  # glued to letters on both sides
        "00123456",  # "00" + 6 digits is not an international number
        "واتساپ +۹۸۹۱۹۸۷۶۵۴۳۲۱",  # one digit too many for an Iranian mobile
        "کد پستی را بعدا می‌گویم؛ فعلا مبلغ ۱۲۳۴۵۶۷۸۹۰ ریال",  # keyword too far away
    ],
)
def test_hard_negatives_are_not_flagged(sample):
    assert _found(sample) == []


def test_configured_classes_limit_detection_and_unknown_classes_are_refused():
    sample = f"email a@b.io and national id {NID}"
    assert _found(sample, [PiiClass.EMAIL]) == [("email", "a@b.io")]
    assert parse_classes(["phone", "email", "phone"]) == [PiiClass.EMAIL, PiiClass.PHONE]
    with pytest.raises(ValueError, match="Unsupported"):
        parse_classes(["person_name"])


def test_placeholders_are_stable_per_value_and_offsets_refer_to_the_original():
    pseudo = Pseudonymizer()
    first, reps = redact("شماره ۰۹۱۲۳۴۵۶۷۸۹ و 09123456789 و 09351234567", pseudonymizer=pseudo)
    assert first == "شماره [PHONE_1] و [PHONE_1] و [PHONE_2]"
    assert [r.value for r in reps] == ["۰۹۱۲۳۴۵۶۷۸۹", "09123456789", "09351234567"]
    second, _ = redact(f"باز هم 09351234567 و {NID}", pseudonymizer=pseudo)
    assert second == "باز هم [PHONE_2] و [IR_NATIONAL_ID_1]"


# --- synthetic annotated evaluation ------------------------------------------

EVALUATION: list[tuple[str, list[tuple[str, str]]]] = [
    (f"سلام، کد ملی من {NID.translate(FA)} هست", [("ir_national_id", NID.translate(FA))]),
    (f"کد ملیم {NID2} ـه", [("ir_national_id", NID2)]),
    (f"my national id is {NID2}", [("ir_national_id", NID2)]),
    ("با شماره ۰۹۱۲۱۲۳۴۵۶۷ تماس بگیرید", [("phone", "۰۹۱۲۱۲۳۴۵۶۷")]),
    ("شماره همراه ۰۹۳۵-۱۲۳-۴۵۶۷", [("phone", "۰۹۳۵-۱۲۳-۴۵۶۷")]),
    ("واتساپ +۹۸۹۱۹۸۷۶۵۴۳۲", [("phone", "+۹۸۹۱۹۸۷۶۵۴۳۲")]),
    ("office line 021 2233 4455", [("phone", "021 2233 4455")]),
    ("تلفن شعبه اصفهان ۰۳۱۳۶۶۵۴۴۳۲", [("phone", "۰۳۱۳۶۶۵۴۴۳۲")]),
    ("reach me on +44 20 7946 0958", [("phone", "+44 20 7946 0958")]),
    (f"شماره کارت {CARD.translate(FA)} رو یادداشت کن", [("bank_card", CARD.translate(FA))]),
    (
        f"card number {CARD2[:4]}-{CARD2[4:8]}-{CARD2[8:12]}-{CARD2[12:]}",
        [("bank_card", f"{CARD2[:4]}-{CARD2[4:8]}-{CARD2[8:12]}-{CARD2[12:]}")],
    ),
    (f"شبای حساب {IBAN} است", [("iban", IBAN)]),
    ("ایمیل من reza.m@example.org است", [("email", "reza.m@example.org")]),
    ("send it to billing@shop.example.ir please", [("email", "billing@shop.example.ir")]),
    ("کد پستی منزل ۸۱۳۴۵۶۷۸۹۰ هست", [("ir_postal_code", "۸۱۳۴۵۶۷۸۹۰")]),
    (
        f"اطلاعات: موبایل ۰۹۱۲۳۳۳۴۴۵۵ ایمیل n.a@x.com کد ملی {NID}",
        [("phone", "۰۹۱۲۳۳۳۴۴۵۵"), ("email", "n.a@x.com"), ("ir_national_id", NID)],
    ),
    ("جلسه ساعت ۱۴:۰۰ روز ۱۴۰۳/۰۷/۰۱ برگزار شد", []),
    ("فاکتور شماره ۹۸۷۶۵ به مبلغ ۱۲۰۰۰۰۰۰۰ ریال", []),
    ("کد رهگیری ۱۲۳۴۵۶۷۸۹۰۱۲ صادر شد", []),
    ("we shipped 1500 units in Q3 2026", []),
    ("قرارداد ۱۴۰۲-۱۱۲۳ تمدید شد", []),
    ("the ticket is SUP-2026-0042", []),
]


def test_synthetic_annotated_set_meets_the_engineering_gate():
    true_positive = false_positive = false_negative = 0
    per_class: dict[str, list[int]] = {}
    for sample, expected in EVALUATION:
        found = set(_found(sample))
        wanted = set(expected)
        for item in found & wanted:
            per_class.setdefault(item[0], [0, 0, 0])[0] += 1
        for item in found - wanted:
            per_class.setdefault(item[0], [0, 0, 0])[1] += 1
        for item in wanted - found:
            per_class.setdefault(item[0], [0, 0, 0])[2] += 1
        true_positive += len(found & wanted)
        false_positive += len(found - wanted)
        false_negative += len(wanted - found)
    recall = true_positive / (true_positive + false_negative)
    precision = true_positive / (true_positive + false_positive)
    assert recall >= 0.95 and precision >= 0.90, (recall, precision, per_class)
    assert set(per_class) == {c.value for c in PiiClass}  # every class is exercised


# --- restricted mappings: encryption ------------------------------------------


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_mapping_cipher_binds_tenant_redaction_and_placeholder_and_rotates():
    old, new = _key(), _key()
    cipher_old = MappingCipher(parse_keyring(f"k1:{old}"), "k1")
    key_id, nonce, ciphertext = cipher_old.encrypt(
        "09123456789", workspace_id="w1", redaction_id="r1", placeholder="[PHONE_1]"
    )
    assert b"0912" not in ciphertext and key_id == "k1"
    rotated = MappingCipher(parse_keyring(f"k1:{old},k2:{new}"), "k2")
    kwargs = dict(key_id=key_id, nonce=nonce, ciphertext=ciphertext, redaction_id="r1")
    assert rotated.decrypt(**kwargs, workspace_id="w1", placeholder="[PHONE_1]") == "09123456789"
    with pytest.raises(MappingIntegrityError):
        rotated.decrypt(**kwargs, workspace_id="w2", placeholder="[PHONE_1]")
    with pytest.raises(MappingIntegrityError):
        rotated.decrypt(**kwargs, workspace_id="w1", placeholder="[PHONE_2]")
    with pytest.raises(MappingKeyUnavailable):
        MappingCipher(parse_keyring(f"k2:{new}"), "k2").decrypt(
            **kwargs, workspace_id="w1", placeholder="[PHONE_1]"
        )
    assert rotated.encrypt("x", workspace_id="w", redaction_id="r", placeholder="p")[0] == "k2"


@pytest.mark.parametrize(
    "keys",
    ["k1", "k1:not-base64!", f"k1:{base64.b64encode(b'short').decode()}", ":abc"],
)
def test_malformed_keyrings_are_refused(keys):
    with pytest.raises(ValueError):
        parse_keyring(keys)


def test_settings_require_the_active_key_to_exist(settings):
    data = settings.model_dump(context={REVEAL_SECRETS_CONTEXT: True})
    data.update(pii_mapping_keys=f"k1:{_key()}", pii_mapping_active_key_id="k2", _env_file=None)
    with pytest.raises(ValueError, match="PII_MAPPING_ACTIVE_KEY_ID"):
        Settings(**data)


# --- redacted versions in the database ------------------------------------------

PII_TRANSCRIPT = (
    f"سلام، من علی هستم. کد ملی {NID.translate(FA)} و موبایل ۰۹۱۲ ۳۴۵ ۶۷۸۹. "
    "ایمیل ali.rezaei@example.com است و دوباره ۰۹۱۲۳۴۵۶۷۸۹ را تکرار می‌کنم."
)


@pytest.fixture
def keyed_settings(settings):
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{_key()}", "pii_mapping_active_key_id": "k1"}
    )


def _processed_with_pii(client, email_outbox, monkeypatch, settings, label: str):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    accepted = _upload(client, tenant, key=f"{label}-0001")
    assert accepted.status_code == 202, accepted.text
    providers = _providers(
        MockASRProvider(allow_mock=True, transcript=PII_TRANSCRIPT, language="fa")
    )
    result = process_job(
        accepted.json()["job"]["id"], worker_id=label, settings=settings, providers=providers
    )
    assert result["status"] == "completed", result
    return tenant, accepted.json()["meeting"]["id"]


def test_redacted_version_keeps_originals_only_as_ciphertext_and_reveal_is_audited(
    client, email_outbox, monkeypatch, keyed_settings, caplog
):
    tenant, meeting_id = _processed_with_pii(
        client, email_outbox, monkeypatch, keyed_settings, "pii-store"
    )
    workspace_id = tenant["workspace_id"]
    caplog.set_level(logging.DEBUG)
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=workspace_id, meeting_id=meeting_id)
        redaction = redact_revision(
            db,
            revision=revision,
            classes=list(PiiClass),
            settings=keyed_settings,
            now=datetime.now(UTC),
        )
        db.commit()
        assert redaction.detector_version == DETECTOR_VERSION
        assert redaction.mapping_retained and redaction.entity_count == 4
        texts = [row.text for row in redacted_segments(db, redaction=redaction)]
        joined = " ".join(texts)
        assert "[IR_NATIONAL_ID_1]" in joined and joined.count("[PHONE_1]") == 2
        assert "[EMAIL_1]" in joined and "0912" not in joined and "۰۹۱۲" not in joined
        assert "ali.rezaei" not in joined and NID.translate(FA) not in joined
        mappings = list(db.scalars(select(PiiMapping).where(PiiMapping.meeting_id == meeting_id)))
        assert len(mappings) == 4 and {m.key_id for m in mappings} == {"k1"}
        assert all(b"0912" not in m.ciphertext and b"example" not in m.ciphertext for m in mappings)

        # Idempotent per policy; a different class list is a new version.
        again = redact_revision(
            db,
            revision=revision,
            classes=list(PiiClass),
            settings=keyed_settings,
            now=datetime.now(UTC),
        )
        assert again.id == redaction.id
        email_only = redact_revision(
            db,
            revision=revision,
            classes=[PiiClass.EMAIL],
            settings=keyed_settings,
            now=datetime.now(UTC),
        )
        db.commit()
        assert email_only.id != redaction.id and email_only.entity_count == 1

        # Rows are immutable once written.
        row = redacted_segments(db, redaction=redaction)[0]
        row.text = "rewritten"
        with pytest.raises(TranscriptImmutableError):
            db.flush()
        db.rollback()

        value = reveal_placeholder(
            db,
            redaction=redaction,
            placeholder="[PHONE_1]",
            actor_user_id=tenant["user_id"],
            reason="customer callback",
            settings=keyed_settings,
        )
        db.commit()
        assert value == "۰۹۱۲ ۳۴۵ ۶۷۸۹"
        with pytest.raises(RedactionError) as missing:
            reveal_placeholder(
                db,
                redaction=redaction,
                placeholder="[PHONE_1]",
                actor_user_id=tenant["user_id"],
                reason="no key",
                settings=keyed_settings.model_copy(
                    update={"pii_mapping_keys": "", "pii_mapping_active_key_id": ""}
                ),
            )
        assert missing.value.code == "mapping_key_unavailable"
        audits = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type.in_(["pii.revealed", "transcript.redaction_created"])
                )
            )
        )
    assert {a.event_type for a in audits} == {"pii.revealed", "transcript.redaction_created"}
    for audit in audits:
        blob = repr(audit.details)
        assert "0912" not in blob and "۰۹۱۲" not in blob and "ali.rezaei" not in blob
    logged = caplog.text
    assert "0912" not in logged and "۰۹۱۲" not in logged and "ali.rezaei" not in logged


def test_without_a_key_nothing_original_is_retained(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _processed_with_pii(
        client, email_outbox, monkeypatch, settings, "pii-nokey"
    )
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        redaction = redact_revision(
            db,
            revision=revision,
            classes=list(PiiClass),
            settings=settings,
            now=datetime.now(UTC),
        )
        db.commit()
        assert not redaction.mapping_retained and redaction.entity_count == 4
        assert db.scalar(select(func.count(PiiMapping.id))) == 0
        with pytest.raises(RedactionError) as refused:
            reveal_placeholder(
                db,
                redaction=redaction,
                placeholder="[PHONE_1]",
                actor_user_id=tenant["user_id"],
                reason="try",
                settings=settings,
            )
        assert refused.value.code == "mapping_not_retained"


def test_the_purge_removes_redacted_versions_and_mappings(
    client, email_outbox, monkeypatch, keyed_settings
):
    tenant, meeting_id = _processed_with_pii(
        client, email_outbox, monkeypatch, keyed_settings, "pii-purge"
    )
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        redact_revision(
            db,
            revision=revision,
            classes=list(PiiClass),
            settings=keyed_settings,
            now=datetime.now(UTC),
        )
        db.commit()
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204
    with inspection_session_factory()() as db:
        for model in (TranscriptRedaction, RedactedSegment, PiiMapping):
            count = db.scalar(select(func.count(model.id)).where(model.meeting_id == meeting_id))
            assert count == 0, model.__tablename__


# --- workspace policy ------------------------------------------------------------


def test_workspace_policy_is_explicit_and_validated(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "pii-policy")

    def patch(payload):
        return client.patch(
            "/api/v1/admin/workspace-settings",
            json=payload,
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )

    current = client.get(
        "/api/v1/admin/workspace-settings", headers=_headers(tenant), cookies=tenant["cookies"]
    ).json()
    assert current["pii_redaction_enabled"] is False
    assert sorted(current["pii_redaction_classes"]) == sorted(c.value for c in PiiClass)
    updated = patch({"pii_redaction_enabled": True, "pii_redaction_classes": ["phone", "email"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["pii_redaction_classes"] == ["email", "phone"]
    assert patch({"pii_redaction_classes": ["person_name"]}).status_code in {400, 422}
    assert patch({"pii_redaction_classes": []}).status_code in {400, 422}
    assert patch({"pii_redaction_enabled": "yes"}).status_code == 422
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace.pii_redaction_enabled is True
        assert workspace.pii_redaction_classes == ["email", "phone"]


def test_member_cannot_change_the_privacy_policy(client, email_outbox, monkeypatch):
    from backend.tests.test_authorization_matrix import _workspace_with_roles

    _patch_media(monkeypatch)
    _owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    response = personas["member"].patch(
        "/api/v1/admin/workspace-settings", json={"pii_redaction_enabled": False}
    )
    assert response.status_code == 403, response.text


@pytest.mark.postgres
def test_rls_binds_redactions_and_mappings_to_the_tenant(
    client, email_outbox, monkeypatch, keyed_settings, probe_role
):
    tenant, meeting_id = _processed_with_pii(
        client, email_outbox, monkeypatch, keyed_settings, "pii-rls"
    )
    other = _tenant(client, email_outbox, "pii-rls-other")
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        redact_revision(
            db,
            revision=revision,
            classes=list(PiiClass),
            settings=keyed_settings,
            now=datetime.now(UTC),
        )
        db.commit()
    engine = create_engine(probe_role, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            for table in ("transcript_redactions", "redacted_segments", "pii_mappings"):
                assert _rls._count(connection, table) == 0
            _rls._bind(connection, workspace_id=other["workspace_id"])
            for table in ("transcript_redactions", "redacted_segments", "pii_mappings"):
                assert _rls._count(connection, table) == 0
            connection.rollback()
            _rls._bind(connection, workspace_id=tenant["workspace_id"])
            assert _rls._count(connection, "pii_mappings") == 4
            touched = connection.execute(
                text("UPDATE pii_mappings SET key_id = 'x' WHERE workspace_id = :w"),
                {"w": other["workspace_id"]},
            )
            assert touched.rowcount == 0
            connection.rollback()
    finally:
        engine.dispose()


# --- HOM-164: postal-code keyword in Arabic letters or with ZWNJ ---------------

POSTAL_DIGITS = "۱۴۳۵۶۷۸۹۱۲"


@pytest.mark.parametrize(
    "keyword",
    [
        "کد پستی",
        "كد پستي",  # Arabic kaf and yeh
        "كد پستی",  # Arabic kaf only
        "کد پستي",  # Arabic yeh only
        "کد‌پستی",  # ZWNJ instead of a space
        "كد‌پستي",  # both
        "کدپستی",
        "كدپستي",
    ],
)
def test_postal_keyword_spellings_are_detected_in_original_offsets(keyword):
    sample = f"{keyword} {POSTAL_DIGITS} است"
    spans = detect(sample)
    start = len(keyword) + 1
    assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
        (start, start + 10, PiiClass.IR_POSTAL_CODE, "1435678912")
    ]
    redacted, replacements = redact(sample)
    assert redacted == f"{keyword} [IR_POSTAL_CODE_1] است"
    assert [sample[r.start : r.end] for r in replacements] == [POSTAL_DIGITS]


@pytest.mark.parametrize(
    "sample",
    [
        # Deny: no keyword, or the variant letters spell a different word.
        f"كد {POSTAL_DIGITS}",
        f"پستي {POSTAL_DIGITS}",
        f"كد‌‌ملی {POSTAL_DIGITS}",
        f"كد پستي را بعدا می‌گویم؛ فعلا مبلغ {POSTAL_DIGITS} ریال",
    ],
)
def test_postal_keyword_variants_do_not_widen_the_window(sample):
    assert [s for s in detect(sample) if s.pii_class is PiiClass.IR_POSTAL_CODE] == []


def test_detector_version_covers_the_postal_keyword_change():
    assert DETECTOR_VERSION != "text-pii/1"


def test_a_stored_v1_version_is_kept_and_a_new_version_is_derived(
    client, email_outbox, monkeypatch, keyed_settings
):
    from backend.privacy import redaction_service

    tenant, meeting_id = _processed_with_pii(
        client, email_outbox, monkeypatch, keyed_settings, "pii-v1-v2"
    )
    workspace_id = tenant["workspace_id"]
    v1_digest = redaction_service.policy_digest(list(PiiClass), detector_version="text-pii/1")
    assert v1_digest != redaction_service.policy_digest(list(PiiClass))
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=workspace_id, meeting_id=meeting_id)
        # A version written by the previous detector release.
        with monkeypatch.context() as old:
            old.setattr(redaction_service, "DETECTOR_VERSION", "text-pii/1")
            old.setattr(
                redaction_service,
                "policy_digest",
                lambda classes: v1_digest,
            )
            v1 = redact_revision(
                db,
                revision=revision,
                classes=list(PiiClass),
                settings=keyed_settings,
                now=datetime.now(UTC),
            )
            db.commit()
        v1_id = v1.id
        v1_texts = [row.text for row in redacted_segments(db, redaction=v1)]
        assert (v1.detector_version, v1.policy_digest) == ("text-pii/1", v1_digest)

        v2 = redact_revision(
            db,
            revision=revision,
            classes=list(PiiClass),
            settings=keyed_settings,
            now=datetime.now(UTC),
        )
        db.commit()
        assert v2.id != v1_id
        assert v2.detector_version == DETECTOR_VERSION
        assert v2.policy_digest == redaction_service.policy_digest(list(PiiClass))

    with inspection_session_factory()() as db:
        stored = db.get(TranscriptRedaction, v1_id)
        assert (stored.detector_version, stored.policy_digest) == ("text-pii/1", v1_digest)
        assert [row.text for row in redacted_segments(db, redaction=stored)] == v1_texts
        versions = db.scalars(
            select(TranscriptRedaction.detector_version).where(
                TranscriptRedaction.meeting_id == meeting_id
            )
        ).all()
        assert sorted(versions) == ["text-pii/1", DETECTOR_VERSION]


# --- HOM-165: fullwidth digits are canonical, Devanagari is not ----------------

FW = str.maketrans("0123456789", "".join(chr(0xFF10 + i) for i in range(10)))
DEVANAGARI = str.maketrans("0123456789", "".join(chr(0x0966 + i) for i in range(10)))


@pytest.mark.parametrize(
    ("sample", "pii_class", "value"),
    [
        ("کد ملی " + NID.translate(FW), PiiClass.IR_NATIONAL_ID, NID),
        ("موبایل " + "09123456789".translate(FW), PiiClass.PHONE, "09123456789"),
        ("موبایل " + "0912 345 6789".translate(FW), PiiClass.PHONE, "09123456789"),
        ("mobile +" + "989123456789".translate(FW), PiiClass.PHONE, "09123456789"),
        ("تلفن " + "021-88776655".translate(FW), PiiClass.PHONE, "02188776655"),
        ("card " + CARD.translate(FW), PiiClass.BANK_CARD, CARD),
        ("شبا IR" + IBAN[2:].translate(FW), PiiClass.IBAN, IBAN),
        ("کد پستی " + "1435678912".translate(FW), PiiClass.IR_POSTAL_CODE, "1435678912"),
    ],
)
def test_fullwidth_digits_are_detected_and_stored_as_ascii(sample, pii_class, value):
    spans = detect(sample)
    assert [(s.pii_class, s.value) for s in spans] == [(pii_class, value)]
    redacted, replacements = redact(sample)
    assert [sample[r.start : r.end] for r in replacements] == [replacements[0].value]
    assert replacements[0].value != value  # the original keeps its spelling
    assert redacted.endswith("]")


@pytest.mark.parametrize(
    "sample",
    [
        "کد ملی " + NID.translate(DEVANAGARI),
        "موبایل " + "09123456789".translate(DEVANAGARI),
        "card " + CARD.translate(DEVANAGARI),
        "کد پستی " + "1435678912".translate(DEVANAGARI),
        "mobile +" + "989123456789".translate(DEVANAGARI),
    ],
)
def test_devanagari_digits_are_not_folded_or_detected(sample):
    assert detect(sample) == []


def test_mixed_scripts_fold_to_one_value():
    mixed = "۰۹۱۲" + "345".translate(FW) + "٦٧٨٩"
    assert [s.value for s in detect("موبایل " + mixed)] == ["09123456789"]


def test_detector_version_covers_the_digit_change():
    assert DETECTOR_VERSION not in {"text-pii/1", "text-pii/2"}


# --- HOM-171: harakat and tatweel in the postal keyword; non-canonical scripts --

BENGALI = str.maketrans("0123456789", "".join(chr(0x09E6 + i) for i in range(10)))


@pytest.mark.parametrize(
    "keyword",
    [
        "کدّ پستی",  # shadda
        "کدِ پستی",  # kasra of the ezafe
        "کد پُستی",  # damma
        "کـد پستی",  # tatweel
        "کد پســتی",  # tatweel run
        "كدّ پستي",  # Arabic kaf/yeh and shadda
        "کدِ‌پستی",  # kasra and ZWNJ
    ],
)
def test_postal_keyword_with_harakat_or_tatweel_is_detected_in_original_offsets(keyword):
    # 1435678912 fails the national-ID check digit: before text-pii/4 these
    # spellings left it in the redacted output.
    sample = f"{keyword} {POSTAL_DIGITS} است"
    start = len(keyword) + 1
    assert [(s.start, s.end, s.pii_class, s.value) for s in detect(sample)] == [
        (start, start + 10, PiiClass.IR_POSTAL_CODE, "1435678912")
    ]
    redacted, replacements = redact(sample)
    assert redacted == f"{keyword} [IR_POSTAL_CODE_1] است"
    assert [sample[r.start : r.end] for r in replacements] == [POSTAL_DIGITS]


def test_marks_never_turn_another_word_into_the_postal_keyword():
    assert detect(f"کدّ {POSTAL_DIGITS}") == []
    assert detect(f"پُستی {POSTAL_DIGITS}") == []


@pytest.mark.parametrize(
    "sample",
    [
        "کد ملی " + NID.translate(BENGALI),
        "موبایل " + "09123456789".translate(BENGALI),
        "کد پستی " + "1435678912".translate(BENGALI),
    ],
)
def test_a_non_devanagari_script_is_not_folded_or_detected_either(sample):
    assert detect(sample) == []


def test_detector_version_covers_the_keyword_marks():
    assert DETECTOR_VERSION not in {"text-pii/1", "text-pii/2", "text-pii/3"}


def test_detector_version_covers_the_id_next_to_a_phone():
    # HOM-291: stored redactions made before the split are derived again.
    # HOM-303: any version after text-pii/4 holds the split.
    assert DETECTOR_VERSION not in {"text-pii/1", "text-pii/2", "text-pii/3", "text-pii/4"}


def test_detector_version_covers_the_glued_runs():
    # HOM-303: stored redactions made before the written-form readings are derived again.
    # HOM-308: any version after text-pii/5 holds the written-form readings.
    assert DETECTOR_VERSION not in {
        "text-pii/1",
        "text-pii/2",
        "text-pii/3",
        "text-pii/4",
        "text-pii/5",
    }


def test_detector_version_covers_the_iban_before_a_latin_word():
    # HOM-308: stored redactions made before an IBAN was read without the word after it
    # are derived again.
    assert DETECTOR_VERSION == "text-pii/7"
