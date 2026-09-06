from types import SimpleNamespace

from conteur.titler import keywords, make_title, strip_think, title_from_ollama


def _post(response=None, exc=None):
    def post(url, json=None, timeout=None):
        post.seen = SimpleNamespace(url=url, json=json, timeout=timeout)
        if exc:
            raise exc
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"response": response},
            raise_for_status=lambda: None,
        )
    return post


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


def test_frequent_stopwords_are_excluded():
    # "avec" et "dans" sont plus fréquents que les vrais mots-clés : seul le
    # filtre de mots-vides peut les écarter, la longueur ne suffit pas.
    text = "avec avec avec avec dans dans dans dans chateau chateau foret"
    out = keywords(text, n=2)
    assert "avec" not in out
    assert "dans" not in out
    assert out == ["chateau", "foret"]


def test_strip_think_removes_reasoning_blocks():
    assert strip_think("<think>bla\nbla</think>\nLe loup") == "Le loup"
    assert strip_think("pas de bloc") == "pas de bloc"


def test_title_from_ollama_returns_clean_title():
    post = _post("Le loup et les trois chevreaux")
    assert title_from_ollama("il etait une fois", post=post) \
        == "Le loup et les trois chevreaux"


def test_title_request_disables_thinking_and_streaming():
    post = _post("titre")
    title_from_ollama("texte", post=post)
    assert post.seen.json["stream"] is False
    assert post.seen.json["think"] is False
    assert post.seen.json["model"] == "qwen3:8b"
    assert post.seen.timeout == 20.0


def test_title_strips_think_block_defensively():
    post = _post("<think>je reflechis</think>Le loup")
    assert title_from_ollama("texte", post=post) == "Le loup"


def test_empty_response_is_rejected():
    assert title_from_ollama("texte", post=_post("   ")) is None


def test_overlong_response_is_rejected():
    assert title_from_ollama("texte", post=_post("x" * 121)) is None


def test_multiline_response_is_rejected():
    assert title_from_ollama("texte", post=_post("Titre\nExplication")) is None


def test_network_failure_returns_none():
    assert title_from_ollama("texte", post=_post(exc=OSError("refused"))) is None


def test_make_title_falls_back_to_keywords():
    text = "Le loup et les chevreaux. Le loup mange les chevreaux."
    out = make_title(text, post=_post(exc=OSError("down")))
    assert "loup" in out and "chevreaux" in out


def test_make_title_tracked_reports_its_source():
    from conteur.titler import make_title_tracked

    title, origin = make_title_tracked("texte", post=_post("Le loup"))
    assert (title, origin) == ("Le loup", "title")

    text = "Le loup et les chevreaux. Le loup mange les chevreaux."
    fallback, origin = make_title_tracked(text, post=_post(exc=OSError("down")))
    assert origin == "keywords"
    assert "loup" in fallback
