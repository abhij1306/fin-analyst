import fin.audit.errors as errors


def _subclasses(cls: type) -> list[type]:
    found = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_subclasses(sub))
    return found


def test_every_plan_code_has_one_error_class_and_its_exit_code() -> None:
    by_code = {cls.code: cls.exit_code for cls in _subclasses(errors.FinError) if cls.code}
    # README.md "Error codes": setup, database and output errors stop the command (2);
    # per-document failures and a missing evidence source complete with failures (1); a refusal
    # pending user confirmation (3). E302 is a warning event and E401-E405 are finding codes
    # (fin.findings), not errors: they have no class.
    assert by_code == {
        "E001": 2, "E002": 2, "E003": 2, "E005": 2,
        "E101": 1, "E102": 1, "E103": 1, "E104": 1, "E105": 1,
        "E201": 1, "E202": 1, "E203": 1, "E204": 1, "E205": 1, "E206": 1,
        "E301": 1, "E303": 1, "E304": 2,
        "E406": 2,
        "E501": 2, "E502": 2, "E503": 2, "E504": 2,
        "E601": 2, "E602": 2, "E603": 2, "E604": 1,
        "E701": 3,
        "E803": 2, "E804": 2, "E805": 2,
    }  # fmt: skip
    codes = [cls.code for cls in _subclasses(errors.FinError) if cls.code]
    assert len(codes) == len(set(codes))


def test_error_text_is_code_then_message() -> None:
    assert str(errors.DatabaseLocked("x.duckdb is locked")) == "E501: x.duckdb is locked"
