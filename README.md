# NABD SI — secure production baseline

A bilingual AI and cybersecurity news briefing with public reading, sign-in, server-synced bookmarks and language preference, RSS ingestion, an OpenAI-compatible scheduled translation task, and an installable PWA.

> **Release status:** this is a hardened production **baseline**, not a live deployment, independent security audit, or penetration-test certification. It has no cloud account, domain, Cognito pool, RDS endpoint, email setup, or AI key attached. Production must remain disabled until the configuration and release gates in [`DEPLOYMENT.md`](DEPLOYMENT.md) are completed.

## Architecture choices

- Server-rendered Django 5.2 LTS. Browser code receives no cloud database or AI credentials and makes no direct API calls.
- AWS Cognito OIDC authorization-code sign-in with PKCE, RS256/JWKS signature verification, nonce, explicit issuer/audience/token-use checks, verified-email enforcement, bounded HTTPS calls, and no redirects/ambient proxies. Cognito holds credentials; Django stores only a provider subject identifier and uses opaque, server-side database sessions.
- PostgreSQL rows for accounts' bookmarks and language preferences; all reads and writes are filtered by the authenticated server-side user. Anonymous reading stays public.
- A one-off ingestion/translation task scheduled by AWS EventBridge. Only that task receives `OPENAI_API_KEY`; translation is OpenAI-compatible HTTPS only, with no public translation fallback.
- AWS hosting recommendation: CloudFront + WAF, private ECS/Fargate web and scheduled ingestion tasks, private encrypted RDS PostgreSQL, Cognito, Secrets Manager, and SES. This avoids an always-on queue/broker. See [`DEPLOYMENT.md`](DEPLOYMENT.md).
- Restrictive same-origin CSP, CSRF on state changes, HTTPS/HSTS, secure `HttpOnly` cookies, server-side session timeout, no-store HTML, escaped templates, safe external URL handling, hardened XML parsing, dependency hash lock, and non-root container.
- PWA offline support is deliberately limited to static shell assets. HTML, account pages, bookmarks, and other user-specific information are **never** cached offline.

## Local preview (Python / SQLite)

Requires Python 3.13 (the container base and hash lock are built and verified for this version).

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8000
```

Native development uses the safe local defaults in `config/settings.py` and local SQLite. This settings module does not implicitly load `.env`; export any custom values in your shell. Docker Compose reads `.env` as shown below. The preview uses synthetic stories marked **DEMO**. Cognito sign-in is intentionally unavailable until you configure a real user pool. Demo stories and sample credentials must never be used in production.

### Local Docker Compose (PostgreSQL)

```bash
cp .env.example .env
# Replace the local-only POSTGRES_PASSWORD and DJANGO_SECRET_KEY before sharing the machine.
docker compose up --build
```

Open `http://localhost:8000`. The Compose web container explicitly receives an empty `OPENAI_API_KEY`; the optional one-off `ingest-once` task is the only container that receives it. Never use these local passwords or services in production.

## Development and verification

```bash
python manage.py test tests -v 2
python manage.py check
python manage.py makemigrations --check --dry-run
python -m compileall -q config newsroom manage.py tests
bandit -r config newsroom
pip-audit -r requirements.lock
pip-audit -r requirements-dev.lock
```

`requirements.lock` pins runtime transitive packages and hashes; `requirements-dev.lock` adds pinned test/security tools; `requirements.txt` lists direct runtime dependencies. After changing direct dependencies, install the pinned lock tool (`python -m pip install pip-tools==7.6.2`), regenerate both locks with `pip-compile --generate-hashes --allow-unsafe --resolver=backtracking --strip-extras -o requirements.lock requirements.txt` and `pip-compile --generate-hashes --allow-unsafe --resolver=backtracking --strip-extras -o requirements-dev.lock requirements-dev.in`, review both diffs, run tests and vulnerability scanning, then commit the lock files.

## Project map

- `config/settings.py` — fail-closed production settings and cookie, database, OIDC and TLS controls.
- `newsroom/auth.py` — Cognito identity validation; enforces audience/issuer and matches the UserInfo subject to the signed ID token.
- `newsroom/views.py`, `newsroom/models.py` — public feed plus per-user bookmarks/preferences.
- `newsroom/ingestion.py` — allowlisted feed ingestion, bounded parsing, PostgreSQL advisory lock, prompt-injection precautions and server-side translation.
- `newsroom/static/newsroom/` — local Tajawal fonts, responsive interface, manifest and no-user-data service worker.
- `tests/` — authorization, CSRF, XSS escaping, OIDC claims, safe URLs and ingestion tests.
- `SECURITY.md`, `THREAT_MODEL.md`, `DEPLOYMENT.md` — controls, assumptions, limitations and launch checklist.

## Before any public launch

Use a user-owned AWS account and domain, configure Cognito MFA and verified email, provision the private managed services, enter secrets directly into Secrets Manager, verify every feed's URL and terms, test login and account isolation end to end, run the production `check --deploy`, dependency/image scans, backup-restore test, and independent security review. The app is not represented as audited or “unhackable.”
