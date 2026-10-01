# SPEC — team wp22t02-t1 (mission 20260929-backend-mode-100)  [DRAFT — drafted by wp22t02-drafter, base 7112407]

Orchestration mode: `foreman_native_adapted`. Role separation, blind oracle, bare-diff review, two-round cap.
Lane: L2 (`LANE=2`: PostgreSQL 127.0.0.1:5434, Redis dbs 4 (app) and 5 (`TEST_REDIS_URL`), temp dirs `/tmp/homayar-l2-*`,
lock `/root/foreman/20260929-backend-mode-100/lane-2.lock`; source `testenv-l2.sh`).
Final-reviewer trigger: **PII** — the unit edits the PII detector module `backend/privacy/text_pii.py` (and, if the lock
request is granted, OTP verification in `backend/routers/v1/auth.py`). Final reviewer: **security-vulnerability-scanner**,
focused on "detector and redaction output byte-identical, offsets unchanged, no OTP acceptance widened".

## Task
- Linear: HOM-120 [WP22-T02] (parent WP22). Plan row 4: "Single normalization owner (speech/text_normalize.py) +
  locale-aware dates/numbers; migrate duplicates with golden no-behaviour-change tests; raw evidence untouched".
  Priority P1, stabilization flag S, heaviest blocker (36 transitive dependents: WP11-T03, WP11-T05, WP17-T02 build on it).
- Type: Implementation (behaviour-preserving refactor). **One root cause:** Persian/Arabic digit and letter normalization
  has no owner. Six copies (a seventh found, see Context 9) define their own digit maps, letter-variant maps, diacritic
  classes and invisible-character sets. They already differ, and new consumers would each pick a copy.
- **This SPEC covers:** make `backend/speech/text_normalize.py` the single definition site of those tables and functions.
  Migrate the copies to import from it with **no behaviour change**. Prove it with golden tests captured from BASE_SHA.
  Stored raw evidence is untouched.
- **Split out and deferred (second root cause): "locale-aware dates/numbers".** This is about formatting output for a
  resolved locale: Jalali or Gregorian dates, Persian or Latin digits and separators in API responses and exports. It is
  not the same as normalizing input. There is no production consumer today: nothing outside `backend/speech/` imports
  `backend.speech` (grep, Context 10). It also depends on locale resolution (WP22-T01, N13: `users.locale`,
  Accept-Language). Recommendation: Foreman records it as a follow-up child of HOM-120 (e.g. "WP22-T02b locale-aware
  date/number formatting", depends on WP22-T01). It is not in this unit.
- Also out of scope: *unifying* the behavioural differences between the copies (Context table). That would be a
  behaviour change that needs version bumps and a data migration (Out of scope finding 3).

## Base
`BASE_SHA` = `7112407` (integration head: HOM-32 landed; checkout `/home/user/Homayar.com` is at it, clean).

## Context (verified by the drafter against source at 7112407; paths relative to `Homayar/backend/` unless absolute)
Probes ran with the repository venv (`Homayar/.venv`, Python 3.12.3, `unicodedata` 15.0.0) from a scratch dir with
`PYTHONDONTWRITEBYTECODE=1`. Nothing in the repository was written.
1. **Owner candidate** `speech/text_normalize.py`:
   - It imports `normalize_digits` FROM `privacy/text_pii.py` (`:26`). As a result, importing it loads
     `backend.privacy.text_pii` (probe). It is not a leaf today.
   - `_ARABIC_TO_PERSIAN` (`:30-43`) has 10 entries: ي ى→ی, ك→ک, ة ۀ→ه, أ إ ٱ ٲ(U+0672)→ا, ؤ→و. `unify_arabic_letters`
     (`:101-102`) is a length-preserving `translate`.
   - `_DIACRITICS_RE` (`:46`) = `[U+064B-U+065F U+0670 U+0640]` (tatweel included).
   - `normalize_fa` (`:137-156`) runs in this order: NFC → letters → diacritics → digits → whitespace → [colloquial]
     → ZWNJ repair → punctuation. It KEEPS ZWNJ as a joiner and does not remove LRM/RLM.
   - Other functions: `normalize_en` `:159`, `normalize_az` `:169`, `normalize_for_matching` `:180`, registry
     `:196-227`.
   - `backend/speech/__init__.py` has only a docstring.
2. **`locations/textnorm.py`** (offset-preserving stream):
   - `normalize_digits` comes from text_pii (`:14`).
   - `_CHAR_MAP` (`:16-33`) has 7 letters (NO ۀ, ؤ or ٲ). It maps ZWNJ→space; drops ZWJ, RLM, LRM and tatweel; maps
     ٫→"." ٬→"," ،→","; and maps NBSP→space.
   - `_DROP` (`:35`) = U+064B–U+065F plus U+0670.
   - `normalize_char_stream` (`:53-75`) has no Unicode normalization. It walks the text char by char, lowercases ASCII
     letters only, collapses whitespace, and records `offsets[i]` = the original index.
   - `:3-7` holds a `TODO(consolidate)` asking to fold the module into the shared normalizer "keeping the offset-map
     contract".
   - Consumers: `locations/extract.py:176-177,560` (spans into the original), `gazetteer.py:45,70`,
     `routing.py:166-184`, `normalize.py:45,52`, `service.py:160-162`.
3. **Location normalization is PERSISTED.**
   - `locations/normalize.py:42-46,69-74` stores `normalize_text(...)` output as the address fields.
   - `locations/service.py:64-71` writes them onto `ServiceLocation`.
   - `list_locations` filters `ServiceLocation.city == normalize_text(city)` (`service.py:159-162`).
   - Any change to the locations profile silently breaks equality lookups on existing rows. `address_text` is stored raw
     (`normalize.py:74`).
4. **`voicemail/classify.py`**:
   - `RULES_VERSION = "vm-rules-2026.09.1"` (`:26`).
   - `_CHAR_MAP` (`:30-49`) has 9 letters (with ۀ and ؤ, NO ٲ), ZWNJ→space, drops ZWJ/RLM/LRM/tatweel, and 20 digits
     built from `0x06F0`/`0x0660` arithmetic (`:46-47`).
   - `_DIACRITICS` (`:50`) = `[U+064B-U+065F U+0670 U+06D6-U+06ED]` (Quranic marks included).
   - `normalize_text` (`:55-60`) runs NFKC → map → diacritics → casefold → non-word→space → collapse.
   - Consumers: the rule patterns (`:63-68`, compiled at import `:307-311`), `is_empty_transcript`/`RuleClassifier`,
     `voicemail/search.py:45` (LIKE variants over stored segments), and `voicemail/pipeline.py:157-168`. The pipeline
     skips reclassification when `classifier_version` equals the stored one, so a behaviour change under an unchanged
     version would silently leave stale rows.
   - Rule data contains a Persian digit (`:207` "عدد ۱ را").
5. **`privacy/text_pii.py`**:
   - `DETECTOR_VERSION = "text-pii/1"` (`:29`).
   - `_DIGITS` (`:53`, 20 code points) and `normalize_digits` (`:60-62`, 1:1, so offsets are kept).
   - `detect` normalizes digits only (`:181`), then runs regexes that use Unicode `\d` with ASCII-only boundaries
     (`:55-67,76-106`).
   - Redaction identity: `policy_digest` = sha256 of `detector_version|classes` (`privacy/redaction_service.py:52-54`).
     A redacted version is looked up by `(revision, policy_digest)` and never rewritten (`redaction_service.py:1-10`;
     immutability asserted at `tests/test_text_pii.py:354-359`).
   - Other importers of text_pii: `jobs/processor.py:86`, `privacy/{redaction_service,subject_export,transcript_view}.py`,
     `speech/entities.py:21`, and `speech/dates.py:31`, which imports `normalize_digits` from text_pii.
6. **`security/normalization.py`**:
   - Two digit tables (`:8-9`). `normalize_iranian_mobile` runs NFKC → Persian → Arabic-Indic digits (`:21`), so
     fullwidth digits are accepted through NFKC.
   - Consumers: `services/otp_service.py:59`, `services/share_service.py:168`, `voicemail/ingest.py:96`.
   - `backend/security/__init__.py` imports `security.tokens` (no speech/privacy import).
7. **`routers/v1/auth.py:616-618`**: OTP `code` is translated with an inline 20-digit `str.maketrans` before
   `verify_otp`. `schemas/auth.py:56` already restricts `code` to `^[0-9۰-۹٠-٩]{6}$`. No existing test verifies an OTP
   typed in Persian or Arabic-Indic digits (`tests/test_api.py:232-249` uses the ASCII code).
8. **`speech/dates.py:248-250`** `_shadow` = `unify_arabic_letters(normalize_digits(text)).replace(ZWNJ, " ")`. It is
   documented "Same length as text", so it depends on both functions staying 1:1. It is a composition of owner functions,
   not a copy, and it is not in scope.
9. **Seventh copy, not in PLAN §3:** `voicemail/ingest.py:99-101` builds a digit table from `0x06F0`/`0x0660` arithmetic
   for non-mobile caller numbers. It then filters with `isdigit()`, and the stored `voicemail_messages.caller_number` is
   used for repeat-caller matching (`voicemail/models.py:102`). It is not in the lock set (see Lock request).
10. **Repository-wide copy scan** (`grep -E "maketrans|0x06F0|0x0660"` over non-test `backend/`):
    - Outside the owner there are 9 hits: `routers/v1/auth.py:617`; `voicemail/classify.py:30,46,47`;
      `voicemail/ingest.py:101`; `privacy/text_pii.py:53`; `security/normalization.py:8,9`; `locations/textnorm.py:35`.
    - Inside the owner: `speech/text_normalize.py:30,47,166`.
    - No module outside `backend/speech/` imports `backend.speech`. Private tables (`_DIGITS`, `_CHAR_MAP`, `_DROP`,
      `_ARABIC_TO_PERSIAN`, `_DIACRITICS*`) are referenced nowhere else, tests included.
11. **Python / Unicode:**
    - CI `PYTHON_VERSION: "3.12"` (`.github/workflows/ci.yml:19`), `Homayar/.python-version` 3.12, and
      `requires-python = ">=3.12,<3.14"` (`Homayar/pyproject.toml:5`). NFC/NFKC, `isalpha`, `casefold` and `\w` depend on
      the Unicode version (15.0 on 3.12, 15.1 on 3.13).
    - Runtime at base: an exhaustive sweep of 12 functions × 1 template × all BMP code points took about 4 s in total.
12. **Existing tests:** `tests/test_speech_normalize.py` (118 lines), `tests/test_locations_extract.py:32-45`,
    `tests/test_voicemail_classify.py:22-27,58`, `tests/test_text_pii.py:96-156` (formatting variants, hard negatives),
    `:278-281` `PII_TRANSCRIPT`, `:291` `_processed_with_pii` (upload → `process_job` with `MockASRProvider`, which
    returns one segment equal to `transcript.strip()`, `providers/mocks.py:58-86`), and `:306-396` (DB redaction).

**Divergence table (verified at base; every cell must be identical after the change).**

| Input (escapes = code points) | speech `normalize_fa` | locations `normalize_char_stream` → text / offsets | voicemail `normalize_text` |
|---|---|---|---|
| `"خانۀ"` (U+06C0) | `"خانه"` | `"خانۀ"` / (0,1,2,3) | `"خانه"` |
| `"مؤسسه"` (U+0624) | `"موسسه"` | `"مؤسسه"` | `"موسسه"` |
| `"ٲ"` | `"ا"` | `"ٲ"` | `"ٲ"` |
| `"آب"` | `"آب"` (NFC) | `"اب"` / (0,2) | `"آب"` (NFKC) |
| `"ئ"` | `"ئ"` (NFC before letter map) | `"ی"` | `"ئ"` |
| `"ﻱﻮ"` (presentation forms) | unchanged | unchanged / (0,1) | `"یو"` |
| `"می‌روم"` | unchanged (ZWNJ kept) | `"می روم"` | `"می روم"` |
| `"‏سلام‎"` | unchanged | `"سلام"` / (1,2,3,4) | `"سلام"` |
| `"۳۵٫۷، ۵۱٬۴"` | `"35٫7، 51٬4"` | `"35.7, 51,4"` | `"35 7 51 4"` |
| `"１２３"` (fullwidth) | unchanged | unchanged | `"123"` |
| `"سلام۝ا"` | unchanged | unchanged | `"سلاما"` |
| `"تهـــران"` | `"تهران"` | `"تهران"` / (0,1,5,6,7) | `"تهران"` |
| `"ABC Tehran"` | `"ABC Tehran"` | `"abc tehran"` | `"abc tehran"` |
| `"خيابان  ولي‌عصر، پلاك ۱۲"` | — | `"خیابان ولی عصر, پلاک 12"`; `span(7,14) == (8,15)` = original `"ولي‌عصر"` | — |

`normalize_digits` maps only U+06F0–U+06F9 and U+0660–U+0669. `"۳۵٫۷، ۵۱٬۴"` → `"35٫7، 51٬4"`; `"１２３"` is unchanged.
Also at base:
- `normalize_iranian_mobile`: `"۰۹۱۲۳۴۵۶۷۸۹"`, `"٠٩١٢٣٤٥٦٧٨٩"`, `"０９１２３４５６７８９"` and `"+۹۸ ۹۱۲ ۳۴۵ ۶۷۸۹"` all give
  `"+989123456789"`. `"۰۹۱۲۳۴۵۶۷۸"` (10 digits) raises `ValueError("Invalid Iranian mobile number")`.
- `detect("شماره من ۰۹۱۲ ۳۴۵ ۶۷۸۹ است")` → one PHONE span (9, 22) with value `"09123456789"`. `redact` of the same text
  → `"شماره من [PHONE_1] است"`, with replacement value `"۰۹۱۲ ۳۴۵ ۶۷۸۹"`.
- `detect("کد پستی ۱۴۳۵۶۷۸۹۱۲ است")` → IR_POSTAL_CODE (8, 18) `"1435678912"`.
- These return `[]`: `detect("كد پستي ۱۴۳۵۶۷۸۹۱۲ است")` (Arabic kaf and yeh), `detect("کد‌پستی ۱۴۳۵۶۷۸۹۱۲ است")`,
  and `detect("موبایل ０９１２３４５６７８９")`.
- `detect("کد " + fullwidth("1000000001") + " تمام")` → IR_NATIONAL_ID (3, 13) with the fullwidth value
  `"１００００００００１"`.
- `policy_digest(list(PiiClass))` = `959fd446dfa18828da8543a05eefb4e4be20bb0cbbcbf84b92f45b58b3ed35ad`.
  `policy_digest([PiiClass.EMAIL])` = `39f06a1d558b0deec7a12df2d3abaf5688443ae31aa770d3e78d333058cdd1d8`.
- `voicemail.classify.normalize_text(None)` → `""`.
- `voicemail.ingest.normalize_caller_number`:
  - `"۰۲۱-۸۸۷۷۶۶۵۵"` → `("02188776655", False)`
  - `"٠٩١٢٣٤٥٦٧٨٩"` → `("+989123456789", True)`
  - `"+۴۴ ۲۰ ۷۹۴۶ ۰۰۰۰"` → `("+442079460000", False)`
  - `"ext ۱۲۳"` → `("123", False)`
  - `""` and `None` → `(None, False)`
  - `"０２１８８７７６６５５"` → `("０２１８８７７６６５５", False)`

## Acceptance criteria (observable)
1. **Single definition site.**
   a. `backend.speech.text_normalize.normalize_digits.__module__ == "backend.speech.text_normalize"` (at base it is
      `"backend.privacy.text_pii"`). Also `backend.privacy.text_pii.normalize_digits is
      backend.speech.text_normalize.normalize_digits`, because the name stays importable from text_pii
      (`speech/dates.py:31`).
   b. **Owner is a leaf.** In a fresh interpreter, after `import backend.speech.text_normalize`, the loaded `backend*`
      modules are exactly `{backend, backend.speech, backend.speech.text_normalize}`. It has no module-level import of any
      `backend.*` module.
   c. **Migrated modules define no tables of their own.** An AST test scans `locations/textnorm.py`,
      `voicemail/classify.py`, `privacy/text_pii.py`, `security/normalization.py` and any granted lock-request file. It
      must find none of the following:
      - a `maketrans` call;
      - an int constant 0x064B, 0x0660 or 0x06F0;
      - a string constant containing a code point of **K**, where that constant sits inside a dict or set literal, a
        dict or set comprehension, or the pattern argument of a `re.*` call.
      Docstrings, comments and rule/keyword data are exempt (e.g. `CATEGORY_RULES` tuples, `voicemail/classify.py:207`).
      At base the scan has hits in all four modules. After the change it has 0.
      **K** = U+0660–0669, U+06F0–06F9; U+0623, 0624, 0625, 0629, 0643, 0649, 064A, 06C0, 0671, 0672; U+0640,
      U+064B–065F, U+0670, U+06D6–06ED; U+200C–200F; U+060C, 066B, 066C.
   d. **Repository ratchet.** Outside `backend/speech/text_normalize.py` and `backend/tests/`, no `.py` file under
      `Homayar/backend/` contains a `maketrans` call or an int constant 0x0660, 0x06F0 or 0x064B. The only exception is an
      explicit allowlist in the test, and it may list only lock-request sites that Foreman did not grant (file:line plus
      the follow-up reference). This prevents an eighth copy.
   e. **Exported tables are immutable.** Every table the owner exports for other modules is immutable: `MappingProxyType`,
      `frozenset`, `tuple`, `str`, or a compiled pattern. Item assignment on an exported mapping raises `TypeError`, so a
      consumer cannot change another consumer's behaviour. `DEFAULT_COLLOQUIAL_FA` keeps its existing type.
   f. **Owner documentation.** The owner's docstring, or a marked section, documents:
      - each exported table;
      - each consumer profile: which tables and steps it uses, and in what order (speech fa/en/az and matching;
        locations stream; voicemail keywords; PII digits; mobile/OTP digits);
      - the preserved divergences from the table above.
      The stale sentence at `speech/text_normalize.py:5-6` and the `TODO(consolidate)` at `locations/textnorm.py:3-7`
      are resolved.
2. **Golden tests captured from BASE_SHA.** New module `Homayar/backend/tests/test_text_normalization_golden.py`. It tests
   pure functions only, uses no DB/client fixtures, and imports only names that exist at BASE_SHA (at base, digits are
   reached through `backend.privacy.text_pii.normalize_digits`).
   a. **G-base.** The module, copied unmodified onto a clean BASE_SHA checkout, passes there with the same number of
      collected tests as on the maker's head (Foreman gate, Verification 2). The handoff pastes the green run on the
      untouched worktree, made before any production edit.
   b. **Exhaustive differential sweep.** Cover every code point U+0000–U+FFFF except U+D800–U+DFFF, in at least these
      templates: `c`, `"ب"+c+"ب"`, `"۱"+c+"2"`, `"ي"+c`, `c+"ٔ"`, `c+"ٓ"`, `"می "+c+"روم"`,
      `"کتاب "+c+"ها"`. Compare the output of every function below with frozen reference copies of the BASE_SHA
      implementations. The copies are verbatim, embedded in the test module or in `tests/_text_normalization_reference.py`,
      and their header names BASE_SHA and the source file:line.
      - Functions: `normalize_digits`; `unify_arabic_letters`, `strip_diacritics`, `normalize_whitespace`, `normalize_zwnj`,
        `normalize_punctuation_fa`; `normalize_fa` (default, `colloquial=DEFAULT_COLLOQUIAL_FA`, `punctuation=False`);
        `normalize_en`; `normalize_az`; `normalize_for_matching` (`fa`, `en`, `az`); locations `normalize_char_stream`,
        `normalize_text`, `is_word_char`; voicemail `normalize_text`, `is_empty_transcript`;
        `security.normalization.normalize_iranian_mobile` (value or `ValueError`); `normalize_caller_number` if its lock
        is granted.
      - For `normalize_char_stream`, compare `(text, offsets, original)` and `span(i, j)` for every `0 <= i <= j <=
        len(text)` (`i == len(text)` exercises the past-end branch).
      - Use frozen references, not literal expectations, so the sweep holds on Python 3.12 and 3.13.
   c. **Literal golden corpus**, with expected values written as literals captured at base:
      - every cell of the divergence table and every value listed under it;
      - `detect` (start, end, class, value) and `redact` (text plus replacements) for the `test_text_pii.py:96-156`
        samples, for `PII_TRANSCRIPT`, and for the Persian- and Arabic-Indic-digit variants;
      - `RuleClassifier().classify` (category, intent, topics, confidence, needs_review, matched_terms) and `prioritize`
        for the `test_voicemail_classify.py` samples, plus Arabic-letter, ZWNJ, diacritic and digit variants;
      - `extract_locations(text)` (`candidates[*].as_dict()`, status, `resolved.as_dict()`, clarification, postal_code,
        landmarks) for the `test_locations_extract.py` samples, plus variants with Arabic letters, ZWNJ, tatweel,
        diacritics and Persian digits;
      - `DETECTOR_VERSION == "text-pii/1"`, `RULES_VERSION == "vm-rules-2026.09.1"`, and both `policy_digest` values.
      Literal inputs use only code points whose Unicode properties are the same in Unicode 15.0 and 15.1.
   d. **Length invariants.** For every sweep string, `len(normalize_digits(s)) == len(s)` and
      `len(unify_arabic_letters(s)) == len(s)`.
   e. **Runtime.** The module finishes in under 30 s on the lane. It does not depend on the global normalizer registry
      (e.g. `ckb` registered by `test_speech_normalize.py`).
3. **Public API unchanged.** These keep their names, signatures and return types:
   - `backend.speech.text_normalize`: every public name at base (`ZWNJ`, `DEFAULT_COLLOQUIAL_FA`, the ten functions,
     `NormalizerEntry`, `register_normalizer`, `get_normalizer`, `registered_languages`, `normalize`);
   - `backend.locations.textnorm`: `NormalizedText` (frozen dataclass with `text`, `offsets`, `original`, `span`),
     `normalize_char_stream`, `normalize_text`, `is_word_char`;
   - `backend.voicemail.classify.normalize_text`;
   - `backend.privacy.text_pii`: `normalize_digits`, `detect`, `redact`, `PiiSpan`, `Replacement`, `Pseudonymizer`,
     `parse_classes`, `valid_*`, `DETECTOR_VERSION`;
   - `backend.security.normalization`: `normalize_email`, `normalize_iranian_mobile`, `normalize_workspace_slug`.
4. **Detection and classification logic untouched (diff property).** In `git diff 7112407`:
   - `privacy/text_pii.py`: only the digit table and `normalize_digits` (base `:53`, `:60-62`) and the import block
     change. Every regex, `_canonical_phone`, the validators, `detect`, `redact`, `Pseudonymizer`, `parse_classes` and
     `DETECTOR_VERSION` are byte-identical.
   - `voicemail/classify.py`: only the imports and the normalisation section (base `:16-60`) and docstring change. Rules,
     patterns, the classifier, prioritisation and `RULES_VERSION` are byte-identical.
   - `security/normalization.py`: only `:1-9` and `:21` change.
   - `locations/textnorm.py`: `NormalizedText`, `normalize_text` and `is_word_char` are unchanged, and the per-character
     loop keeps its semantics. The tables now come from the owner.
5. **Import safety.** Source `testenv-l2.sh` first. Then each of these imports cleanly as the FIRST import in a fresh
   interpreter (exit 0):
   - `backend.speech.text_normalize`, `backend.privacy.text_pii`, `backend.security.normalization`;
   - `backend.locations.textnorm`, `backend.locations.extract`;
   - `backend.voicemail.classify`, `backend.voicemail.ingest`;
   - `backend.speech.dates`, `backend.speech.entities`, `backend.speech.languages`, `backend.speech.benchmark`;
   - `backend.routers.v1.auth`, `backend.main`.
6. **Raw evidence untouched.**
   - No migration, no model or schema change, no backfill, and no change to any write path (enforced by File ownership).
   - A DB-backed test, either new (`tests/test_text_normalization_evidence.py`) or an extension of `test_text_pii.py`,
     reuses `_processed_with_pii`. Its mock transcript contains ي ك ة ۀ ؤ, Persian and Arabic-Indic digits, U+200C,
     U+064E, U+0640, U+200F and a Persian-digit mobile.
   - After `process_job` and `redact_revision(classes=list(PiiClass))`, the stored `transcript_segments.text` of the
     current revision equals `transcript.strip()` code point for code point. No normalized form (e.g. `ی` in place of the
     supplied `ي`) appears in it.
   - Each redaction replacement's original value equals the slice of the stored text at its offsets.
   - This test must also pass on BASE_SHA (G-base).
7. **(Only if the lock request is granted.)**
   - `routers/v1/auth.py` uses the owner's `normalize_digits`. An API test sends the SMS code rewritten in Persian digits
     to `/api/v1/auth/otp/verify` → 200 and a session. A second challenge with Arabic-Indic digits → 200. A wrong code in
     Persian digits → 400 with `detail.code == "invalid_otp"`.
   - `voicemail/ingest.py` uses the owner's `normalize_digits`, and `normalize_caller_number` returns exactly the values
     listed under the Context table.
   - Both tests pass at base too.
8. **No regressions.** Existing tests keep their meaning: additions only, no assertion weakened, no case deleted,
   `test_text_pii.py` names `FA`/`NID` kept (imported by `test_privacy_routing.py:50`). The full suite on the lane has no
   new failure relative to the baseline. `ruff check` and `ruff format --check` are clean on the changed files.

## Deny-cases and closest allow-controls
- DENY the locations profile gaining ۀ, ؤ or ٲ folding, or NFC/NFKC. Probes: `normalize_text("خانۀ") == "خانۀ"`, and
  `normalize_char_stream("آب")` gives text `"اب"` with offsets (0, 2). A change would alter persisted
  `service_locations` address fields and `list_locations(city=...)` equality (Context 3). ALLOW-CONTROL: locations still
  folds what it folds today (`normalize_text("علي كتاب") == "علی کتاب"`), and voicemail `normalize_text("خانۀ") == "خانه"`.
- DENY `detect` gaining letter unification, NFKC, ZWNJ handling or extra digit scripts. Probes:
  `detect("كد پستي ۱۴۳۵۶۷۸۹۱۲ است") == []`; the fullwidth mobile is not detected; the fullwidth national ID keeps its
  fullwidth value. Any such change is a semantic change under an unchanged `DETECTOR_VERSION` and `policy_digest`.
  ALLOW-CONTROL: `detect("کد پستی ۱۴۳۵۶۷۸۹۱۲ است")` → IR_POSTAL_CODE (8, 18), and the Persian-digit phone gives
  span (9, 22) in original offsets.
- DENY the owner's `normalize_digits` covering more than the 20 code points: `"１２３"` stays `"１２３"`.
  ALLOW-CONTROL: `"۱۲۳ و ٤٥٦"` → `"123 و 456"`.
- DENY voicemail losing NFKC or the U+06D6–06ED class: `"ﻱﻮ"` must still give `"یو"`, and `"سلام۝ا"` must
  still give `"سلاما"`. ALLOW-CONTROL: speech `normalize_fa` leaves both unchanged.
- DENY reordering speech steps: `normalize_fa("ئ")` stays `"ئ"`, which needs NFC before letter unification.
  ALLOW-CONTROL: locations gives `"ی"` for the same input.
- DENY speech turning ZWNJ into a space or dropping LRM/RLM (`normalize_fa("می‌روم")` keeps U+200C).
  ALLOW-CONTROL: locations and voicemail give `"می روم"` and `"سلام"`.
- DENY a length-changing `unify_arabic_letters` or `normalize_digits`, which would break `speech/dates.py:250` offsets and
  PII offsets. ALLOW-CONTROL: 1:1 letter mapping (`"علي"` → `"علی"`, length 3).
- DENY any cycle, or the owner importing `backend.*`. ALLOW-CONTROL: text_pii, security, locations and voicemail import
  from the owner.
- DENY a change to `DETECTOR_VERSION`, `RULES_VERSION` or either `policy_digest` value. ALLOW-CONTROL: identical values,
  so existing redacted versions and voicemail classifications stay valid (`pipeline.py:157-168` returns `changed=False`
  for an already-classified revision).
- DENY any stored `transcript_segments.text` differing from the supplied transcript. ALLOW-CONTROL: the redacted
  derivative (`redacted_segments`) contains placeholders.
- DENY `normalize_iranian_mobile("۰۹۱۲۳۴۵۶۷۸")` (10 digits) being accepted. ALLOW-CONTROL: the 11-digit Persian,
  Arabic-Indic and fullwidth spellings give `"+989123456789"`.
- (If granted) DENY a wrong Persian-digit OTP → it must stay 400 `invalid_otp`. ALLOW-CONTROL: the correct code in
  Persian or Arabic-Indic digits → 200.
- DENY a mutable shared table (a consumer assigning into an owner mapping). ALLOW-CONTROL: consumers build their own
  merged local mapping from owner tables, e.g. `{**A, **B}` wrapped read-only.

## File ownership
Maker may change (all locked to HOM-120 / L2 in `locks.tsv`):
- `Homayar/backend/speech/text_normalize.py`: the owner. Add the digit table and `normalize_digits` definition, add
  immutable named tables, and document the profiles. Existing function outputs stay identical.
- `Homayar/backend/locations/textnorm.py`: tables from the owner; stream semantics and offsets unchanged.
- `Homayar/backend/voicemail/classify.py`: imports and the normalisation section only.
- `Homayar/backend/privacy/text_pii.py`: the digit table and `normalize_digits` only (import from the owner, keep the
  name).
- `Homayar/backend/security/normalization.py`: digit handling only.
- Tests (new): `Homayar/backend/tests/test_text_normalization_golden.py`,
  `Homayar/backend/tests/test_text_normalization_owner.py` (AC1 structure; fails at base by design), and optionally
  `Homayar/backend/tests/_text_normalization_reference.py` (frozen base references; underscore, so not collected) and
  `Homayar/backend/tests/test_text_normalization_evidence.py` (AC6).
- Tests (additive only): `Homayar/backend/tests/test_speech_normalize.py`, `test_locations_extract.py`,
  `test_voicemail_classify.py`, `test_text_pii.py`.

Must not change:
- `speech/{dates,entities,benchmark,languages,audio_profile}.py`;
- `locations/{extract,gazetteer,normalize,routing,service,validate}.py`;
- `voicemail/{pipeline,search,models}.py`, and `voicemail/ingest.py` unless granted;
- `privacy/{redaction_service,subject_export,transcript_view,mapping_crypto}.py`;
- `jobs/*`, `services/*`, `schemas/*`, `routers/*` (except a granted `routers/v1/auth.py`);
- migrations, `saas_models.py`, `rls.py`, `config.py`, `main.py`, `authorization.py`, CI, docs, any frontend;
- `tests/test_voice_admission*` (L1), `tests/test_transport_drills.py` and `tests/_transport.py` (L4).

No data migration or backfill of any stored row.

Checker (blind oracle): only `Homayar/backend/tests/test_oracle_wp22t02.py` in the checker worktree, plus records under
`/root/foreman/20260929-backend-mode-100/wp22t02-t1/{oracle,reviews}/`. Its golden part must pass on the checker's clean
BASE_SHA worktree before it is run against the maker's head. Its structural part (AC1) is expected to fail at base.

### Lock request (Foreman decides)
1. `Homayar/backend/routers/v1/auth.py`, lines 616-618 only. This is the sixth copy in PLAN §3 (OTP digits): replace the
   inline `str.maketrans` with the owner's `normalize_digits` (one import plus one expression). The behaviour is
   identical because `schemas/auth.py:56` admits only these 20 digits plus ASCII. It adds AC7 (OTP API test). Not
   locked by any active issue (`locks.tsv`). WP22-T01 will touch `routers/v1/*` later.
2. `Homayar/backend/voicemail/ingest.py`, lines 99-101 only. This is the seventh copy, not in PLAN §3. Same replacement.
   It adds AC7 (`normalize_caller_number` golden). Not locked by anyone.

If either is denied, that site stays and is listed in the AC1d allowlist with a follow-up reference, and the handoff
records it.

## Recommended change (the maker may deviate if all acceptance criteria hold)
1. First, write the golden module and the evidence test against the untouched worktree and run them green. Paste the
   summary in the handoff.
2. Move `_DIGITS` and `normalize_digits` into the owner. In text_pii, import it from the owner. Remove the owner's import
   of text_pii.
3. The owner exports immutable named tables:
   - digits;
   - three letter-variant sets: core 7 (locations), plus ۀ and ؤ (voicemail), plus ٲ (speech);
   - format/invisible characters: ZWNJ, ZWJ, LRM, RLM;
   - tatweel;
   - the harakat range U+064B–065F plus U+0670;
   - Quranic marks U+06D6–06ED;
   - Arabic numeric separators and comma (the locations mapping).
   Each consumer builds its exact current map from these tables, keeps its own step order, and keeps its own local
   generic steps (casefold, `\s`, `[^\w\s]`).
4. `security.normalize_iranian_mobile`: `normalize_digits(unicodedata.normalize("NFKC", value))`. This is equivalent to
   base because the two base tables have disjoint keys.
5. Keep `normalize_char_stream` streaming over `normalize_digits(text)` char by char. Never apply a whole-string
   transform that can change length before offsets are recorded.

## Verification commands
`<W>` = maker worktree, `<C>` = checker worktree, `<B>` = a Foreman-owned clean BASE_SHA checkout with its own
`Homayar/.venv`. Always `source /root/foreman/20260929-backend-mode-100/testenv-l2.sh` first, and run every
PostgreSQL-touching command under `flock "$LANE_LOCK"`.
1. **Focused** (from `<W>/Homayar`):
   `flock "$LANE_LOCK" uv run pytest backend/tests/test_text_normalization_golden.py backend/tests/test_text_normalization_owner.py backend/tests/test_text_normalization_evidence.py backend/tests/test_speech_normalize.py backend/tests/test_speech_dates.py backend/tests/test_speech_entities.py backend/tests/test_speech_benchmark.py backend/tests/test_locations_extract.py backend/tests/test_locations_api.py backend/tests/test_voicemail_classify.py backend/tests/test_voicemail_pipeline.py backend/tests/test_voicemail_api.py backend/tests/test_text_pii.py backend/tests/test_privacy_routing.py backend/tests/test_retention_lifecycle.py backend/tests/test_api.py backend/tests/test_auth_contract.py backend/tests/test_share_organization.py -q -p no:cacheprovider -rs`
   (Drop the evidence file from the list if AC6 extends `test_text_pii.py` instead.)
2. **G-base (Foreman).** Copy `test_text_normalization_golden.py`, `_text_normalization_reference.py` (if present) and the
   AC6 evidence test from `<W>` into `<B>/Homayar/backend/tests/`. Then, from `<B>/Homayar`:
   `flock "$LANE_LOCK" uv run pytest backend/tests/test_text_normalization_golden.py backend/tests/test_text_normalization_evidence.py -q -p no:cacheprovider -rs`
   It must pass with the same passed count as on `<W>`. Remove the copies afterwards. The oracle's golden part is checked
   the same way on `<C>`.
3. **Owner structure** (from `<W>/Homayar`):
   `uv run python -c "import sys, backend.speech.text_normalize as t; print(t.normalize_digits.__module__); print(sorted(m for m in sys.modules if m.startswith('backend')))"`
   Expected output: `backend.speech.text_normalize` and `['backend', 'backend.speech', 'backend.speech.text_normalize']`.
   Then:
   `for m in backend.speech.text_normalize backend.privacy.text_pii backend.security.normalization backend.locations.textnorm backend.locations.extract backend.voicemail.classify backend.voicemail.ingest backend.speech.dates backend.speech.entities backend.speech.languages backend.speech.benchmark backend.routers.v1.auth backend.main; do uv run python -c "import $m" || echo "FAIL $m"; done`
   Expected: no `FAIL`.
4. **Ratchet** (from `<W>/Homayar`):
   `grep -rn --include=*.py -E "maketrans|0x06[Ff]0|0x0660|0x064[Bb]" backend | grep -v -e '^backend/tests/' -e '^backend/speech/text_normalize.py'`
   Expected: empty, or only lock-request sites Foreman did not grant.
5. **Diff property (reviewer):**
   - `git -C <W> diff --stat 7112407` lists only owned files.
   - `git -C <W> diff 7112407 -- Homayar/backend/privacy/text_pii.py Homayar/backend/security/normalization.py Homayar/backend/voicemail/classify.py Homayar/backend/locations/textnorm.py`
     has hunks only where AC4 allows.
6. **Full suite** (one chunk per foreground command):
   `LANE=2 bash /root/foreman/20260929-backend-mode-100/suite.sh <W> <outdir> 00`, then `01`, `02`, `03`.
7. **Lint** (from `<W>/Homayar`): `uv run ruff check <changed files> && uv run ruff format --check <changed files>`.

## Worktrees
Foreman fills these in. Convention: maker `/root/foreman/20260929-backend-mode-100/wt/wp22t02-t1` (branch
`mission/wp22t02-t1` at 7112407); checker `/root/foreman/20260929-backend-mode-100/wt/wp22t02-t1-check` (detached at
7112407, clean). Each has its own `Homayar/.venv` (`uv sync --frozen`). `<B>` for G-base is Foreman's.

## Known baseline
At `7112407`: full suite 0 failed / 1278 passed / 47 skipped. One intermittent drill (HOM-163,
`test_transport_drills.py`, chunk 03) is not attributable to this unit.

## Allowed and forbidden actions; communication map; turn protocol; team directory
As in `stab02-t1/SPEC.md` and `wp02t03-t1/SPEC.md`. Team `wp22t02-t1`; names `wp22t02-t1-impl`, `wp22t02-t1-rev` and
final reviewer `wp22t02-t1-final` (security-vulnerability-scanner). Directory
`/root/foreman/20260929-backend-mode-100/wp22t02-t1/`. Test fixtures are synthetic only: no real phone numbers, IDs or
e-mails, and PII values are never logged.

## Out of scope findings (recorded, not fixed)
1. **Under-redaction of postal codes spelled with Arabic letters or ZWNJ.** `privacy/text_pii.py:109-113`
   `_POSTAL_KEYWORD_RE` accepts only `کد\s?پستی`/`کدپستی`. At base, `detect("كد پستي ۱۴۳۵۶۷۸۹۱۲ است")` and
   `detect("کد‌پستی ۱۴۳۵۶۷۸۹۱۲ است")` both return `[]`: the digits fail the national-ID check, so nothing is
   redacted. The Persian-letter spelling is detected. A fix changes detector semantics, so it needs a `DETECTOR_VERSION`
   bump (new redacted versions per `policy_digest`). It needs its own ticket (privacy, P1 candidate).
2. **Non-Persian Unicode digits are handled inconsistently** (low).
   - `privacy/text_pii.py:65-67,98-99,125-126`: `\d` and `\D` are Unicode-aware, but the boundaries and phone prefixes
     are ASCII-only. A fullwidth or Devanagari valid national ID is detected with a non-ASCII canonical value
     (`"１００００００００１"`), so its pseudonym token and subject-export matching (`privacy/subject_export.py:4-7`) differ
     from the ASCII spelling of the same ID.
   - Fullwidth mobiles are not detected at all, while `security/normalization.py:21` (NFKC) accepts them at login.
   - `voicemail/ingest.py:99-104` stores fullwidth landline digits verbatim (`"０２１８８７７６６５５"`), which splits
     repeat-caller matching.
   - Decide with detector v2.
3. **Profile divergences (the Context table) are preserved on purpose.** Unifying them needs a `DETECTOR_VERSION` and
   `RULES_VERSION` bump plus a data migration of the `service_locations` address fields, which are persisted normalized
   (`locations/service.py:64-71`). Candidate follow-up once WP17-T02 picks its lexical profile.
4. **PLAN §3 undercounts the copies.** A seventh copy exists at `voicemail/ingest.py:99-101` (Lock request 2).

## Models
Implementer: opus (high: behaviour-preserving refactor across a privacy detector and a persisted normalizer, with an
exhaustive differential harness). Reviewer: opus (high). Final reviewer: security-vulnerability-scanner (PII trigger).

## Foreman rulings (activation, 2026-09-30)
- BASE_SHA = `7112407`. Lane L2 (`testenv-l2.sh`, port 5434, `LANE=2 suite.sh`; focused runs under `flock "$LANE_LOCK"`).
- Lock request 1 GRANTED: `Homayar/backend/routers/v1/auth.py`, ONLY the OTP digit translation at :616-618 (verified).
- Lock request 2 GRANTED: `Homayar/backend/voicemail/ingest.py`, ONLY the digit translation at :99-101 (verified).
- Scope confirmed: single normalization owner, no behaviour change. Locale-aware dates/numbers deferred (follow-up).
- Out-of-scope findings (postal-code keyword spelled with Arabic letters/ZWNJ not redacted; fullwidth/Devanagari digit
  inconsistencies) are filed as separate Linear issues; do NOT fix them here (they change detector behaviour/version).
- Final reviewer: security-vulnerability-scanner (edits the PII detector module and OTP handling).
- Known lane gate exception until HOM-163 lands: test_transport_drills::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert
  (pre-existing, reproduced on base under load). Any other failure is a real failure.
- Worktrees: maker /root/foreman/20260929-backend-mode-100/wt/wp22t02-t1 (branch mission/wp22t02-t1); checker
  /root/foreman/20260929-backend-mode-100/wt/wp22t02-t1-check (detached). Parallel mode: never touch files outside File
  ownership (locks.tsv) or another lane's database.

## Amendments (Foreman, after phase A, before phase B)
- A1 (AC8 vs lock grant, reviewer F1): `routers/v1/auth.py` is not `ruff format`-clean at base (hunks at :159, :187,
  :400, :667, outside the grant), and `security/normalization.py` has only a trailing blank line at EOF. AC8 for these
  files means: `ruff check` clean AND no format drift beyond base. Do NOT reformat anything outside the granted lines
  in auth.py (the grant is :616-618 only); in other changed files, format-clean is required.
- A2 (golden provenance, reviewer F5): the golden/reference test module states its provenance: base `7112407`, the four
  source file names of the migrated copies, and file.py:<line> for each frozen reference.
- A3 (parallel harness): temporary files ONLY under /root/foreman/20260929-backend-mode-100/wp22t02-t1/scratch/. Never
  delete anything outside your worktree and that scratch dir.
