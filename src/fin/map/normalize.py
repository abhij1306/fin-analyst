"""Label normalization: the key the dictionary matches on (PLAN.md Phase 2).

`normalize_label("(b) Financial assets - Trade receivables (Note 10)")` gives
`"financial assets trade receivables"`. Steps, in order:

1. Unicode NFKC, lower case; apostrophes dropped ("shareholders' funds" -> "shareholders funds").
2. Currency markers removed: ₹, Rs., INR, and a bracket left holding only "in" ("Basic (in ₹)").
3. Note references removed: "(Note 10)", "Note No. 3", "refer note 4".
4. Numbering removed: "(a)", "(iv)", "(ia)", "(2)", "(III - IV)", "(A+B+C)" anywhere, and "I.",
   "A.", "1)" at the start, and a bare roman numeral at the start ("III Total income").
5. "&" becomes "and"; every other character that is not a letter or digit becomes a space.
6. Runs of spaces collapse to one; ends trimmed.
"""

import re
import unicodedata

_APOSTROPHES = re.compile(r"['’‘`]")
_CURRENCY = re.compile(r"₹|\brs\b\.?|\binr\b")
_EMPTY_BRACKET = re.compile(r"\(\s*(?:in)?\s*\)")
_NOTE_REF = re.compile(
    r"\(?\s*(?:refer\s+)?\bnotes?\b(?:\s*no)?\.?\s*\d+[a-z]?"
    r"(?:\s*(?:,|and|&)\s*\d+[a-z]?)*\s*\)?"
)
# A roman numeral up to 39 (the lookahead stops it matching the empty string), one letter a-n, or a
# number of one or two digits; a numeral or number may carry a letter ("(ia)", "(2a)").
_NUMBER = r"(?:(?=[ivx])x{0,3}(?:ix|iv|v?i{0,3})[a-h]?|[a-n]|\d{1,2}[a-h]?)"
_BRACKETED_NUMBERING = re.compile(rf"\(\s*{_NUMBER}(?:\s*[+\-–]\s*{_NUMBER})*\s*\)")
_LEADING_NUMBERING = re.compile(r"^(?:(?:(?=[ivx])x{0,3}(?:ix|iv|v?i{0,3})|[a-z]|\d{1,2})[.)]\s+)+")
_LEADING_ROMAN = re.compile(r"^(?=[ivx])x{0,3}(?:ix|iv|v?i{0,3})\s+")
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def normalize_label(text: str) -> str:
    """The dictionary key for a label or section heading as written in a document."""
    label = unicodedata.normalize("NFKC", text).lower()
    label = _APOSTROPHES.sub("", label)
    label = _CURRENCY.sub(" ", label)
    label = _EMPTY_BRACKET.sub(" ", label)
    label = _NOTE_REF.sub(" ", label)
    label = _BRACKETED_NUMBERING.sub(" ", label)
    label = _LEADING_NUMBERING.sub("", label.strip())
    label = _LEADING_ROMAN.sub("", label)
    label = label.replace("&", " and ")
    return _NOT_WORD.sub(" ", label).strip()
