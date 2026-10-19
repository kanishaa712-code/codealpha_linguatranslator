# Lingua AI Translator

A responsive language translation web app built for the **CodeAlpha Artificial Intelligence Internship**.
Flask (Python) backend · HTML/CSS/JavaScript frontend · powered by the **DeepL API** (with an optional key-less **MyMemory** fallback).

## Features

- Enter text, choose a source and a target language, and translate.
- **Automatic language detection** (DeepL only – see [API notes](#api-notes-verified-against-official-docs)).
- Copy, clear and **swap-languages** buttons; `Ctrl/⌘ + Enter` shortcut.
- Loading spinner and skeleton, dismissible error banner, live character counter.
- Server-side + client-side input validation (empty text, length limit, unsupported or identical languages).
- Handles API failures: bad key, exhausted quota, rate limiting (with retry/backoff), timeouts, network errors, malformed responses.
- Language list is fetched from the provider's API at runtime (DeepL `GET /v3/languages`), with a built-in fallback list.
- Right-to-left output for Arabic, Hebrew, Persian, Urdu, etc.
- Per-IP rate limit (default 30 requests/min) so a bug can't burn through your free quota.
- API key lives only in the server environment – it is never sent to the browser.

## Project structure

```
CodeAlpha_LanguageTranslationTool/
├── app.py                 # Flask app: routes, validation, rate limiting
├── translators.py         # DeepL + MyMemory provider classes, error mapping
├── templates/
│   └── index.html         # UI markup
├── static/
│   ├── css/style.css      # Gradient / glass design, responsive layout
│   └── js/app.js          # Frontend logic (fetch, swap, copy, clear, errors)
├── tests/
│   └── test_app.py        # Unit tests (HTTP is mocked – no real API calls)
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # + pytest
├── .env.example           # Template for your secrets (copy to .env)
├── .gitignore             # Keeps .env and .venv out of Git
└── README.md
```

## Quick start

Requires Python 3.9+.

```bash
# 1. Get the code
git clone https://github.com/<your-username>/CodeAlpha_LanguageTranslationTool.git
cd CodeAlpha_LanguageTranslationTool

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1
                                   # Windows (cmd):        .venv\Scripts\activate.bat

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure your API key (see next section)
cp .env.example .env               # Windows: copy .env.example .env
# open .env and paste your key after DEEPL_API_KEY=

# 5. Run
python app.py
```

Open <http://127.0.0.1:5000>.

## API configuration

### Option A – DeepL API Free (recommended)

1. Go to <https://www.deepl.com/pro-api> and sign up for **DeepL API Free**.
   - DeepL's docs say that if you already have a regular DeepL Translator account, you may need to log out and create a *new* account for the API.
   - Third-party guides report that signup asks for a **credit card for identity verification**, and DeepL only accepts cards from supported countries. I could not verify the current country list – check DeepL's signup page. If you can't sign up, use Option B.
2. Open <https://www.deepl.com/your-account/keys> and copy your **API key**. Free keys end in `:fx`.
3. Put it in `.env`:
   ```
   TRANSLATION_PROVIDER=deepl
   DEEPL_API_KEY=your-key-here:fx
   ```
4. Restart the app.

**Keeping the key safe**
- Only put it in `.env` (or your host's environment-variable settings). It is never hardcoded in the source.
- `.env` is in `.gitignore`. Before every push, run `git ls-files | grep -i "^\.env"` – only `.env.example` should appear.
- The key is used only by the Flask server; the browser never sees it. (DeepL's docs also say not to use API keys in client-side code.)
- If a key leaks, deactivate it and create a new one in the "API Keys" tab of your DeepL account.

### Option B – MyMemory (no key needed)

```
TRANSLATION_PROVIDER=mymemory
MYMEMORY_EMAIL=you@example.com   # optional, raises the daily limit
```

Trade-offs: much smaller free limit, lower quality for many pairs, **no automatic language detection** (the documented spec doesn't offer it, so the app disables it), 500-byte limit per request (the app splits long text into chunks automatically), and a curated language list rather than one fetched from an API.

If `TRANSLATION_PROVIDER` is empty, the app uses DeepL when `DEEPL_API_KEY` is set and MyMemory otherwise.

## API notes (verified against official docs)

Checked on 2026-10-15 against DeepL's developer documentation:

| Item | Value |
|---|---|
| Translate endpoint | `POST https://api-free.deepl.com/v2/translate` (Free) · `https://api.deepl.com/v2/translate` (Pro) |
| Auth | Header `Authorization: DeepL-Auth-Key <key>` |
| Body | JSON `{"text": ["..."], "target_lang": "DE", "source_lang": "EN"}` – omit `source_lang` to auto-detect |
| Response | `{"translations":[{"detected_source_language":"EN","text":"..."}]}` |
| Languages | `GET /v3/languages?resource=translate_text` (the older `/v2/languages` is marked deprecated) |
| Request size | 128 KiB per translate request |
| Error codes used | 403 bad key · 429/529 too many requests · **456 quota exhausted** · 413 too large · 5xx server |
| Retry guidance | Retry 429 and 5xx with backoff; do **not** retry 456 or 400 (the app follows this) |
| Target variants | `EN-US`, `EN-GB`, `PT-BR`, `PT-PT`, `ZH-HANS`, `ZH-HANT`… are **target-only**; use `EN`, `PT`, `ZH` as the source |

DeepL's supported-languages page lists well over 100 languages, including Urdu, Hindi, Arabic, Persian, Chinese, Japanese, Korean and many more.

### Limits and costs – honestly

- **DeepL API Free:** up to **500,000 characters per month**, no daily cap. Characters are counted on the *source* text (Unicode code points), and a request still counts if source and target are the same language. When the quota is gone, requests fail with HTTP 456 until the next period.
- **DeepL API Pro:** no fixed monthly cap, pay-as-you-go. Third-party sources quote roughly **$25 per million characters** plus a small monthly fee (figures differ by source: €4.99 / $5.49), so check <https://www.deepl.com/pro-api> for the current price before upgrading. This project never upgrades anything for you.
- **MyMemory (per its usage-limits page):** 5,000 characters/day anonymous, 50,000/day with a valid email in the `de` parameter. The page doesn't say what happens at the limit; the app reports a friendly "daily limit reached" error when it sees MyMemory's warning text.
- Machine translation can be wrong or awkward – don't rely on it for legal, medical or other critical text.
- Text you translate is sent to the provider. Read their privacy terms before translating sensitive content.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The 39 unit tests mock all HTTP, so they run offline and use **none** of your quota. They cover the Flask routes, validation, error mapping, DeepL request shape/auth header/retry rules, language parsing, MyMemory chunking and limit handling, rate limiting, and that the API key never appears in any response.

> **What has and hasn't been verified:** the unit tests pass and the server starts and serves the page, static files, `/api/languages`, validation errors and a clean network-failure error. **Live calls to DeepL and MyMemory were not made** (the build sandbox blocks outbound API hosts and no real key was available), and the UI was not exercised in a real browser. Request/response formats follow the official docs above; please run the manual checks below with your own key.

## Manual testing examples

With the app running, open <http://127.0.0.1:5000> and try these (the exact wording of translations will vary):

| # | Source | Target | Input | What to check |
|---|---|---|---|---|
| 1 | English | French | `Hello, how are you today?` | A French sentence appears |
| 2 | Detect language | English (American) | `Guten Morgen, wie geht es dir?` | Badge says "Detected: German" |
| 3 | Spanish | Japanese | `Me gustaría reservar una mesa para dos.` | Japanese text appears |
| 4 | English | Arabic | `Welcome to our city` | Output is right-to-left |
| 5 | English | Urdu | `Thank you very much` | Urdu text appears |
| 6 | Detect language | Chinese (simplified) | `Bonjour tout le monde` | Detected French, Chinese output |
| 7 | English | Portuguese (Brazilian) | `Where is the train station?` | Portuguese output |

Button and error checks: **Swap** (after translating #1 the boxes exchange languages and the result moves into the input), **Copy** (button briefly says "Copied!"), **Clear** (both boxes reset), empty input (message, no request sent), same source and target (message), stopping the server and pressing Translate (network error banner), and an invalid key (clear "key was rejected" message).

The same checks from a terminal:

```bash
# Health + languages
curl -s http://127.0.0.1:5000/api/health
curl -s http://127.0.0.1:5000/api/languages | head -c 400

# English -> French
curl -s -X POST http://127.0.0.1:5000/api/translate \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello, how are you today?","source":"en","target":"fr"}'

# Auto-detect (DeepL) -> English
curl -s -X POST http://127.0.0.1:5000/api/translate \
  -H "Content-Type: application/json" \
  -d '{"text":"Guten Morgen","source":"auto","target":"EN-US"}'

# Validation error (empty text) -> HTTP 400
curl -s -i -X POST http://127.0.0.1:5000/api/translate \
  -H "Content-Type: application/json" -d '{"text":"","target":"fr"}'
```

(Windows PowerShell: use `curl.exe` and put the JSON in a file with `-d "@body.json"`.)

## Backend API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | The web UI |
| GET | `/api/health` | Provider status (never includes the key) |
| GET | `/api/languages` | `{provider, supports_detection, languages:[{code,name,source,target}]}` |
| POST | `/api/translate` | Body `{text, source ("auto" or code), target}` → `{translation, detected_source, provider, characters}` or `{error}` |

## Uploading to GitHub

```bash
cd CodeAlpha_LanguageTranslationTool
git init
git add .
git status                 # confirm .env and .venv are NOT listed
git commit -m "Add Lingua AI Translator (Flask + DeepL)"
git branch -M main
```

Create an empty repository named `CodeAlpha_LanguageTranslationTool` on <https://github.com/new> (don't add a README there), then:

```bash
git remote add origin https://github.com/<your-username>/CodeAlpha_LanguageTranslationTool.git
git push -u origin main
```

With the GitHub CLI instead: `gh repo create CodeAlpha_LanguageTranslationTool --public --source=. --push`.

Final check: open the repo on GitHub and make sure there is no `.env` file. If a real key was ever committed, **revoke it in your DeepL account and create a new one** – deleting the file later does not remove it from Git history.

## Troubleshooting

| Message | Cause / fix |
|---|---|
| "DEEPL_API_KEY is missing" | `.env` not created, or the key line is empty. Restart after editing. |
| "The DeepL API key was rejected" | Wrong or deactivated key, or you copied a web-app credential instead of an API key. |
| "…character quota … is used up" | Free 500,000 characters spent this period (HTTP 456). |
| "Automatic detection isn't available" | You're on MyMemory – pick a source language, or use DeepL. |
| Can't reach the service | No internet, firewall/proxy, or the provider is down. |

## License

Educational project – use and adapt freely for learning.
