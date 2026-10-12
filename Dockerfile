# Production build: immutable application image, non-root runtime, no credentials in layers.
ARG PYTHON_IMAGE=python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV APP_ENV=production \
    APP_ROLE=web \
    DJANGO_DEBUG=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --create-home app

WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock
COPY --chown=app:app . .
COPY certs/rds-global-bundle.pem /etc/ssl/certs/rds-global-bundle.pem

# Build a manifest without production secrets; APP_ENV=build selects the same static backend.
RUN APP_ENV=build DJANGO_DEBUG=0 python manage.py collectstatic --noinput \
    && chown -R app:app /app/staticfiles

USER app:app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --start-period=30s --retries=3 \
  CMD if [ "$APP_ROLE" = "web" ]; then python -c "import os,http.client; c=http.client.HTTPConnection('127.0.0.1',8000,timeout=3); c.request('GET','/healthz',headers={'Host':os.environ['DJANGO_ALLOWED_HOSTS'].split(',')[0].strip(),'X-Forwarded-Proto':'https'}); r=c.getresponse(); r.read(); raise SystemExit(0 if r.status==200 else 1)"; else exit 0; fi

CMD ["gunicorn", "config.wsgi:application", "--bind=0.0.0.0:8000", "--workers=3", "--threads=2", "--timeout=30", "--graceful-timeout=30", "--access-logfile=-", "--access-logformat=%(h)s %(t)s %(m)s %(U)s %(s)s %(L)s", "--error-logfile=-"]
