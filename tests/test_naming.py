import pytest
from conteur.naming import slugify, choose_name


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


def _boom(_text):
    raise AssertionError("make_title ne doit pas être appelé sous le seuil")


def test_short_speech_uses_the_transcript_verbatim():
    assert choose_name("Note pour le chapitre trois", 5.0, _boom) \
        == "note-pour-le-chapitre-trois"


def test_threshold_is_inclusive_of_the_short_branch():
    assert choose_name("Bonjour", 12.0, _boom) == "bonjour"


def test_long_speech_delegates_to_make_title():
    calls = []

    def fake_title(text):
        calls.append(text)
        return "Le loup et les trois chevreaux"

    assert choose_name("il etait une fois...", 12.1, fake_title) \
        == "le-loup-et-les-trois-chevreaux"
    assert calls == ["il etait une fois..."]


def test_empty_transcript_is_named_silence():
    assert choose_name("", 0.0, _boom) == "silence"
    assert choose_name("   ", 30.0, _boom) == "silence"


def test_unusable_title_falls_back_to_sans_nom():
    assert choose_name("du texte", 30.0, lambda _t: "!!!") == "sans-nom"
