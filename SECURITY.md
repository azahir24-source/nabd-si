# Security posture and vulnerability reporting

NABD SI is a security-sensitive news reader. The repository implements a set of hardening controls, but **has not been independently audited, penetration-tested, certified, or deployed to an owner-managed environment**. Do not describe it as “unhackable,” audited, or compliant without separate evidence.

## Implemented controls

- Server-rendered Django 5.2 LTS with templates auto-escaping content. No browser-side database client, user token, service-role key, or AI key.
- Cognito hosted OIDC authorization-code flow with PKCE S256 and nonce. The custom backend enforces RS256/JWKS signature validation, exact issuer, client audience, Cognito `token_use=id`, time claims, verified email, and matching UserInfo subject. OIDC calls are bounded HTTPS with redirects and ambient proxies disabled. Provider ID/access tokens are not saved.
- Server-side database sessions; production session cookie is `Secure`, `HttpOnly`, `SameSite=Lax`, `__Host-` prefixed; 30-minute idle and 8-hour absolute timeout. CSRF tokens live in the session and all mutations use POST.
- Server-side authorization and per-user bookmark/preference filtering. Public pages are read-only; no public write API.
- Restrictive self-hosted CSP (no inline script/style), anti-framing, `nosniff`, same-origin policies, no-store dynamic pages, HTTPS/HSTS and exact host/CSRF allowlists.
- `StrictHostMiddleware` validates every Host header against the configured list; only the health endpoint accepts private target-IP Host headers, restricted to explicit health-check CIDRs.
- No data-bearing PWA offline cache; only static shell resources are cached.
- RSS size/time caps, reviewed static source configuration, HTTPS-only fetches on standard ports, no redirects or ambient proxies, hardened XML parsing, sanitized link schemes, prompt-injection-aware LLM instructions, strict output shape/length, HTML escaping and no AI tools.
- OpenAI-compatible credentials exist only in the scheduled ingestion task. The ingestion role does not need Cognito client or Django web signing secrets. Translation endpoint must be HTTPS; redirects and ambient proxies are rejected. No MyMemory/public translation fallback.
- Runtime dependencies are version-pinned and hash-locked. Container runs as non-root and excludes local secrets/databases from build context. Gunicorn logs paths without OIDC query strings.

## Security operations required before production

- Provision and review AWS IAM, VPC/security groups, CloudFront/WAF, Cognito MFA/sign-up/recovery policy, RDS encryption/TLS/backups, Secrets Manager scopes/rotation, EventBridge schedule role, log retention, alerting and incident response. These resources are not provisioned in this workspace.
- Configure WAF/Cognito rate limits and abuse detection; application code alone does not provide a comprehensive IP reputation or bot defense layer.
- Keep Django and every locked dependency, Python base image, OS packages, RDS CA bundle and AWS services patched. `requirements.lock` is a point-in-time lock, not a promise that versions remain safe indefinitely.
- Review RSS publishers and terms; disable unavailable or unverified sources. Restrict scheduled-task egress at the VPC/NAT/egress firewall; this build intentionally does not use ambient HTTP proxy settings.
- Test account recovery, MFA, sign-out, session revocation, data deletion/retention, backup restore and secret rotation with the real services.
- Run dependency and container scans, SAST/DAST, independent application security review and penetration testing. Resolve all findings before launch.

## Vulnerability reporting

Before public release, the operator must publish a monitored security contact at the NABD SI domain and define a response SLA. Until then, report findings privately to the project owner through the channel used to deliver this package; avoid sending exploit details or real credentials in public issues. Do not test against a live system without written authorization.

## Scope limitations

The code is a carefully hardened baseline, not a formal OWASP ASVS Level 2 verification. No IaC, IAM policy, AWS account, account deletion workflow, legal/privacy policy, incident-response service, WAF ruleset or third-party audit is included. The external identity, cloud and translation provider controls must be configured and independently verified by the deployer.
