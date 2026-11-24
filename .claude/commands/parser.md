---
description: Show or choose the PDF add-on that reads the pages pdfplumber skips (Docling, LiteParse, LlamaParse)
argument-hint: [docling|liteparse|llamaparse|none]
---

Show or choose the optional PDF add-on: $ARGUMENTS

1. Run `uv run fin parser show`. If it fails, report the error code and run_id and stop. Report the current choice,
   whether it is installed, and its models or API key in plain words (`uv run fin doctor` shows the same as its
   `pdf parser` line).
2. Explain the choices in plain words. fin reads PDFs with pdfplumber; the add-on reads only the statement pages
   pdfplumber skips (and short scanned results filings). One add-on at a time:
   - **Docling**: runs on this Mac; about 1.4 GB with its models; slow, about 15 seconds a page on CPU; read 99-100% of
     the figures on our skipped pages when benchmarked. Apple silicon only.
   - **LiteParse**: runs on this Mac; small (about 60 MB with its table model) and fast.
   - **LlamaParse**: a cloud service; the most accurate on hard tables; paid per page; needs the API key in the
     environment variable `LLAMA_CLOUD_API_KEY` (set in the shell, never in config.toml or the repo).
   - **none**: pages pdfplumber skips are listed as not read; Claude can read them when asked.
   Figures any add-on reads are marked as read by it; whether they are trusted is the user's call (`/verify`).
3. Only when the user names one (in `$ARGUMENTS` or in reply), run `uv run fin parser use <name>` and report what it
   printed. Never choose one unasked. Never ask for, print or store the value of `LLAMA_CLOUD_API_KEY`.
4. If it fails, report the error code and run_id and stop. Do not work around it.
