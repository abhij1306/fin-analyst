import pytest

from fin.extract.units import unit_in


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("(₹ in Lakhs)", "lakh"),
        ("(Rs. in Millions, except per equity share data)", "million"),
        ("Rs. In Lacs except for per share data", "lac"),
        # A line that is only a unit, after a rupee sign the text layer garbled.
        ("a: Crores", "crore"),
        ('"Crores', "crore"),
        ("Crores of shares were issued in the year", None),
        ("Particulars", None),
    ],
)
def test_unit_in(text: str, expected: str | None) -> None:
    assert unit_in(text) == expected
