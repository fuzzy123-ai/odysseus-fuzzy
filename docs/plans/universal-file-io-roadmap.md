# Universal File IO Roadmap

## Goal

Odysseus can receive files through Universal Inbox, understand their type and
safety profile, accept follow-up export requests such as "make this a PDF",
and later return converted files through Telegram, UI download, Nextcloud, or
project folders.

## Mode

Standard ABC, backend/logik-first. UI placement is out of scope for this slice.

## Current Evidence

- Universal Inbox already spools Telegram document/image attachments.
- Universal Inbox already classifies text, documents, images, audio, video,
  archives, structured messages, and dangerous files.
- Text, JSON, CSV/TSV, HTML, XML, DOCX, and PDF text extraction have an
  ephemeral extraction packet.
- Reviewed inbox writes can now write native Memory plus bounded RaptorGraph
  provenance events.

## Non-Goals

- No broad UI work.
- No live Telegram file delivery in this first slice.
- No live Nextcloud mutation.
- No destructive writes and no overwriting originals.
- No full OCR, full Office conversion, ffmpeg, LibreOffice, Blender, or
  asset-import execution until each tool path has a separate gate.

## Slice Queue

1. `UFIO1-file-capability-registry`
   - Class: `repo_only`
   - Owner: Bob
   - Goal: extend file type coverage to common document/media/game asset
     families and expose conversion-relevant capabilities.

2. `UFIO2-export-intent-contract`
   - Class: `repo_only`
   - Owner: Bob
   - Goal: parse follow-up requests like "mach daraus ein PDF" into a redacted
     export intent referencing the recent inbox file.

3. `UFIO3-export-capability-plan`
   - Class: `repo_only`
   - Owner: Bob
   - Goal: return safe export plans for document, image, audio, PDF page image,
     and 2D/3D asset conversions without executing external tools.

4. `UFIO4-telegram-delivery-prep`
   - Class: `safe_offline`
   - Owner: Charlie
   - Goal: document and test the Telegram reply/delivery contract before live
     sendDocument/sendPhoto/sendAudio execution.

5. `UFIO5-live-converters`
   - Class: `needs_live_go`
   - Owner: Charlie
   - Goal: enable bounded converters after operator approval and tool checks:
     LibreOffice/Pandoc/WeasyPrint, Pillow, ffmpeg, OCR, Blender/assimp.

## Export Families

Documents:
- DOCX, ODT, RTF, TXT, MD, HTML to PDF.
- PDF to text, PDF to page images, searchable PDF after OCR.
- XLSX/ODS/CSV to CSV/PDF after table policy review.
- PPTX/ODP to PDF or page images.

Images:
- PNG, JPG/JPEG, WEBP, TIFF, BMP, AVIF, HEIC.
- Convert, resize, thumbnail, compress, background removal, OCR overlay.

Audio:
- MP3, WAV, OGG/OPUS, M4A, FLAC, AAC.
- Transcode, trim, normalize, split.

Video:
- MP4, MOV, WEBM, MKV, AVI.
- Thumbnail, audio extraction, transcode later.

GameDev Assets:
- 2D: sprite sheets, atlases, textures, normal maps, UI screenshots.
- 3D: GLB, GLTF, OBJ, FBX, BLEND, STL, DAE.
- Planned exports: GLB/GLTF/OBJ/FBX plus preview images and asset manifests.

## Safety Rules

- The export intent references a recent inbox spool item, not raw file content.
- The plan may include source suffix, family, target format, required local
  tool, and policy decisions.
- Raw content, Telegram IDs, host paths, and secrets must not be written to
  reports, tests, docs, or history.
- DSGVO mode forces local-only converters.
- Originals are never overwritten; exports write to a new output ref.
- Live delivery requires a separate explicit Go.

## Done For This Slice

- File type registry recognizes common document/media/game asset formats.
- Export intent parser understands common natural-language target requests.
- Export capability planner returns deterministic, redacted plans.
- Focused tests cover document, image, audio, PDF-to-image, 3D asset plans,
  unsupported targets, DSGVO local-only behavior, and redaction.
