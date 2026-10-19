"""Lingua AI Translator - Flask backend.

Run:  python app.py      (development)
The browser only ever talks to this server; the translation API key stays here.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from translators import (
    DeepLTranslator,
    MyMemoryTranslator,
    TranslationError,
)

load_dotenv()  # reads .env (never committed) into the process environment

DEFAULT_MAX_CHARS = 5000


class RateLimiter:
    """Tiny in-memory per-IP limiter so a stray loop can't burn your API quota."""

    def __init__(self, limit: int, window: int = 60):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, int(self.window - (now - q[0])) + 1
            q.append(now)
            return True, 0


def build_translator():
    """Pick the provider from the environment. Never hardcode keys here."""
    provider = os.getenv("TRANSLATION_PROVIDER", "").strip().lower()
    deepl_key = os.getenv("DEEPL_API_KEY", "").strip()
    if provider == "deepl" or (not provider and deepl_key):
        if not deepl_key:
            return None, "DEEPL_API_KEY is missing. Copy .env.example to .env and add your key."
        return DeepLTranslator(deepl_key, os.getenv("DEEPL_API_URL") or None), None
    if provider in ("", "mymemory"):
        return MyMemoryTranslator(os.getenv("MYMEMORY_EMAIL")), None
    return None, f"Unknown TRANSLATION_PROVIDER '{provider}'. Use 'deepl' or 'mymemory'."


def create_app(translator=None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CHARS"] = int(os.getenv("MAX_CHARS", DEFAULT_MAX_CHARS))
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024  # reject absurd request bodies early

    config_error = None
    if translator is None:
        translator, config_error = build_translator()
    limiter = RateLimiter(int(os.getenv("RATE_LIMIT_PER_MIN", "30")))

    def err(message: str, status: int = 400, **extra):
        resp = jsonify({"error": message, **extra})
        resp.status_code = status
        return resp

    @app.get("/")
    def index():
        return render_template("index.html", max_chars=app.config["MAX_CHARS"])

    @app.get("/api/health")
    def health():
        return jsonify({
            "status": "ok" if translator else "misconfigured",
            "provider": getattr(translator, "label", None),
            "message": config_error,
        })

    @app.get("/api/languages")
    def languages():
        if not translator:
            return err(config_error or "Translator is not configured.", 503)
        langs = translator.languages()
        return jsonify({
            "provider": translator.label,
            "supports_detection": translator.supports_detection,
            "languages": [l.to_dict() for l in langs],
        })

    @app.post("/api/translate")
    def translate():
        if not translator:
            return err(config_error or "Translator is not configured.", 503)

        ok, wait = limiter.allow(request.remote_addr or "unknown")
        if not ok:
            return err(f"Too many requests. Please wait {wait}s and try again.", 429, retry_after=wait)

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return err("Request body must be JSON.")

        text = data.get("text")
        source = data.get("source", "auto")
        target = data.get("target")

        # ---- input validation ------------------------------------------------
        if not isinstance(text, str) or not text.strip():
            return err("Please enter some text to translate.")
        text = text.strip()
        if len(text) > app.config["MAX_CHARS"]:
            return err(f"Text is too long ({len(text)} characters). The limit is {app.config['MAX_CHARS']}.", 413)
        if not isinstance(target, str) or not target:
            return err("Please choose a target language.")
        if not isinstance(source, str) or not source:
            return err("Invalid source language.")

        langs = translator.languages()
        valid_targets = {l.code.upper(): l.code for l in langs if l.target}
        valid_sources = {l.code.upper(): l.code for l in langs if l.source}

        if target.upper() not in valid_targets:
            return err(f"Unsupported target language: {target}.")
        target_code = valid_targets[target.upper()]

        if source.lower() == "auto":
            if not translator.supports_detection:
                return err("Automatic detection isn't available with this provider. Please choose a source language.")
            source_code = None
        else:
            if source.upper() not in valid_sources:
                return err(f"Unsupported source language: {source}.")
            source_code = valid_sources[source.upper()]
            if source_code.upper() == target_code.upper():
                return err("Source and target languages are the same. Please pick two different languages.")

        # ---- call provider ---------------------------------------------------
        try:
            result = translator.translate(text, target_code, source_code)
        except TranslationError as exc:
            extra = {"retry_after": exc.retry_after} if exc.retry_after else {}
            return err(exc.message, exc.status, **extra)
        except Exception:  # last-resort guard; details go to the server log only
            app.logger.exception("Unhandled error during translation")
            return err("Something went wrong on the server. Please try again.", 500)

        return jsonify({
            "translation": result.text,
            "detected_source": result.detected_source,
            "provider": result.provider,
            "characters": len(text),
        })

    @app.errorhandler(413)
    def too_large(_):
        return err("Request is too large.", 413)

    @app.errorhandler(404)
    def not_found(_):
        return err("Not found.", 404)

    @app.errorhandler(405)
    def bad_method(_):
        return err("Method not allowed.", 405)

    return app


app = create_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
