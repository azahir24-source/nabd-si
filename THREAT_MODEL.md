# NABD SI threat model

**Target:** public bilingual news/PWA with optional authenticated bookmarks and a server-side language preference. **Status:** design and code baseline; deployment controls are external and must be verified.

## Assets

- Cognito authentication state and Django session IDs.
- Per-user bookmark list and language setting.
- Django secret, Cognito confidential-client secret, RDS credentials and ingestion-only OpenAI-compatible key.
- Newsfeed integrity, source attribution, safe outbound links and translation quality.
- Availability of the public feed and ingestion schedule.

## Trust boundaries

1. Browser ↔ CloudFront/WAF/ALB ↔ Django web task.
2. Django web task ↔ Cognito OIDC over HTTPS.
3. Web/ingestion/migration tasks ↔ private RDS PostgreSQL with role-specific logins.
4. Scheduled ingestion task ↔ fixed RSS/Atom publishers and the selected translation API.
5. AWS control plane, IAM roles, Secrets Manager, build pipeline and logging system.

## Principal abuse cases and mitigations

| Abuse case | Main controls | Remaining operator action |
|---|---|---|
| Read or change another account's bookmarks/preferences | No browser DB access; server-side sessions; each bookmark query/write is bound to `request.user`; unique DB constraint; POST + CSRF | Test cross-account behavior with real Cognito users and penetration testing |
| Host-header poisoning, open redirects or untrusted OIDC callback | Exact production host and CSRF allowlists; custom host validation; safe same-host preference redirect; fixed Cognito endpoints and exact callback URLs | Configure ALB exact-host routing/default 404, CloudFront forwarding, WAF health-path blocking and security groups exactly |
| OIDC token substitution / confused-deputy login | Authorization code + PKCE S256, state, nonce, RS256/JWKS, issuer/audience/token-use/time checks, UserInfo subject binding, verified email | Configure Cognito MFA/email verification; test key rotation and expired/replayed code cases |
| CSRF, XSS, clickjacking or credential leakage | CSRF-protected POSTs; template escaping; strict CSP/no inline JS; `X-Frame-Options`; `Referrer-Policy`; `HttpOnly`/`Secure` cookies; no external analytics/CDN | Keep cache/logging rules and client-side scripts reviewed |
| Authorization code/session copied from logs | Gunicorn access format excludes query; browser response is no-store; no PWA HTML cache | Disable/redact callback query strings in ALB, CloudFront and WAF logs; verify log sink end to end |
| Stored or reflected content injection through RSS/LLM | HTML stripped from excerpts, Django autoescape, scheme-filtered external links, strict output bounds; no LLM tools | Treat all publisher content and model output as untrusted; review sources and update parser |
| SSRF or internal service probing via RSS URL | Feed URLs come from reviewed static JSON, HTTPS/standard-port only, no redirects or ambient proxies; no user-supplied fetch target | Enforce VPC egress/DNS restrictions; review every source configuration change |
| XML entity expansion or oversized feed denial of service | `defusedxml`, 2 MiB body cap, bounded per-source items, HTTP timeout, capped thread pool | Monitor ingestion durations and task resource use |
| Prompt injection or AI key exfiltration | Source text labeled untrusted, no tools, JSON-only bounded output; key only in scheduled task; HTTPS, no redirects or ambient proxy, no raw prompts in logs | Provider still processes public article excerpts; review the provider's data terms and endpoint owner |
| Abuse, credential stuffing, cost exhaustion | Cognito risk/MFA features, WAF rate rules, bounded ingestion and source limits, scheduled cadence | Configure per-path WAF/Cognito limits, alarms, budgets and incident response |
| Compromise of cloud or CI credentials | Recommended separate roles, secret scoping, image scanning and private services | Implement IAM/CI OIDC least privilege, MFA, ECR signing/scanning, CloudTrail/GuardDuty and key rotation |
| Offline device leakage | Service worker caches only static shell; navigation and HTML are network-only; offline page reveals no user content | Retest after any PWA/service-worker change and clear old caches on release |

## Assumptions

- Production web/ingestion tasks are private and reachable only through the intended AWS network path.
- Cognito verifies the user's email and enforces MFA as configured; the app refuses unverified identity claims.
- The web role receives `DJANGO_SECRET_KEY`, RDS credentials and Cognito secret; ingestion receives its own RDS credentials plus the OpenAI-compatible key; maintenance receives only migration DB credentials. Secrets come from owner-controlled Secrets Manager roles. Only ingestion receives the AI key.
- Feed source URLs are reviewed by maintainers and are not user-controllable. Outbound network restrictions are enforced outside this code.
- Dynamic page caching is disabled at the edge; query strings and credentials are not written to logs.

If any assumption is false, the risk profile changes and deployment must stop until the architecture is re-reviewed.
