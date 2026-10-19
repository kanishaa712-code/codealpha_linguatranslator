"""Translation providers for Lingua AI Translator.

Two providers are implemented, both against their *documented* HTTP APIs:

* DeepL API (Free or Pro)  - https://developers.deepl.com
    POST {base}/v2/translate        (JSON body, header "Authorization: DeepL-Auth-Key <key>")
    GET  {base}/v3/languages?resource=translate_text
  Supports automatic source-language detection (omit ``source_lang``; the
  response contains ``detected_source_language``).

* MyMemory - https://mymemory.translated.net/doc/spec.php
    GET https://api.mymemory.translated.net/get?q=...&langpair=en|fr
  Needs no key. The documented spec does not describe automatic language
  detection, so this app does NOT offer auto-detect with this provider.

API keys are read from the environment only and are never sent to the browser.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests


class TranslationError(Exception):
    """An error that is safe to show to the end user."""

    def __init__(self, message: str, status: int = 502, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


@dataclass
class Language:
    code: str
    name: str
    source: bool = True
    target: bool = True

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "source": self.source, "target": self.target}


@dataclass
class TranslationResult:
    text: str
    detected_source: str | None = None
    provider: str = ""
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# DeepL
# --------------------------------------------------------------------------- #

# Fallback list, used only if GET /v3/languages cannot be reached. Codes are
# taken from DeepL's "Languages supported" documentation page. Variants such as
# EN-US / PT-BR are target-only; their base codes (EN, PT) are source-only.
_DEEPL_FALLBACK = [
    ("AR", "Arabic"), ("BG", "Bulgarian"), ("CS", "Czech"), ("DA", "Danish"),
    ("NL", "Dutch"), ("EN", "English"), ("EN-GB", "English (British)"),
    ("EN-US", "English (American)"), ("ET", "Estonian"), ("FI", "Finnish"),
    ("FR", "French"), ("DE", "German"), ("EL", "Greek"), ("HE", "Hebrew"),
    ("HI", "Hindi"), ("HU", "Hungarian"), ("ID", "Indonesian"), ("IT", "Italian"),
    ("JA", "Japanese"), ("KO", "Korean"), ("LV", "Latvian"), ("LT", "Lithuanian"),
    ("NB", "Norwegian (Bokmål)"), ("FA", "Persian"), ("PL", "Polish"),
    ("PT", "Portuguese"), ("PT-BR", "Portuguese (Brazilian)"),
    ("PT-PT", "Portuguese (European)"), ("RO", "Romanian"), ("RU", "Russian"),
    ("SK", "Slovak"), ("SL", "Slovenian"), ("ES", "Spanish"), ("SV", "Swedish"),
    ("TH", "Thai"), ("TR", "Turkish"), ("UK", "Ukrainian"), ("UR", "Urdu"),
    ("VI", "Vietnamese"), ("ZH", "Chinese"), ("ZH-HANS", "Chinese (simplified)"),
    ("ZH-HANT", "Chinese (traditional)"),
]
_DEEPL_TARGET_ONLY = {"EN-GB", "EN-US", "PT-BR", "PT-PT", "ZH-HANS", "ZH-HANT",
                      "ES-419", "FR-CA", "FR-FR", "DE-DE", "DE-CH", "ES-ES"}
_DEEPL_SOURCE_ONLY = {"EN", "PT", "ZH"}


class DeepLTranslator:
    name = "deepl"
    label = "DeepL"
    supports_detection = True

    FREE_URL = "https://api-free.deepl.com"
    PRO_URL = "https://api.deepl.com"
    TIMEOUT = 20  # seconds
    MAX_RETRIES = 2  # DeepL docs: retry 429/5xx with backoff; never retry 456/400

    def __init__(self, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError("DEEPL_API_KEY is not set")
        self._key = api_key.strip()
        # DeepL docs: Free keys end in ":fx" and must use api-free.deepl.com.
        self.base_url = (base_url or (self.FREE_URL if self._key.endswith(":fx") else self.PRO_URL)).rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"DeepL-Auth-Key {self._key}",
            "User-Agent": "LinguaAITranslator/1.0 (CodeAlpha internship project)",
        })
        self._lang_cache: tuple[float, list[Language]] | None = None

    # -- internals ---------------------------------------------------------- #
    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = self._session.request(method, url, timeout=self.TIMEOUT, **kwargs)
            except requests.Timeout as exc:
                last_exc = exc
                if attempt < self.MAX_RETRIES:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise TranslationError("The translation service timed out. Please try again.", 504) from exc
            except requests.ConnectionError as exc:
                last_exc = exc
                if attempt < self.MAX_RETRIES:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise TranslationError("Could not reach the translation service. Check your internet connection.", 502) from exc
            except requests.RequestException as exc:  # pragma: no cover - defensive
                raise TranslationError("Unexpected network error while contacting the translation service.", 502) from exc

            # Retry only 429 and 5xx (but not 456), as recommended by DeepL.
            if resp.status_code == 429 or (resp.status_code >= 500 and resp.status_code != 456):
                if attempt < self.MAX_RETRIES:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
            return resp
        raise TranslationError("Translation service is unavailable.", 502) from last_exc  # pragma: no cover

    @staticmethod
    def _api_message(resp: requests.Response) -> str:
        try:
            data = resp.json()
            if isinstance(data, dict):
                if isinstance(data.get("message"), str):
                    return data["message"]
                if isinstance(data.get("error"), dict) and isinstance(data["error"].get("message"), str):
                    return data["error"]["message"]
        except ValueError:
            pass
        return ""

    def _raise_for_status(self, resp: requests.Response) -> None:
        s = resp.status_code
        if s < 400:
            return
        detail = self._api_message(resp)
        if s == 403:
            raise TranslationError(
                "The DeepL API key was rejected. Check DEEPL_API_KEY in your .env file "
                "(use an API key, not a DeepL web-app login; Free keys end in ':fx').", 502)
        if s == 456:
            raise TranslationError(
                "The DeepL character quota for this billing period is used up. "
                "It resets next period, or you can upgrade the plan.", 429)
        if s == 429 or s == 529:
            raise TranslationError("Too many requests to DeepL. Please wait a moment and try again.", 429, retry_after=5)
        if s == 413:
            raise TranslationError("The text is too large for the translation service.", 413)
        if s == 400:
            raise TranslationError(f"The translation service rejected the request. {detail}".strip(), 400)
        if s in (500, 502, 503, 504):
            raise TranslationError("DeepL is temporarily unavailable. Please try again shortly.", 503)
        raise TranslationError(f"Translation service error (HTTP {s}). {detail}".strip(), 502)

    # -- public API --------------------------------------------------------- #
    def languages(self) -> list[Language]:
        """Languages from GET /v3/languages?resource=translate_text (cached 1h)."""
        now = time.time()
        if self._lang_cache and now - self._lang_cache[0] < 3600:
            return self._lang_cache[1]
        langs: list[Language] = []
        try:
            resp = self._request("GET", "/v3/languages", params={"resource": "translate_text"})
            if resp.status_code == 200:
                for item in resp.json():
                    code = str(item.get("lang", "")).upper()
                    if not code:
                        continue
                    langs.append(Language(
                        code=code,
                        name=item.get("name") or code,
                        source=bool(item.get("usable_as_source", False)),
                        target=bool(item.get("usable_as_target", False)),
                    ))
            else:
                self._raise_for_status(resp)
        except (TranslationError, ValueError, AttributeError):
            langs = []
        if not langs:
            langs = [
                Language(c, n, source=c not in _DEEPL_TARGET_ONLY, target=c not in _DEEPL_SOURCE_ONLY)
                for c, n in _DEEPL_FALLBACK
            ]
        else:
            self._lang_cache = (now, langs)
        langs.sort(key=lambda l: l.name.lower())
        return langs

    def translate(self, text: str, target: str, source: str | None = None) -> TranslationResult:
        payload: dict = {"text": [text], "target_lang": target.upper()}
        if source:
            payload["source_lang"] = source.upper()
        resp = self._request("POST", "/v2/translate", json=payload)
        self._raise_for_status(resp)
        try:
            item = resp.json()["translations"][0]
            return TranslationResult(
                text=item["text"],
                detected_source=item.get("detected_source_language"),
                provider=self.label,
            )
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise TranslationError("Received an unexpected response from DeepL.", 502) from exc


# --------------------------------------------------------------------------- #
# MyMemory (keyless fallback)
# --------------------------------------------------------------------------- #

# Curated list of common languages (ISO 639-1 codes; MyMemory accepts ISO / RFC 3066).
# This is a curated list, NOT queried from the API (the spec has no language endpoint).
_MYMEMORY_LANGS = [
    ("af", "Afrikaans"), ("sq", "Albanian"), ("ar", "Arabic"), ("hy", "Armenian"),
    ("bn", "Bengali"), ("bg", "Bulgarian"), ("ca", "Catalan"), ("zh-CN", "Chinese (Simplified)"),
    ("zh-TW", "Chinese (Traditional)"), ("hr", "Croatian"), ("cs", "Czech"), ("da", "Danish"),
    ("nl", "Dutch"), ("en", "English"), ("et", "Estonian"), ("fi", "Finnish"),
    ("fr", "French"), ("de", "German"), ("el", "Greek"), ("gu", "Gujarati"),
    ("he", "Hebrew"), ("hi", "Hindi"), ("hu", "Hungarian"), ("is", "Icelandic"),
    ("id", "Indonesian"), ("it", "Italian"), ("ja", "Japanese"), ("kn", "Kannada"),
    ("ko", "Korean"), ("lv", "Latvian"), ("lt", "Lithuanian"), ("ms", "Malay"),
    ("ml", "Malayalam"), ("mr", "Marathi"), ("ne", "Nepali"), ("no", "Norwegian"),
    ("fa", "Persian"), ("pl", "Polish"), ("pt", "Portuguese"), ("pa", "Punjabi"),
    ("ro", "Romanian"), ("ru", "Russian"), ("sr", "Serbian"), ("sk", "Slovak"),
    ("sl", "Slovenian"), ("es", "Spanish"), ("sw", "Swahili"), ("sv", "Swedish"),
    ("ta", "Tamil"), ("te", "Telugu"), ("th", "Thai"), ("tr", "Turkish"),
    ("uk", "Ukrainian"), ("ur", "Urdu"), ("vi", "Vietnamese"), ("cy", "Welsh"),
]


def _chunk_text(text: str, max_bytes: int = 450) -> list[str]:
    """Split text into chunks of at most ``max_bytes`` UTF-8 bytes (MyMemory max is 500)."""
    chunks: list[str] = []
    current = ""
    # Split on whitespace boundaries but keep sentence-ish pieces together.
    pieces: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in ".!?。！？\n":
            pieces.append(buf)
            buf = ""
    if buf:
        pieces.append(buf)

    def flush():
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for piece in pieces:
        if len(piece.encode("utf-8")) > max_bytes:
            flush()
            # Hard-split an oversized sentence on characters.
            part = ""
            for ch in piece:
                if len((part + ch).encode("utf-8")) > max_bytes:
                    chunks.append(part)
                    part = ch
                else:
                    part += ch
            current = part
            continue
        if len((current + piece).encode("utf-8")) > max_bytes:
            flush()
        current += piece
    flush()
    return [c for c in chunks if c.strip()] or [text]


class MyMemoryTranslator:
    name = "mymemory"
    label = "MyMemory"
    supports_detection = False

    URL = "https://api.mymemory.translated.net/get"
    TIMEOUT = 20

    def __init__(self, email: str | None = None):
        # Optional: a valid email in the "de" parameter raises the documented free
        # daily limit (see README). It is only sent to MyMemory, never to the browser.
        self.email = (email or "").strip() or None
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "LinguaAITranslator/1.0 (CodeAlpha internship project)"

    def languages(self) -> list[Language]:
        return [Language(c, n) for c, n in sorted(_MYMEMORY_LANGS, key=lambda x: x[1].lower())]

    def _translate_chunk(self, chunk: str, source: str, target: str) -> str:
        params = {"q": chunk, "langpair": f"{source}|{target}"}
        if self.email:
            params["de"] = self.email
        try:
            resp = self._session.get(self.URL, params=params, timeout=self.TIMEOUT)
        except requests.Timeout as exc:
            raise TranslationError("The translation service timed out. Please try again.", 504) from exc
        except requests.RequestException as exc:
            raise TranslationError("Could not reach the translation service. Check your internet connection.", 502) from exc

        if resp.status_code == 429:
            raise TranslationError("MyMemory's free usage limit was reached. Try again later.", 429, retry_after=60)
        if resp.status_code >= 500:
            raise TranslationError("MyMemory is temporarily unavailable. Please try again shortly.", 503)
        if resp.status_code >= 400:
            raise TranslationError(f"Translation service error (HTTP {resp.status_code}).", 502)
        try:
            data = resp.json()
            status = int(data.get("responseStatus", resp.status_code))
            translated = data["responseData"]["translatedText"]
        except (ValueError, KeyError, TypeError) as exc:
            raise TranslationError("Received an unexpected response from MyMemory.", 502) from exc

        if status == 429 or (isinstance(translated, str) and translated.upper().startswith("MYMEMORY WARNING")):
            raise TranslationError("MyMemory's free daily limit was reached. Try again tomorrow or set MYMEMORY_EMAIL / use DeepL.", 429, retry_after=3600)
        if status != 200:
            detail = data.get("responseDetails") or ""
            raise TranslationError(f"The translation service rejected the request. {detail}".strip(), 400 if status == 400 else 502)
        return translated

    def translate(self, text: str, target: str, source: str | None = None) -> TranslationResult:
        if not source:
            raise TranslationError("Automatic language detection is not available with the MyMemory provider. Please choose a source language.", 400)
        parts = [self._translate_chunk(c, source, target) for c in _chunk_text(text)]
        return TranslationResult(text=" ".join(p.strip() for p in parts), provider=self.label)
