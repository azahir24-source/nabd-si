# Production deployment plan — NABD SI

This is the recommended production topology, not an executed deployment. The repository contains no cloud credentials, domain, Cognito pool, managed database, or AI key. Keep the public account flow unavailable until the owner's AWS resources and release gates are configured.

## Recommended host and region

**AWS**, using managed identity, private Fargate tasks, RDS PostgreSQL, and a scheduled one-off ingestion task. For an initial footprint, evaluate **EU (Frankfurt), `eu-central-1`**; compare measured latency/cost and data-residency requirements with **Middle East (Bahrain), `me-south-1`** before provisioning. Confirm current service availability (ECS/Fargate, RDS PostgreSQL, Cognito, EventBridge Scheduler, WAF, SES) in the [AWS regional services matrix](https://aws.amazon.com/about-aws/global-infrastructure/regional-product-services/). AWS notes that some edge/global services may process data globally, so review residency requirements separately.

A managed platform can still be misconfigured. Use a dedicated NABD SI application domain and do not treat this architecture as a security audit.

## Target topology

```text
Readers ── HTTPS ──> CloudFront + AWS WAF ──> public ALB (TLS)
                                                     │
                                                     └── private ECS/Fargate web tasks
                                                            ├── Cognito hosted OIDC sign-in
                                                            └── private RDS PostgreSQL

EventBridge Scheduler (every 15 min)
        └── private ECS/Fargate one-off ingestion task
                ├── private RDS PostgreSQL
                ├── approved RSS/Atom publisher hosts
                └── HTTPS OpenAI-compatible endpoint
```

There is deliberately **no Redis, browser-side database client, public database endpoint, or always-on scheduler service**. The web task, scheduled ingestion task and one-off migration task use the same immutable image but distinct `APP_ROLE` values, IAM task roles and secret sets. The ingestion task is run with container command `python manage.py ingest_news`; PostgreSQL advisory locking prevents overlapping runs. Reading stays public, while bookmarks and language preference are stored against the authenticated server-side user.

### AWS services

- Route 53 and ACM for DNS and TLS; CloudFront for edge delivery; AWS WAF rate-based rules and managed protections.
- Application Load Balancer in public subnets; ECS/Fargate tasks in private subnets. Only the ALB should reach the web task. Only the scheduled task should have egress to the feeds/provider and database.
- RDS PostgreSQL in private subnets, encryption with KMS, TLS with certificate verification, automated backups/PITR and deletion protection. Use distinct database login roles and Secrets Manager entries for web, ingestion and migration; do not give the public web or ingestion role schema-owner/migration privileges.
- Cognito User Pool for managed credentials, verified email, MFA and hosted OIDC.
- Secrets Manager for Django, Cognito client and database credentials. The **ingestion task only** gets the OpenAI-compatible API key.
- SES for Cognito verification/transactional messages if using a verified custom domain.
- CloudTrail, GuardDuty, AWS Config/Security Hub, CloudWatch alarms, ECR image scanning and encrypted, retention-limited logs.

## Identity provider configuration

Create a Cognito User Pool in the chosen region and configure:

1. Email as the required, verified sign-in attribute. Do not allow unverified addresses to authenticate. Decide whether public self-registration is actually desired; use verified email and abuse protections if enabled.
2. Require a second factor for every account. Prefer TOTP/software authenticator; do not use SMS as the only factor. Apply a long password policy (at least 14 characters), compromised-password/risk controls where available, account recovery protections, and Cognito/WAF rate limits.
3. Create a **confidential server-side app client**. Enable OAuth authorization-code grant only, `openid email` scopes, and PKCE S256. Disable implicit grant and unused identity providers. Store its generated client secret in Secrets Manager.
4. Set the exact callback URL `https://<NABD-SI-domain>/oidc/callback/` and sign-out URL `https://<NABD-SI-domain>/`. Do not use wildcard callback/logout URLs.
5. Set `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_DOMAIN` (including `https://`), `COGNITO_CLIENT_ID`, and `COGNITO_CLIENT_SECRET` for the web task. Check the Cognito issuer matches `https://cognito-idp.<region>.amazonaws.com/<pool-id>`.

The application uses PKCE, nonce, HTTPS/JWKS signature verification, and its custom backend explicitly checks issuer, audience, Cognito `token_use=id`, time claims, verified email, and equality of the UserInfo `sub` with the signed ID-token subject. ID/access tokens are not persisted. Validate the complete login, sign-out, expired-session and MFA flows against the real pool before launch.

## Secrets and runtime configuration

Generate a new, random Django secret (at least 50 characters) and store it directly in Secrets Manager, for example with `openssl rand -base64 64`. Never copy production secrets into `.env`, Git, a Docker build argument, an image layer, a browser bundle, or a support ticket.

Required web-task configuration:

- `APP_ENV=production`, `APP_ROLE=web`, `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY` from Secrets Manager.
- `DJANGO_ALLOWED_HOSTS=<exact-host>`; no `*` or broad wildcards.
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://<exact-host>`.
- `DJANGO_HEALTHCHECK_CIDRS` set to the exact private ECS task subnet CIDRs from which the ALB health-check target IPs are allocated; never use `0.0.0.0/0`.
- `DATABASE_URL` for private RDS PostgreSQL, `DATABASE_SSLMODE=verify-full`, and `DATABASE_SSLROOTCERT` pointing to the current RDS CA bundle mounted in the image/task.
- Cognito variables listed above.
- `TRANSLATION_PROVIDER=openai`, `OPENAI_BASE_URL=https://...`, `OPENAI_MODEL=...`. The web task does not need `OPENAI_API_KEY`.

Set `APP_ENV=production`, `APP_ROLE=ingestion`, and `DJANGO_DEBUG=0` for the ingestion task. It needs the private database/TLS configuration, the safe OpenAI-compatible endpoint/model settings, and `OPENAI_API_KEY` from Secrets Manager; it does **not** need a Django web signing secret, public host/CSRF/health-check settings, or any Cognito client secret. Its Django signing key is generated ephemerally and never used for browser sessions. Use a task role limited to the exact database access and ingestion-key secret ARN; do not share the OpenAI secret with the web task, deployment pipeline, or monitoring service. Set `APP_ROLE=maintenance` for a one-off migration task; it needs the DB/TLS configuration but no web, Cognito or OpenAI secrets. The configured translation endpoint must be HTTPS; redirects and ambient proxy settings are rejected so the bearer key cannot be forwarded to another host. Provide direct controlled NAT/egress rather than injecting `HTTP_PROXY`/`HTTPS_PROXY` into these containers.

The production web role fails closed if the Django secret, TLS/CA verification, Cognito, exact host/CSRF allowlists or private health-check CIDRs are missing. The scheduled ingestion command also refuses to run in production without the OpenAI-compatible provider and task-only key.

## Network, TLS, caches and logging

- Use HTTPS from browser to CloudFront and CloudFront to ALB. Redirect HTTP to HTTPS. Configure the ALB listener to forward only the exact NABD SI Host header to the target group and return a fixed 404 for its default action; block public access to `/healthz` at WAF. Configure the ALB/security groups so direct Internet access to tasks is impossible; only the trusted ALB can reach port 8000.
- `SECURE_PROXY_SSL_HEADER` is set for the trusted ALB deployment. Verify the ALB overwrites forwarded-protocol headers and that no untrusted caller can reach the application directly. Django `ALLOWED_HOSTS` stays an exact hostname list. `StrictHostMiddleware` permits an IP-valued Host only for `GET /healthz` and only when that target IP is inside `DJANGO_HEALTHCHECK_CIDRS`; it rewrites that host to the configured app domain before Django processes it. The HTTP-to-HTTPS exemption is limited to this minimal readiness route because ALB target health checks use HTTP. Configure the ALB target group path as `/healthz` with success code `200`; never expose health details or secrets there.
- Production sends HSTS for one year with `includeSubDomains` and `preload`. **Use a dedicated domain and confirm every subdomain is HTTPS before enabling it.** If that cannot be guaranteed, do not deploy this exact HSTS policy until the domain plan is changed.
- CloudFront must not cache HTML or authenticated/session-bearing paths. Bypass cache for `/oidc/*`, `/account/*`, `/preferences/*`, and `/healthz`; honor the application's `Cache-Control: private, no-store`. Cache only static assets. Do not forward unnecessary cookies or query parameters.
- The OIDC callback uses query parameters for the short-lived authorization code and `state`. Gunicorn is intentionally configured to log the path without the query string. Do not enable ALB/CloudFront/WAF request logging that records callback query strings, authorization headers or session cookies. Omit CloudFront `cs-uri-query`; configure WAF log data protection/redaction. If a logging service cannot selectively redact, disable request-line logging for this endpoint.
- Retain security logs only as long as operationally necessary, restrict access, encrypt with KMS, and alert on abnormal sign-in failures, WAF blocks, task-role changes and secret access. Never log feed text, prompts, provider responses or secret values.
- Restrict outbound traffic from the ingestion task to reviewed RSS/Atom publisher domains, DNS, the configured translation endpoint, and required AWS endpoints/NAT. Source feed URLs live in `newsroom/sources.json` and require code review; user input never controls server-side fetch destinations. Review each publisher's availability and reuse terms before enabling it.

## Database, migration and scheduled ingestion

1. Use private RDS PostgreSQL, encryption at rest, TLS `verify-full`, backups/PITR, deletion protection and a tested restore procedure. Use separate database users/`DATABASE_URL` secrets: web needs read access to articles/sources plus the minimum `auth_user`, `django_session`, bookmark and preference operations; ingestion needs only source/article/ingestion-run writes; a one-off migration role owns schema changes. Grant required table and sequence permissions explicitly, test each role, and never give web/ingestion schema-owner privileges. No public RDS access.
2. Build one immutable image and scan/sign it. Run schema migrations as a reviewed, one-off ECS deployment task using `APP_ROLE=maintenance python manage.py migrate --noinput`; do not run migrations independently in every web replica.
3. Create an EventBridge Scheduler schedule every 15 minutes, no flexible window, targeting `ecs:RunTask` in private subnets with the ingestion task definition (`APP_ROLE=ingestion`) and command `python manage.py ingest_news`. Use a dedicated execution role scoped to that task definition, cluster, subnets/security group and `iam:PassRole` for only the exact ECS task/execution roles. Set a bounded retry policy, timeout/age policy and alarm on task failure.
4. The ingestion task has a PostgreSQL advisory lock, bounded feed/body/time limits and idempotent URL upserts. Keep task concurrency at one operationally as well; the lock is the second line of defense.
5. Confirm the production worker can read the RDS CA bundle and OpenAI secret, while the web task cannot. Run a scheduled-task smoke test with real provider credentials before publishing the app.

## Static files and image build

The Docker image installs only hash-locked runtime packages, collects manifest-hashed static files, runs as a non-root user, and logs only the path (not query string). Its health check queries `/healthz` only for `APP_ROLE=web`; one-off ingestion/migration tasks do not run an HTTP server. The Dockerfile accepts `PYTHON_IMAGE`; the release pipeline should set it to an approved `python:3.13-slim-bookworm@sha256:<verified-digest>` instead of relying on the default tag. The build context excludes `.env`, virtual environments and local databases through `.dockerignore`. Rebuild regularly for OS security fixes. Use a read-only filesystem where the ECS task permits it; only `/tmp` should be writable.

The RDS CA bundle in `certs/rds-global-bundle.pem` was fetched from AWS's official `https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem` endpoint; its recorded SHA-256 is `fe45bbebf92ad3e27a583bbb2ddd1553c521ed4d49af5514dc0a40372ea5395c`. Re-verify against the official AWS truststore and rotation guidance before image releases; the CA bundle is public trust material, not a secret.

## Release gate / operational checklist

- [ ] Separate AWS staging and production environments/accounts; least-privilege IAM; MFA for administrators; no root access keys.
- [ ] Dedicated domain, DNS, ACM certificates, WAF rules, no-store/cache behaviors and callback-query log redaction verified.
- [ ] Cognito MFA, verified email, exact callback/logout URLs, PKCE, sign-up and account recovery tested.
- [ ] RDS is private, encrypted, TLS `verify-full`, backups enabled, restore test passed; database role is least privilege.
- [ ] ECS web role lacks `OPENAI_API_KEY`; ingestion task role can read only its required Secrets Manager entries.
- [ ] Feed hosts, licensing/usage terms and category mappings reviewed; provisional AITNews source stays disabled until verified.
- [ ] `python manage.py check --deploy` has no unresolved warnings for the actual production configuration.
- [ ] Tests, `pip-audit -r requirements.lock`, `pip-audit -r requirements-dev.lock`, Bandit, image scanning and SBOM generation pass.
- [ ] Public/anonymous read, user-to-user bookmark isolation, Arabic preference persistence, CSRF, OIDC wrong-issuer/audience rejection, logout, session expiry and PWA no-user-data caching verified.
- [ ] Backup restoration, secret rotation, rollback and incident-response procedures exercised.
- [ ] Independent application security review and penetration test completed before marketing the service as production-ready.

**Do not represent this code or deployment plan as independently audited, certified, or immune to compromise.**
