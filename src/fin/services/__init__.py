"""One function per user action, called by both the CLI and the web UI (docs/specs/SPEC-02). Each
takes typed arguments, does the work inside an audited run of the CLI command's name and returns an
`ActionResult` (`fin.services.result`). The CLI and the UI only parse, call and render."""
