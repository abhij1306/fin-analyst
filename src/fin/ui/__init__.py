"""The optional web UI (`fin ui`, docs/web-ui-plan.md): a local server (FastAPI and Jinja2, in the
`ui` extra) rendering HTML pages that use htmx and Basecoat (static/vendor/). Nothing in the rest
of fin imports this package except `fin.cli.ui`, which imports the server lazily, so without the
extra fin works as before."""
