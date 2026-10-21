# Speech metrics (`backend.speech_metrics`)

HOM-51 (WP11-T01, feature F34). This package measures how much of a call is speech, how
much is silence, how the talk time is split between speakers, how much speakers talk over
each other, and how fast they speak. It works only on time intervals and word counts.

- It is pure: no database, no network, no provider, no file I/O. Standard library only.
- It is deterministic: the same intervals in any order give the same result.
- It never asks a language model for a number. Every value is exact arithmetic on
  integers.
- When a metric cannot be measured, it says why with a reason code. It never reports 0 as
  a stand-in.

Nothing in the backend uses it yet. Later WP11 and WP12 work wires it into the pipeline,
reports and playback. There is no table, no API and no migration here.

The version is `speech-metrics/1` (`METRICS_VERSION`). A change to any definition, limit
or rounding rule below is a new version.

Import everything from `backend.speech_metrics`.

## Usage

```python
from backend.speech_metrics import compute_speech_metrics, intervals_from_segments

intervals = intervals_from_segments(segments)  # TranscriptSegment rows, or similar
result = compute_speech_metrics(
    intervals,
    call_duration_ms=meeting_duration_ms,       # None when not known
    diarization_available=revision.diarization_status == "available",
    speakers=declared_speaker_keys,             # optional: speakers who may be silent
)
result.as_dict()  # JSON-safe
```

## Input

`SpeechInterval(start_ms, end_ms, speaker_key, word_count=None)` is one stretch of speech.
`intervals_from_segments` builds one per segment from `start_ms`, `end_ms`, `speaker_key`
and `text`. Its `word_count` is `count_words(text)`, or None when `text` is not a str.

The rules, in the order they apply:

1. **Validation.** `start_ms` and `end_ms` must be `int` (a `bool` is refused) with
   `0 <= start_ms <= end_ms`. `word_count` must be None or an `int >= 0`. `speaker_key`
   must be None or a `str`. Anything else raises `SpeechMetricsError` with code
   `invalid_interval`. The `SpeechInterval` constructor does not check; the check happens
   inside `compute_speech_metrics`.
2. **Duration.** `call_duration_ms` None means unknown. It must otherwise be an `int >= 0`,
   or the code is `invalid_duration`. `0` is a valid zero-length call.
3. **Declared speakers.** `speakers` must be None or a collection of `str`. A bare `str`,
   or a collection with any entry that is not a `str` (None included), raises the code
   `invalid_speakers`. This is checked even when diarization is off.
4. **Half-open.** An interval covers `[start_ms, end_ms)`: the start is inside, the end
   is not. This is the same as `filter_segments` in the transcript service.
5. **Zero-length.** An interval with `end_ms == start_ms` is ignored, words too.
6. **Clipping.** When `call_duration_ms` is above 0, every interval is cut to
   `[0, call_duration_ms)`. A cut interval keeps its full `word_count`. An interval wholly
   past the end has no length left: it is counted as clipped, then ignored, words too.
   With an unknown or zero duration nothing is clipped.
7. **Unknown speaker.** A key is unknown when it is None, or when `.strip().casefold()` of
   it is empty, `unknown` or `speaker_unknown`. Why both: `unknown` is the default of the
   `TranscriptSegment.speaker_key` column, and `speaker_unknown` is what the pipeline
   writes when no diarization turn covers a segment. Other keys are compared after
   `.strip()` only, so `A` and `a` are two speakers.
8. **Known speakers.** The known speakers are the declared speakers (unknown entries
   dropped) plus the known keys of kept intervals. A key seen only on an ignored interval
   does not make a speaker.
9. **No diarization.** When `diarization_available` is False, every interval is unknown
   speech and `speakers` is ignored. The speaker list in the result is empty.
10. **Same speaker.** Two intervals of one speaker that overlap or touch are merged for
    time. That is not overlap. Their word counts are added.

"Kept intervals" below means the intervals left after rules 5 and 6.

## Definitions (version 1)

`U(S)` is the total length in ms of the union of the intervals in S: time covered by at
least one of them, each moment counted once. `D` is `call_duration_ms`.

### Whole call

| Metric | Unit | Definition | Numerator / denominator | Not computable when |
| --- | --- | --- | --- | --- |
| `call_duration_ms` | ms | `D` | value / none | `D` is None (`duration_unknown`) or 0 (`zero_length_call`) |
| `speech_ms` | ms | `U(all kept intervals)` | value / none | never (0 when there is no speech) |
| `silence_ms` | ms | `D - speech_ms` | value / none | `duration_unknown`, `zero_length_call` |
| `speech_ratio` | ratio | `speech_ms / D` | `speech_ms` / `D` | `duration_unknown`, `zero_length_call` |
| `silence_ratio` | ratio | `silence_ms / D` | `silence_ms` / `D` | `duration_unknown`, `zero_length_call` |
| `attributed_speech_ms` | ms | `U(kept intervals of known speakers)` | value / none | `diarization_unavailable` |
| `unattributed_speech_ms` | ms | `speech_ms - attributed_speech_ms`: time when only unknown speech is active | value / none | `diarization_unavailable` |
| `overlap_ms` | ms | time when 2 or more different known speakers are active at once | value / none | `diarization_unavailable` |
| `overlap_ratio_of_speech` | ratio | `overlap_ms / speech_ms` | `overlap_ms` / `speech_ms` | `diarization_unavailable`, `no_speech` |
| `speaking_rate_wpm` | words_per_minute | sum of `word_count` of all kept intervals, divided by `speech_ms / 60000` | word sum / `speech_ms` | `no_speech`, `word_counts_missing`, `too_little_speech` |

Unknown speech that runs at the same time as a known speaker is not overlap. Overlap is
only between known speakers.

### Per known speaker (`speakers[key]`)

| Metric | Unit | Definition | Numerator / denominator | Not computable when |
| --- | --- | --- | --- | --- |
| `talk_ms` | ms | `U(kept intervals of the speaker)`; 0 for a declared speaker who said nothing | value / none | never (the list is empty without diarization) |
| `talk_share` | ratio | `talk_ms / sum of talk_ms of all known speakers` | `talk_ms` / that sum | `no_speech`, `no_attributed_speech`, `too_much_unattributed_speech` |
| `talk_ratio_of_call` | ratio | `talk_ms / D` | `talk_ms` / `D` | `duration_unknown`, `zero_length_call` |
| `speaking_rate_wpm` | words_per_minute | sum of `word_count` of the speaker's kept intervals, divided by `talk_ms / 60000` | word sum / `talk_ms` | `no_speech`, `word_counts_missing`, `too_little_speech` |

Why `talk_share` divides by the sum of talk times and not by `speech_ms`: when speakers
overlap, the sum of their talk times is larger than the union. Dividing by the union would
give shares that add up to more than 1. With the sum, the shares add up to 1 (up to
rounding). `overlap_ms` reports the overlap on its own.

### Limits (part of version 1)

- `MAX_UNATTRIBUTED_SHARE = 0.2`. When `unattributed_speech_ms / speech_ms` is above 0.2,
  every `talk_share` is not computable (`too_much_unattributed_speech`). Too much speech
  has no speaker, so the shares would mislead. At exactly 0.2 the shares are computed,
  over attributed time only, and `unattributed_speech_ms` shows how much was left out.
  The comparison is exact, with fractions.
- `MIN_RATE_SPEECH_MS = 5000`. A rate over less than 5000 ms of speech time is not
  computable (`too_little_speech`). A few words in a short turn give a rate that means
  nothing. 0 words over enough time is a computed `0.0`.

A rate is also not computable when any interval that adds to it has `word_count` None
(`word_counts_missing`). Words of overlapping intervals are added, not removed as
doubles: two people talking at once both say words.

### Checks that always hold

When the parts are computed, these hold exactly:

- `speech_ms + silence_ms == D`
- `attributed_speech_ms + unattributed_speech_ms == speech_ms`
- `overlap_ms <= attributed_speech_ms`
- `max(talk_ms) <= attributed_speech_ms <= sum(talk_ms) - overlap_ms <= sum(talk_ms)`.
  The middle is an equality when no moment has 3 or more known speakers at once (so always
  with exactly 2 speakers). A moment with 3 speakers is counted 3 times in the sum but
  removed only once by `overlap_ms`.

## Rounding

- ms values are exact ints. They are never rounded.
- A ratio is the exact fraction `numerator / denominator`, rounded to 4 decimal places
  with ROUND_HALF_EVEN (a tie goes to the even digit: 0.00025 becomes 0.0002), then
  returned as a Python float.
- Words per minute is the exact fraction `word sum * 60000 / time ms`, rounded to 1
  decimal place with ROUND_HALF_EVEN (11.25 becomes 11.2), returned as a float.
- Each `talk_share` is rounded on its own. Their sum can differ from 1 by rounding (three
  equal speakers give 0.3333 each). They are not forced to add up to 1.

## The result

`compute_speech_metrics` returns `SpeechMetrics`. Each metric is a `Measurement` with:

| Field | Meaning |
| --- | --- |
| `status` | `computed` or `not_computable` |
| `value` | int for ms, float for ratio and rate; None when not computable |
| `unit` | `ms`, `ratio` or `words_per_minute` |
| `reason` | None when computed, else one reason code below |
| `numerator`, `denominator` | the exact ints behind the value: for ms the value and None, for a ratio its two terms, for a rate the word sum and the time in ms. Both None when not computable |

`speakers` maps each known speaker key to its `SpeakerMetrics`, sorted by key.

`input_summary` counts what the input rules did:

| Key | Counts |
| --- | --- |
| `interval_count` | every input interval, before any rule |
| `ignored_zero_length_count` | intervals that were zero-length as given (not those clipped to nothing) |
| `clipped_interval_count` | intervals cut at the end of the call, including those cut to nothing |
| `unknown_interval_count` | kept intervals with an unknown speaker (all kept intervals when diarization is off) |

`as_dict()` gives the same names as a JSON-safe dict.

## Reason codes

When several reasons apply to one metric, the first one in this list is reported. Only
reasons that concern that metric's inputs count: `speech_ratio`, for example, never says
`diarization_unavailable`.

| Code | Meaning |
| --- | --- |
| `diarization_unavailable` | speakers were not separated, so no speaker metric exists |
| `duration_unknown` | the call duration is not known |
| `zero_length_call` | the call duration is 0 |
| `no_speech` | there is no speech at all (`speech_ms` is 0): no overlap ratio, no rate, no talk share |
| `no_attributed_speech` | there is speech, but none of it has a known speaker, so talk shares have no denominator |
| `too_much_unattributed_speech` | more than `MAX_UNATTRIBUTED_SHARE` of the speech has no known speaker |
| `word_counts_missing` | an interval that adds to the rate has no word count |
| `too_little_speech` | the speech time behind a rate is below `MIN_RATE_SPEECH_MS`; a declared speaker who said nothing gets this when the call has some speech (`speech_ms > 0`). With no speech at all the reason is `no_speech`, which comes first |

With no speech at all, the ms metrics are still measured: `attributed_speech_ms`,
`unattributed_speech_ms`, `overlap_ms` and every `talk_ms` are a computed 0 (with
diarization on), `silence_ms` is `D` and `silence_ratio` is `1.0` (when `D > 0`).

## Errors

Invalid input raises `SpeechMetricsError` (a `ValueError`) with a `code`:

| Code | When |
| --- | --- |
| `invalid_interval` | a bad `start_ms`, `end_ms`, `word_count` or `speaker_key` (see Input, rule 1) |
| `invalid_duration` | `call_duration_ms` is negative or not an int |
| `invalid_speakers` | `speakers` is a bare str, or holds an entry that is not a str |

Missing information is never an error. A missing duration, no speech, no diarization and
missing word counts all give a result with not-computable measurements.

## `count_words`

Text is split on Unicode whitespace. ZWNJ (U+200C, the Persian half-space) is not
whitespace, so a word like `می‌خواهم` counts once. A token counts as a word only if it has
at least one letter or digit (`str.isalnum()`), so a lone `-` or `...` is not a word.
Persian digits count. Empty text gives 0.

## Not in this version

- Accuracy against real annotated calls (the F34 target of at most 5 points mean absolute
  error) needs an annotated corpus (WP13). It is not measured here.
- Acoustic silence from the audio itself is `backend/speech/audio_profile.py`. That is a
  different signal; this package only reads intervals.
