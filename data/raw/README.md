# Drop your source documents here

Put your textbook PDFs and course-note Markdown files in this folder.

- **PDFs** (`.pdf`) → converted to Markdown automatically during ingest.
  Scanned PDFs (no text layer) are OCR'd first if `ocrmypdf` is installed.
- **Markdown** (`.md`) → copied through as-is.

After running an ingest, the normalized Markdown lands in `../corpus/`.
**Review and hand-fix any garbled equations there before embedding** — that
is the whole point of normalizing to Markdown first.

Nothing in this folder is committed (see `.gitignore`); it's your private
study material.
