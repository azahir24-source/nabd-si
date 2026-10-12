import uuid
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, SimpleTestCase
from django.urls import reverse
from newsroom.ingestion import safe_public_url
from newsroom.models import Article, Bookmark, Source, UserPreference


class URLPolicyTests(SimpleTestCase):
    def test_external_article_url_is_canonicalized(self):
        result = safe_public_url("https://Example.org/story?utm_source=feed&id=7#details")
        self.assertEqual(result, "https://example.org/story?id=7")

    def test_active_and_private_urls_are_rejected(self):
        for value in (
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "https://user:pass@example.org/story",
            "http://127.0.0.1/admin",
            "https://localhost/private",
        ):
            with self.subTest(value=value):
                self.assertEqual(safe_public_url(value), "")

    def test_feed_urls_are_restricted_to_standard_https_port(self):
        self.assertEqual(safe_public_url("https://example.org:8443/feed.xml", require_https=True), "")


class NewsroomViewTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug="test-source", name="Test Publisher", feed_url="https://example.org/rss.xml",
            category="mixed", language="en", enabled=True,
        )
        self.article = Article.objects.create(
            source=self.source,
            url="https://example.org/story/one",
            original_language="en",
            title_en="A clearly labelled demo headline",
            title_ar="عنوان توضيحي باللغة العربية",
            summary_en="An English demo summary.",
            summary_ar="ملخص تجريبي باللغة العربية.",
            category=Article.Category.AI,
            published_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
            is_demo=True,
        )

    def test_public_read_is_available_without_an_account(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A clearly labelled demo headline")
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Cache-Control"], "private, no-store, max-age=0")

    def test_arabic_preference_survives_page_refresh_and_uses_arabic_fields(self):
        response = self.client.post(reverse("set_language"), {"language": "ar", "next": "/"})
        self.assertEqual(response.status_code, 302)
        page = self.client.get(reverse("home"))
        self.assertContains(page, "عنوان توضيحي باللغة العربية")
        self.assertNotContains(page, "A clearly labelled demo headline")
        self.assertEqual(page.context["ui_language"], "ar")

    def test_private_bookmarks_are_synced_but_isolated_per_user(self):
        User = get_user_model()
        owner = User.objects.create_user(username=f"cognito_{uuid.uuid4()}")
        other = User.objects.create_user(username=f"cognito_{uuid.uuid4()}")
        Bookmark.objects.create(user=owner, article=self.article)

        self.client.force_login(owner)
        response = self.client.get(reverse("bookmarks"))
        self.assertContains(response, "A clearly labelled demo headline")
        self.client.force_login(other)
        response = self.client.get(reverse("bookmarks"))
        self.assertContains(response, "No saved stories yet")
        self.assertNotContains(response, "A clearly labelled demo headline")

    def test_bookmark_mutations_require_authentication_and_post(self):
        url = reverse("toggle_bookmark", args=[self.article.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("sign_in"), response["Location"])
        user = get_user_model().objects.create_user(username=f"cognito_{uuid.uuid4()}")
        self.client.force_login(user)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Bookmark.objects.filter(user=user, article=self.article).exists())
        self.client.post(url)
        self.assertFalse(Bookmark.objects.filter(user=user, article=self.article).exists())

    def test_authenticated_language_is_saved_server_side(self):
        user = get_user_model().objects.create_user(username=f"cognito_{uuid.uuid4()}")
        self.client.force_login(user)
        self.client.post(reverse("set_language"), {"language": "ar", "next": "/"})
        self.assertEqual(UserPreference.objects.get(user=user).language, "ar")
        self.assertContains(self.client.get(reverse("home")), "عنوان توضيحي باللغة العربية")

    def test_titles_are_escaped_and_external_links_are_safe(self):
        self.article.title_en = "<script>alert('xss')</script>"
        self.article.save(update_fields=["title_en"])
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "<script>alert('xss')</script>")
        self.assertContains(response, "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", html=False)
        self.assertContains(response, 'rel="noopener noreferrer"')

    def test_account_page_is_honest_when_identity_provider_is_unconfigured(self):
        response = self.client.get(reverse("sign_in"))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Account sign-in is not configured", status_code=503)
        self.assertEqual(self.client.post(reverse("sign_in")).status_code, 405)


class CsrfEnforcementTests(TestCase):
    def test_language_preference_post_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("set_language"), {"language": "ar", "next": "/"})
        self.assertEqual(response.status_code, 403)


class OIDCTokenPolicyTests(SimpleTestCase):
    def setUp(self):
        from django.conf import settings
        self.client_id = settings.COGNITO_CLIENT_ID
        self.issuer = settings.COGNITO_ISSUER
        from django.test import override_settings
        self.settings_override = override_settings(
            COGNITO_CLIENT_ID="sample-client",
            COGNITO_ISSUER="https://cognito-idp.eu-central-1.amazonaws.com/eu-central-1_sample",
        )
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()

    def _claims(self, **overrides):
        import time
        claims = {
            "iss": "https://cognito-idp.eu-central-1.amazonaws.com/eu-central-1_sample",
            "aud": "sample-client",
            "token_use": "id",
            "sub": "provider-subject",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": "test-nonce",
        }
        claims.update(overrides)
        return claims

    def test_token_requires_expected_issuer_audience_and_id_token_use(self):
        from unittest.mock import patch
        from django.core.exceptions import SuspiciousOperation
        from mozilla_django_oidc.auth import OIDCAuthenticationBackend
        from newsroom.auth import CognitoOIDCBackend

        backend = CognitoOIDCBackend()
        with patch.object(OIDCAuthenticationBackend, "verify_token", return_value=self._claims()):
            self.assertEqual(backend.verify_token("header.payload.signature", nonce="test-nonce")["sub"], "provider-subject")
        for override in ({"aud": "another-client"}, {"iss": "https://attacker.example"}, {"token_use": "access"}):
            with self.subTest(override=override), patch.object(OIDCAuthenticationBackend, "verify_token", return_value=self._claims(**override)):
                with self.assertRaises(SuspiciousOperation):
                    backend.verify_token("header.payload.signature", nonce="test-nonce")

    def test_unverified_email_claim_is_rejected(self):
        from newsroom.auth import CognitoOIDCBackend
        backend = CognitoOIDCBackend()
        self.assertFalse(backend.verify_claims({"sub": "abc", "email": "a@example.org", "email_verified": False}))
        self.assertTrue(backend.verify_claims({"sub": "abc", "email": "a@example.org", "email_verified": True}))


class StrictHostMiddlewareTests(TestCase):
    def test_only_private_target_ip_on_health_endpoint_is_accepted(self):
        from django.test import override_settings
        url = reverse("healthz")
        with override_settings(ALLOWED_HOSTS=["nabdsi.example"], IS_PRODUCTION=True, DJANGO_HEALTHCHECK_CIDRS=["10.0.0.0/16"]):
            response = self.client.get(url, HTTP_HOST="10.0.5.11:8000")
            self.assertEqual(response.status_code, 200)
            denied = self.client.get(reverse("home"), HTTP_HOST="10.0.5.11:8000")
            self.assertEqual(denied.status_code, 400)
            outside = self.client.get(url, HTTP_HOST="192.168.4.2:8000")
            self.assertEqual(outside.status_code, 400)


class PwaCacheTests(TestCase):
    def test_service_worker_is_scoped_and_has_no_unsubstituted_template_values(self):
        response = self.client.get(reverse("service_worker"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertIn("nabd-si-static-", response.content.decode("utf-8"))
        self.assertNotIn("__STATIC_ASSETS__", response.content.decode("utf-8"))
        self.assertNotIn("__CACHE_NAME__", response.content.decode("utf-8"))
        self.assertIn("Never cache HTML", response.content.decode("utf-8"))
        self.assertIn("/static/newsroom/icon-192.png", response.content.decode("utf-8"))
        self.assertIn("/static/newsroom/icon-512.png", response.content.decode("utf-8"))


class OIDCHttpPolicyTests(SimpleTestCase):
    def setUp(self):
        from django.test import override_settings
        self.override = override_settings(
            COGNITO_DOMAIN="https://auth.example.org",
            COGNITO_REGION="eu-central-1",
            COGNITO_USER_POOL_ID="eu-central-1_example",
            OIDC_OP_TOKEN_ENDPOINT="https://auth.example.org/oauth2/token",
            OIDC_OP_USER_ENDPOINT="https://auth.example.org/oauth2/userInfo",
            OIDC_OP_JWKS_ENDPOINT="https://cognito-idp.eu-central-1.amazonaws.com/eu-central-1_example/.well-known/jwks.json",
            OIDC_TIMEOUT=10,
            OIDC_TOKEN_USE_BASIC_AUTH=False,
        )
        self.override.enable()

    def tearDown(self):
        self.override.disable()

    def test_token_request_rejects_redirects_and_ambient_proxy_settings(self):
        from unittest.mock import Mock, patch
        from django.core.exceptions import SuspiciousOperation
        from newsroom.auth import CognitoOIDCBackend

        session = Mock()
        session.post.return_value.status_code = 302
        backend = CognitoOIDCBackend()
        with patch("newsroom.auth.requests.Session", return_value=session):
            with self.assertRaises(SuspiciousOperation):
                backend.get_token({"client_id": "client", "client_secret": "secret"})
        self.assertFalse(session.trust_env)
        self.assertFalse(session.post.call_args.kwargs["allow_redirects"])
        self.assertTrue(session.post.call_args.kwargs["verify"])
        session.post.return_value.close.assert_called_once()

    def test_userinfo_subject_binding_uses_bounded_https_json_response(self):
        from unittest.mock import Mock, patch
        from django.core.exceptions import SuspiciousOperation
        from newsroom.auth import CognitoOIDCBackend

        response = Mock(status_code=200, headers={"content-type": "application/json"})
        response.iter_content.return_value = [b'{"sub":"different-subject"}']
        session = Mock()
        session.get.return_value = response
        backend = CognitoOIDCBackend()
        with patch("newsroom.auth.requests.Session", return_value=session):
            with self.assertRaises(SuspiciousOperation):
                backend.get_userinfo("access-token", "id-token", {"sub": "expected-subject"})
        self.assertFalse(session.trust_env)
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        self.assertEqual(session.get.call_args.kwargs["headers"]["Authorization"], "Bearer access-token")


class ProductionConfigurationTests(TestCase):
    def _production_env(self):
        import os
        from pathlib import Path
        env = os.environ.copy()
        env.update({
            "APP_ENV": "production",
            "APP_ROLE": "web",
            "DJANGO_DEBUG": "0",
            "DJANGO_SECRET_KEY": "x" * 64,
            "DJANGO_ALLOWED_HOSTS": "nabdsi.example",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://nabdsi.example",
            "DJANGO_HEALTHCHECK_CIDRS": "10.0.0.0/16",
            "DATABASE_URL": "postgresql://nabdsi:example@db.internal:5432/nabdsi",
            "DATABASE_SSLMODE": "verify-full",
            "DATABASE_SSLROOTCERT": str(Path(__file__).resolve().parents[1] / "certs" / "rds-global-bundle.pem"),
            "COGNITO_REGION": "eu-central-1",
            "COGNITO_USER_POOL_ID": "eu-central-1_example",
            "COGNITO_DOMAIN": "https://auth.example.org",
            "COGNITO_CLIENT_ID": "sample-client",
            "COGNITO_CLIENT_SECRET": "sample-secret",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_MODEL": "gpt-4o-mini",
            "TRANSLATION_PROVIDER": "openai",
        })
        return env

    def _check(self, env):
        import subprocess
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [sys.executable, "manage.py", "check"], cwd=root, env=env,
            capture_output=True, text=True, timeout=15,
        )

    def test_production_rejects_missing_rds_ca_bundle(self):
        env = self._production_env()
        env["DATABASE_SSLROOTCERT"] = "/not-mounted/rds-bundle.pem"
        result = self._check(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_SSLROOTCERT", result.stderr)

    def test_production_rejects_missing_exact_host_allowlist(self):
        env = self._production_env()
        env.pop("DJANGO_ALLOWED_HOSTS")
        result = self._check(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_ALLOWED_HOSTS", result.stderr)

    def test_production_rejects_catch_all_health_check_cidr(self):
        env = self._production_env()
        env["DJANGO_HEALTHCHECK_CIDRS"] = "0.0.0.0/0"
        result = self._check(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("catch-all", result.stderr)

    def test_non_web_roles_do_not_require_web_or_cognito_secrets(self):
        for role in ("ingestion", "maintenance"):
            with self.subTest(role=role):
                env = self._production_env()
                env["APP_ROLE"] = role
                for name in (
                    "DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "DJANGO_CSRF_TRUSTED_ORIGINS",
                    "DJANGO_HEALTHCHECK_CIDRS", "COGNITO_USER_POOL_ID", "COGNITO_DOMAIN",
                    "COGNITO_CLIENT_ID", "COGNITO_CLIENT_SECRET",
                ):
                    env.pop(name, None)
                result = self._check(env)
                self.assertEqual(result.returncode, 0, result.stderr)
                import subprocess
                import sys
                from pathlib import Path
                wsgi = subprocess.run(
                    [sys.executable, "-c", "import config.wsgi"],
                    cwd=Path(__file__).resolve().parents[1], env=env,
                    capture_output=True, text=True, timeout=15,
                )
                self.assertNotEqual(wsgi.returncode, 0)
                self.assertIn("Only APP_ROLE=web", wsgi.stderr)

    def test_production_https_redirect_is_exempt_only_for_health_route(self):
        from django.test import override_settings
        from django.urls import reverse

        with override_settings(
            ALLOWED_HOSTS=["nabdsi.example"],
            IS_PRODUCTION=True,
            DJANGO_HEALTHCHECK_CIDRS=["10.0.0.0/16"],
            SECURE_SSL_REDIRECT=True,
            SECURE_REDIRECT_EXEMPT=[r"^healthz$"],
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ):
            health = self.client.get(reverse("healthz"), HTTP_HOST="nabdsi.example")
            self.assertEqual(health.status_code, 200)
            page = self.client.get(reverse("home"), HTTP_HOST="nabdsi.example")
            self.assertEqual(page.status_code, 301)
            self.assertTrue(page["Location"].startswith("https://nabdsi.example/"))
