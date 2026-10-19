"""Unit tests. The real translation APIs are NOT called - HTTP is mocked."""

import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import translators  # noqa: E402
from app import create_app  # noqa: E402
from translators import (  # noqa: E402
    DeepLTranslator,
    Language,
    MyMemoryTranslator,
    TranslationError,
    TranslationResult,
    _chunk_text,
)


# ---------------------------------------------------------------- helpers ---
class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeTranslator:
    label = "Fake"
    supports_detection = True

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def languages(self):
        return [
            Language("EN", "English", target=False),
            Language("EN-US", "English (American)", source=False),
            Language("FR", "French"),
            Language("DE", "German"),
            Language("AR", "Arabic"),
        ]

    def translate(self, text, target, source=None):
        self.calls.append((text, target, source))
        if self.error:
            raise self.error
        return TranslationResult(text=f"[{target}] {text}", detected_source="EN", provider="Fake")


@pytest.fixture()
def fake():
    return FakeTranslator()


@pytest.fixture()
def client(fake):
    app = create_app(translator=fake)
    app.config["TESTING"] = True
    return app.test_client()


def post(client, **body):
    return client.post("/api/translate", json=body)


# ------------------------------------------------------------ Flask routes ---
def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Lingua" in r.data and b"translateBtn" in r.data


def test_languages_endpoint(client):
    data = client.get("/api/languages").get_json()
    assert data["supports_detection"] is True
    assert {l["code"] for l in data["languages"]} >= {"EN", "FR", "DE"}


def test_translate_success_with_detection(client, fake):
    r = post(client, text="Hello", source="auto", target="fr")
    assert r.status_code == 200
    body = r.get_json()
    assert body["translation"] == "[FR] Hello"
    assert body["detected_source"] == "EN"
    assert fake.calls == [("Hello", "FR", None)]  # auto => no source passed to provider


def test_translate_with_explicit_source(client, fake):
    assert post(client, text="Hallo", source="de", target="EN-US").status_code == 200
    assert fake.calls[-1] == ("Hallo", "EN-US", "DE")


@pytest.mark.parametrize("body,fragment", [
    ({"text": "", "target": "FR"}, "enter some text"),
    ({"text": "   ", "target": "FR"}, "enter some text"),
    ({"text": 123, "target": "FR"}, "enter some text"),
    ({"text": "hi"}, "target language"),
    ({"text": "hi", "target": "XX"}, "Unsupported target"),
    ({"text": "hi", "target": "FR", "source": "XX"}, "Unsupported source"),
    ({"text": "hi", "target": "FR", "source": "FR"}, "same"),
    ({"text": "hi", "target": "EN"}, "Unsupported target"),  # EN is source-only
])
def test_validation_errors(client, fake, body, fragment):
    r = post(client, **body)
    assert r.status_code == 400
    assert fragment in r.get_json()["error"]
    assert fake.calls == []  # provider never called on invalid input


def test_text_too_long(client):
    r = post(client, text="a" * 6000, target="FR")
    assert r.status_code == 413


def test_non_json_body(client):
    r = client.post("/api/translate", data="hello", content_type="text/plain")
    assert r.status_code == 400


def test_auto_rejected_when_provider_has_no_detection(fake):
    fake.supports_detection = False
    c = create_app(translator=fake).test_client()
    r = post(c, text="hi", source="auto", target="FR")
    assert r.status_code == 400 and "detection" in r.get_json()["error"]


@pytest.mark.parametrize("status", [429, 502, 504])
def test_provider_errors_are_forwarded_cleanly(status):
    t = FakeTranslator(error=TranslationError("boom", status))
    c = create_app(translator=t).test_client()
    r = post(c, text="hi", target="FR")
    assert r.status_code == status and r.get_json()["error"] == "boom"


def test_unexpected_exception_is_hidden():
    t = FakeTranslator(error=RuntimeError("secret internals"))
    c = create_app(translator=t).test_client()
    r = post(c, text="hi", target="FR")
    assert r.status_code == 500
    assert "secret" not in r.get_data(as_text=True)


def test_rate_limit(monkeypatch, fake):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    c = create_app(translator=fake).test_client()
    assert post(c, text="a", target="FR").status_code == 200
    assert post(c, text="b", target="FR").status_code == 200
    assert post(c, text="c", target="FR").status_code == 429


def test_unconfigured_deepl_returns_clear_error(monkeypatch):
    monkeypatch.setenv("TRANSLATION_PROVIDER", "deepl")
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    c = create_app().test_client()
    r = post(c, text="hi", target="FR")
    assert r.status_code == 503 and "DEEPL_API_KEY" in r.get_json()["error"]


def test_api_key_never_exposed(monkeypatch):
    monkeypatch.setenv("TRANSLATION_PROVIDER", "deepl")
    monkeypatch.setenv("DEEPL_API_KEY", "super-secret-key:fx")
    c = create_app().test_client()
    for path in ("/", "/api/health", "/static/js/app.js"):
        assert "super-secret-key" not in c.get(path).get_data(as_text=True)


# ------------------------------------------------------------------ DeepL ---
@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(translators.time, "sleep", lambda *_: None)


def make_deepl(monkeypatch, responses):
    """Return (translator, recorded_calls). `responses` is a list of FakeResponse/Exception."""
    d = DeepLTranslator("abc123:fx")
    calls = []
    it = iter(responses)

    def fake_request(method, url, **kw):
        calls.append((method, url, kw))
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(d._session, "request", fake_request)
    return d, calls


def test_deepl_free_key_uses_free_host():
    assert DeepLTranslator("k:fx").base_url == "https://api-free.deepl.com"
    assert DeepLTranslator("k").base_url == "https://api.deepl.com"


def test_deepl_request_shape_and_auth_header(monkeypatch):
    d, calls = make_deepl(monkeypatch, [FakeResponse(200, {"translations": [{"detected_source_language": "EN", "text": "Bonjour"}]})])
    res = d.translate("Hello", "fr")
    assert res.text == "Bonjour" and res.detected_source == "EN"
    method, url, kw = calls[0]
    assert (method, url) == ("POST", "https://api-free.deepl.com/v2/translate")
    assert kw["json"] == {"text": ["Hello"], "target_lang": "FR"}  # no source_lang => detection
    assert d._session.headers["Authorization"] == "DeepL-Auth-Key abc123:fx"


def test_deepl_with_source_lang(monkeypatch):
    d, calls = make_deepl(monkeypatch, [FakeResponse(200, {"translations": [{"text": "Hi"}]})])
    d.translate("Hallo", "EN-US", "de")
    assert calls[0][2]["json"]["source_lang"] == "DE"


@pytest.mark.parametrize("status,expected_status,fragment", [
    (403, 502, "key was rejected"),
    (456, 429, "quota"),
    (413, 413, "too large"),
    (400, 400, "rejected the request"),
])
def test_deepl_error_mapping(monkeypatch, status, expected_status, fragment):
    d, _ = make_deepl(monkeypatch, [FakeResponse(status, {"message": "x"})])
    with pytest.raises(TranslationError) as e:
        d.translate("Hello", "FR")
    assert e.value.status == expected_status and fragment in e.value.message


def test_deepl_does_not_retry_456_or_400(monkeypatch):
    for status in (456, 400):
        d, calls = make_deepl(monkeypatch, [FakeResponse(status, {})])
        with pytest.raises(TranslationError):
            d.translate("Hello", "FR")
        assert len(calls) == 1


def test_deepl_retries_429_then_succeeds(monkeypatch, no_sleep):
    ok = FakeResponse(200, {"translations": [{"text": "Salut"}]})
    d, calls = make_deepl(monkeypatch, [FakeResponse(429, {}), ok])
    assert d.translate("Hi", "FR").text == "Salut"
    assert len(calls) == 2


def test_deepl_gives_up_after_retries(monkeypatch, no_sleep):
    d, calls = make_deepl(monkeypatch, [FakeResponse(503, {})] * 3)
    with pytest.raises(TranslationError) as e:
        d.translate("Hi", "FR")
    assert e.value.status == 503 and len(calls) == 3


def test_deepl_timeout_and_connection_errors(monkeypatch, no_sleep):
    d, _ = make_deepl(monkeypatch, [requests.Timeout()] * 3)
    with pytest.raises(TranslationError) as e:
        d.translate("Hi", "FR")
    assert e.value.status == 504
    d, _ = make_deepl(monkeypatch, [requests.ConnectionError()] * 3)
    with pytest.raises(TranslationError) as e:
        d.translate("Hi", "FR")
    assert e.value.status == 502


def test_deepl_malformed_response(monkeypatch):
    d, _ = make_deepl(monkeypatch, [FakeResponse(200, {"unexpected": True})])
    with pytest.raises(TranslationError):
        d.translate("Hi", "FR")


def test_deepl_languages_v3_parsing_and_fallback(monkeypatch):
    payload = [
        {"lang": "en", "name": "English", "usable_as_source": True, "usable_as_target": False},
        {"lang": "en-US", "name": "English (American)", "usable_as_source": False, "usable_as_target": True},
        {"lang": "de", "name": "German", "usable_as_source": True, "usable_as_target": True},
    ]
    d, calls = make_deepl(monkeypatch, [FakeResponse(200, payload)])
    langs = {l.code: l for l in d.languages()}
    assert calls[0][1].endswith("/v3/languages") and calls[0][2]["params"] == {"resource": "translate_text"}
    assert langs["EN"].source and not langs["EN"].target
    assert langs["EN-US"].target and not langs["EN-US"].source
    d.languages()  # cached -> no second HTTP call
    assert len(calls) == 1

    d2, _ = make_deepl(monkeypatch, [FakeResponse(403, {}), ])
    fallback = d2.languages()  # API failure -> static fallback list
    assert any(l.code == "FR" for l in fallback)


# --------------------------------------------------------------- MyMemory ---
def test_chunking_respects_byte_limit():
    text = ("This is a sentence. " * 80) + "é" * 600
    chunks = _chunk_text(text, 450)
    assert all(len(c.encode("utf-8")) <= 450 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_mymemory_success_and_request_shape(monkeypatch):
    m = MyMemoryTranslator("me@example.com")
    seen = {}

    def fake_get(url, params, timeout):
        seen.update(url=url, params=params)
        return FakeResponse(200, {"responseData": {"translatedText": "Bonjour"}, "responseStatus": 200})

    monkeypatch.setattr(m._session, "get", fake_get)
    assert m.translate("Hello", "fr", "en").text == "Bonjour"
    assert seen["params"] == {"q": "Hello", "langpair": "en|fr", "de": "me@example.com"}
    assert seen["url"] == "https://api.mymemory.translated.net/get"


def test_mymemory_requires_source():
    with pytest.raises(TranslationError) as e:
        MyMemoryTranslator().translate("Hello", "fr")
    assert e.value.status == 400


def test_mymemory_daily_limit_warning(monkeypatch):
    m = MyMemoryTranslator()
    monkeypatch.setattr(m._session, "get", lambda *a, **k: FakeResponse(200, {
        "responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY."},
        "responseStatus": 200}))
    with pytest.raises(TranslationError) as e:
        m.translate("Hello", "fr", "en")
    assert e.value.status == 429
