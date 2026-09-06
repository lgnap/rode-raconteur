import pytest
from conteur.naming import slugify


@pytest.mark.parametrize("raw, expected", [
    ("Café Crème", "cafe-creme"),
    ("Il était une fois", "il-etait-une-fois"),
    ("Note  pour   le chapitre 3 !", "note-pour-le-chapitre-3"),
    ("---bonjour---", "bonjour"),
    ("", ""),
    ("!!!", ""),
])
def test_slugify_normalises(raw, expected):
    assert slugify(raw) == expected


def test_slugify_truncates_on_word_boundary():
    text = "le loup et les trois chevreaux qui vivaient dans la foret profonde"
    out = slugify(text, max_len=30)
    assert len(out) <= 30
    assert not out.endswith("-")
    assert out == "le-loup-et-les-trois-chevreaux"


def test_slugify_single_long_word_is_hard_cut():
    assert slugify("a" * 80, max_len=10) == "a" * 10
