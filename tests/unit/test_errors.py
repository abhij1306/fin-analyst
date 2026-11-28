import fin.audit.errors as errors


def _subclasses(cls: type) -> list[type]:
    found = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_subclasses(sub))
    return found


def test_every_plan_code_has_one_error_class_and_its_exit_code() -> None:
    by_code = {cls.code: cls.exit_code for cls in _subclasses(errors.FinError) if cls.code}
    # docs/errors.md: setup, database and output errors stop the command (2);
    # per-document failures and a missing evidence source complete with failures (1); a refusal
    # pending user confirmation (3). E302 is a warning event and E401-E405 are finding codes
    # (fin.findings), not errors: they have no class.
    assert by_code == {
        "E001": 2, "E002": 2, "E003": 2, "E005": 2, "E006": 2, "E007": 2, "E008": 2,
        "E009": 2, "E010": 2, "E011": 2, "E012": 2,
        "E101": 1, "E102": 1, "E103": 1, "E104": 1, "E105": 1,
        "E201": 1, "E202": 1, "E203": 1, "E204": 1, "E205": 1, "E206": 1,
        "E301": 1, "E303": 1, "E304": 2, "E890": 1, "E891": 1,
        "E406": 2, "E407": 2, "E408": 2, "E409": 2,
        "E501": 2, "E502": 2, "E503": 2, "E504": 2, "E505": 2, "E506": 2, "E507": 2,
        "E601": 2, "E602": 2, "E603": 2, "E604": 1,
        "E701": 3,
        "E801": 3, "E802": 3,
        "E803": 2, "E804": 2, "E805": 2, "E806": 2,
        "E807": 2, "E808": 2, "E809": 2, "E810": 2, "E811": 2, "E812": 2,
        "E813": 2, "E814": 2, "E815": 2, "E816": 2, "E817": 2, "E818": 2, "E819": 2,
        "E820": 2, "E821": 2,
        "E830": 2, "E831": 2, "E832": 2, "E833": 2,
        "E840": 2, "E841": 2, "E842": 2, "E845": 2, "E846": 2, "E854": 2, "E855": 2,
        "E858": 2, "E859": 2, "E860": 2, "E861": 2, "E863": 2, "E864": 2, "E865": 2,
        "E868": 2, "E869": 2, "E873": 2, "E874": 2, "E881": 2,
        "E901": 2, "E902": 2, "E903": 2,
        "E910": 2,
        "E920": 2, "E921": 2, "E922": 2, "E923": 2,
        "E930": 2, "E931": 2, "E932": 2, "E933": 2, "E934": 2,
        "E940": 2, "E941": 2,
        "E950": 2, "E951": 2,
        "E960": 2, "E961": 2, "E962": 2,
        "E970": 2, "E971": 2, "E972": 2,
    }  # fmt: skip
    codes = [cls.code for cls in _subclasses(errors.FinError) if cls.code]
    assert len(codes) == len(set(codes))


def test_error_text_is_code_then_message() -> None:
    assert str(errors.DatabaseLocked("x.duckdb is locked")) == "E501: x.duckdb is locked"
