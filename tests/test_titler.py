from conteur.titler import keywords


def test_keywords_ranks_by_frequency_ignoring_stopwords():
    text = ("Le loup et les trois chevreaux. Le loup mange. "
            "Les chevreaux fuient le loup dans la foret.")
    assert keywords(text, n=3) == ["loup", "chevreaux", "trois"]


def test_keywords_drops_short_words_and_numbers():
    text = "Le roi a dit 1789 et puis rien, roi roi chateau chateau"
    out = keywords(text, n=3)
    assert "1789" not in out
    assert "roi" not in out          # 3 lettres
    assert "chateau" in out


def test_keywords_handles_accents():
    assert keywords("La forêt, la forêt, la forêt profonde", n=1) == ["foret"]


def test_keywords_on_empty_text_is_empty():
    assert keywords("") == []
