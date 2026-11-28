"""The tables and columns each gate's approval depends on (SPEC-00): a change to any of them
changes the gate's input hash, so an approved gate reopens (`fin.diligence.gates`). The test
(tests/unit/test_gate_inputs.py) changes each column listed here and expects the hash to change; a
gate input added in code is added here with its test.

Inputs computed from many tables are checked through their own hash instead: G2's reconciliations
and G1's trial balance reconciliation (`fin.reconcile.store` input_hash), and G3's recast runs
(`fin.reconcile.recast`). G1's mapping items come from review/<company>/pending.json, not a table.
"""

GATE_INPUTS: dict[str, dict[str, tuple[str, ...]]] = {
    "G1": {
        "documents": ("sha256", "status"),
        "facts": ("account", "value"),  # through current_facts: the figures in use
        "stated_figures": ("value", "reason"),
        "fact_corrections": ("value", "reason"),
        "source_files": ("kind", "period", "sha256", "status"),
        "ledger_accounts": ("ledger", "account", "status"),
        "payroll_units": ("unit",),
        "bank_statements": ("ties", "closing_balance", "line_count"),
        "findings": ("check_name", "status"),  # of bank statements
    },
    "G2": {
        "accountant_queries": ("question", "status"),
    },
    "G3": {},
    "G4": {
        "adjustments": (
            "status",
            "amount",
            "category",
            "account",
            "period",
            "scope",
            "source_ref",
        ),
        "netdebt_items": ("period", "amount", "description", "evidence", "status"),
        "fdd_findings": (
            "status",
            "area",
            "title",
            "observation",
            "severity",
            "evidence",
            "final_amount",
        ),
        "netdebt_classes": ("ledger", "debt_class", "status"),
        "tax_ledgers": ("ledger", "tax_class", "status"),
        "related_party_links": ("party", "link_kind", "target", "status"),
    },
}
