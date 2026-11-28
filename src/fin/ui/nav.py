"""The web UI's screens, as the top bar shows them: Home, then the screens in groups (each a
Basecoat dropdown menu). Plain data, so the command coverage test (docs/product.md, "Where each
command is") reads it without the `ui` extra."""

NAV = [
    ("Data", ["Files", "Mappings", "Statements"]),
    ("Analysis", ["Questions", "Reports", "Dashboards"]),
    (
        "Diligence",
        ["Engagement", "Differences", "Queries", "Gates", "Bridge", "Findings", "References"],
    ),
    ("System", ["Settings", "Health", "Log"]),
]
# Each group with its screens as (id, label); SCREENS is every screen, Home first.
NAV_GROUPS = [(group, [(name.lower(), name) for name in names]) for group, names in NAV]
SCREENS = [("home", "Home"), *(item for _, items in NAV_GROUPS for item in items)]
