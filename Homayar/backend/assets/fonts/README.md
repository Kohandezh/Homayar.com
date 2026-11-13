# Bundled fonts

The report PDF export (`backend/services/pdf_text.py`) draws every line with
these fonts. The app never downloads a font at runtime (air-gap).

- Font: Vazirmatn, static TTF, Regular and Bold
- Release: v33.003 (2022-06-22), the latest release of
  https://github.com/rastikerdar/vazirmatn
- Download URL:
  https://github.com/rastikerdar/vazirmatn/releases/download/v33.003/vazirmatn-v33.003.zip
  (SHA-256 of the zip:
  `0a9afd41967e6f57096a56a181a23f81a2b999b62f1f2a4e4b26736580854fdb`)
- Files taken from the zip: `fonts/ttf/Vazirmatn-Regular.ttf`,
  `fonts/ttf/Vazirmatn-Bold.ttf` and `OFL.txt`, unchanged
- Licence: SIL Open Font License 1.1 (`OFL.txt`)

| File | SHA-256 |
| --- | --- |
| `Vazirmatn-Regular.ttf` | `b69fd4c680b8f3f225feabcc655a2c585d97627b8f5f5c0f9985e894069f3a56` |
| `Vazirmatn-Bold.ttf` | `f635fdbea28f265de395ba83b4b1570dcf2f58d13c65469e61903b1c2d2ae723` |
| `OFL.txt` | `17e355067c8284f47743a1ee3b1ef7ff684ff0601eda357f9353b10b3016ab31` |
