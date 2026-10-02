"""WP22-T02 (HOM-120): stored raw evidence is never normalised.

The upload pipeline stores the transcript exactly as the ASR provider returned
it, Arabic letter variants, native digits and format characters included, and
redaction offsets refer to that stored text.  Passes at base ``7112407`` and
after the normalisation owner migration.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

from sqlalchemy import select

from backend.database.saas_models import PiiMapping, TranscriptSegment
from backend.privacy.mapping_crypto import MappingCipher
from backend.privacy.redaction_service import redact_revision, redacted_segments
from backend.privacy.text_pii import PiiClass, detect
from backend.services.transcript_service import current_revision
from backend.tests import test_text_pii
from backend.tests._database import inspection_session_factory

# Arabic yeh/kaf/teh marbuta, heh with yeh above, waw with hamza, Persian and
# Arabic-Indic digits, ZWNJ, fatha, tatweel, RLM and a Persian-digit mobile.
EVIDENCE_TRANSCRIPT = (
    "‏سلام، من علي از شركت رؤیاـی خانۀ مدرسة هستم. "
    "کد پستی ۱۴۳۵۶۷۸۹۱۲ و موبایل "
    "۰۹۱۲ ۳۴۵ ۶۷۸۹، کد ملی "
    "٠٠١٢٣٤٥٦٧٩ را مَی‌گویم."
)


def _stored(db, revision_id) -> list[TranscriptSegment]:
    return list(
        db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.revision_id == revision_id)
            .order_by(TranscriptSegment.sequence.asc())
        )
    )


def test_stored_transcript_keeps_every_code_point_through_redaction(
    client, email_outbox, monkeypatch, settings
):
    key = base64.b64encode(os.urandom(32)).decode()
    keyed = settings.model_copy(
        update={"pii_mapping_keys": f"k1:{key}", "pii_mapping_active_key_id": "k1"}
    )
    supplied = EVIDENCE_TRANSCRIPT.strip()
    monkeypatch.setattr(test_text_pii, "PII_TRANSCRIPT", EVIDENCE_TRANSCRIPT)
    tenant, meeting_id = test_text_pii._processed_with_pii(
        client, email_outbox, monkeypatch, keyed, "hom120-evidence"
    )
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        segments = _stored(db, revision.id)
        assert [segment.text for segment in segments] == [supplied]
        assert "ی" + " از" not in segments[0].text  # the supplied Arabic yeh is kept

        redaction = redact_revision(
            db, revision=revision, classes=list(PiiClass), settings=keyed, now=datetime.now(UTC)
        )
        db.commit()
        assert redaction.entity_count == len(detect(supplied)) == 3

        by_id = {segment.id: segment.text for segment in segments}
        cipher = MappingCipher.from_settings(keyed)
        mappings = list(
            db.scalars(select(PiiMapping).where(PiiMapping.redaction_id == redaction.id))
        )
        assert len(mappings) == 3
        for mapping in mappings:
            original = cipher.decrypt(
                key_id=mapping.key_id,
                nonce=mapping.nonce,
                ciphertext=mapping.ciphertext,
                workspace_id=mapping.workspace_id,
                redaction_id=redaction.id,
                placeholder=mapping.placeholder,
            )
            assert original == by_id[mapping.segment_id][mapping.start_offset : mapping.end_offset]

        redacted = " ".join(row.text for row in redacted_segments(db, redaction=redaction))
        for placeholder in ("[PHONE_1]", "[IR_POSTAL_CODE_1]", "[IR_NATIONAL_ID_1]"):
            assert placeholder in redacted
        assert [segment.text for segment in _stored(db, revision.id)] == [supplied]
