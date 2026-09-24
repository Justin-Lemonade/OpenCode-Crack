# Format Support Matrix

Route every file by extension. Order of preference: repo extractor → agent native read → external conversion → declared unsupported. Record the route actually used in the manifest.

## Native via repo extractors (no new tools needed)

| Extensions | Route |
|---|---|
| `.txt`, `.md`, `.csv`, `.tsv`, `.html`, `.htm` | `src/translation/formats/textual.py`, or read directly |
| `.docx` | `src/translation/formats/docx.py` (paragraphs + tables); `python-docx` installed |
| `.xlsx` | `src/translation/formats/xlsx.py` / `src/ingestion/formats/xlsx_parser.py`; `openpyxl` installed |
| `.xls` | `src/ingestion/formats/xls_parser.py` |
| `.pptx` | `src/translation/formats/pptx.py` / `src/ingestion/formats/pptx_parser.py`; `python-pptx` installed |
| `.pdf` (text-based) | `src/translation/formats/pdf.py`; `pymupdf` + `pypdf` installed |
| `.epub` | `src/ingestion/formats/epub_parser.py` |
| `.odt`, `.ods` | `src/ingestion/formats/odt_parser.py`, `ods_parser.py` |
| `.rtf` | `src/ingestion/formats/rtf_parser.py` |
| `.eml`, `.msg` | `src/ingestion/formats/eml_parser.py`, `msg_parser.py` |
| `.json`, `.xml`, `.yaml`, `.yml` | matching `src/ingestion/formats/` parsers; read directly works too |

## Via agent native read (no repo code needed)

- **Images** (`.png`, `.jpg`, screenshots) and **short PDFs**: the read tool renders them directly. Page through multi-page PDFs one chunk at a time and transcribe the needed text into the cache with `[p.N]` anchors.
- **Scanned/image-only PDFs**: no OCR engine is installed — transcribe via the read tool's rendering. If the set is large, say so and propose installing Tesseract rather than silently skipping pages.

## Via external conversion (only if installed — check first, never assume)

- Legacy `.doc`, `.ppt`, `.wps`, `.key`, `.numbers`, `.pages`: convert with LibreOffice (`soffice --headless --convert-to`) or pandoc if present. If neither is installed, record `status: needs-conversion (soffice/pandoc not installed)` — do not fake the content.
- Archives (`.zip`): list contents, extract to a temp folder inside the set directory, intake the inner files individually.

## Declared out of scope (say so, do not improvise)

- **Audio/video** (`.mp3`, `.wav`, `.mp4`, etc.): no transcription tool is installed. Record as unsupported and offer transcription as a follow-up if the user provides one.
- Password-protected or DRM-locked files: record as unsupported, never ask the user to strip protection inside this workflow.
- Anything the route step cannot extract: `status: failed (<reason>)`. A manifest with honest gaps beats a complete-looking one with holes.
