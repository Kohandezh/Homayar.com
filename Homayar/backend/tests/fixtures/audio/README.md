# Test audio

One file. No other audio goes into the repository.

## `fleurs_en_us_11705046490861852963_tel8k.wav`

- Source: FLEURS (`google/fleurs` on Hugging Face), language `en_us`, split `test`.
  https://huggingface.co/datasets/google/fleurs
- Creator: Google; FLEURS, Conneau et al., 2022, https://arxiv.org/abs/2205.12446
- Item id: `11705046490861852963` (file `11705046490861852963.wav`, row id 1866).
- License: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/
- Changes: converted from the 16 kHz original to 8 kHz mu-law mono (the phone channel):

  ```bash
  ffmpeg -nostdin -loglevel error -y -i 11705046490861852963.wav \
    -ac 1 -ar 8000 -c:a pcm_mulaw fleurs_en_us_11705046490861852963_tel8k.wav
  ```

- Size and length: 62 972 bytes, 7.86 s.
- SHA-256: `04735f29a67dd5b6ae8d68413ff76f28d165c3c42a8456ee34f8dfb6390f4cb7`
- Reference text: `First, most riders wear riding boots with a heel and a smooth, quite narrow, sole.`

Why it is here: with the default Silero VAD options of faster-whisper 1.2.0, VAD
keeps no audio of this file, although it holds real speech (HOM-39 comparison,
HOM-40). `backend/tests/test_whisper_vad_rescue.py` uses it to check the VAD
rescue pass (ADR 0044, amendment HOM-40).
