"""Hardened Cognito OIDC backend with bounded, non-redirecting HTTPS requests."""
import json
import time
from urllib.parse import urlsplit

import jwt
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousOperation
from requests.auth import HTTPBasicAuth
from mozilla_django_oidc.auth import OIDCAuthenticationBackend


class CognitoOIDCBackend(OIDCAuthenticationBackend):
    """Create only verified Cognito identities and store no email or provider tokens.

    The upstream library verifies signatures/nonces but disables JWT audience checks and
    follows redirects on OIDC HTTP calls. This backend adds explicit issuer/audience/token
    checks and uses bounded requests with redirects and ambient proxies disabled.
    """

    MAX_RESPONSE_BYTES = 1_000_000

    @staticmethod
    def _require_https_host(url: str, expected_host: str, expected_path: str) -> None:
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError:
            raise SuspiciousOperation("Untrusted OIDC endpoint configuration")
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.lower() != expected_host.lower()
            or parsed.username
            or parsed.password
            or port not in {None, 443}
            or parsed.path != expected_path
            or parsed.query
            or parsed.fragment
        ):
            raise SuspiciousOperation("Untrusted OIDC endpoint configuration")

    @classmethod
    def _response_json(cls, response):
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > cls.MAX_RESPONSE_BYTES:
                raise SuspiciousOperation("OIDC provider response exceeded the size limit")
            chunks.append(chunk)
        try:
            result = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SuspiciousOperation("OIDC provider returned an invalid JSON response")
        if not isinstance(result, dict):
            raise SuspiciousOperation("OIDC provider returned an unexpected response")
        return result

    @staticmethod
    def _request_session():
        session = requests.Session()
        session.trust_env = False  # Do not send OIDC credentials through ambient proxy/netrc settings.
        return session

    def get_token(self, payload):
        endpoint = settings.OIDC_OP_TOKEN_ENDPOINT
        expected_host = urlsplit(settings.COGNITO_DOMAIN).hostname or ""
        self._require_https_host(endpoint, expected_host, "/oauth2/token")
        token_payload = dict(payload)
        auth = None
        if settings.OIDC_TOKEN_USE_BASIC_AUTH:
            client_id = token_payload.get("client_id", "")
            client_secret = token_payload.pop("client_secret", "")
            auth = HTTPBasicAuth(client_id, client_secret)

        session = self._request_session()
        try:
            response = session.post(
                endpoint,
                data=token_payload,
                auth=auth,
                verify=True,
                timeout=settings.OIDC_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            try:
                if response.status_code != 200:
                    raise SuspiciousOperation(f"OIDC token endpoint returned HTTP {response.status_code}")
                return self._response_json(response)
            finally:
                response.close()
        finally:
            session.close()

    def get_userinfo(self, access_token, id_token, payload):
        if not isinstance(access_token, str) or not access_token or len(access_token) > 32_768:
            raise SuspiciousOperation("OIDC access token is missing or malformed")
        endpoint = settings.OIDC_OP_USER_ENDPOINT
        expected_host = urlsplit(settings.COGNITO_DOMAIN).hostname or ""
        self._require_https_host(endpoint, expected_host, "/oauth2/userInfo")
        session = self._request_session()
        try:
            response = session.get(
                endpoint,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                verify=True,
                timeout=settings.OIDC_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            try:
                if response.status_code != 200:
                    raise SuspiciousOperation(f"OIDC userinfo endpoint returned HTTP {response.status_code}")
                if not response.headers.get("content-type", "").lower().startswith("application/json"):
                    raise SuspiciousOperation("OIDC userinfo must be returned as JSON")
                userinfo = self._response_json(response)
            finally:
                response.close()
        finally:
            session.close()
        if userinfo.get("sub") != payload.get("sub"):
            raise SuspiciousOperation("OIDC userinfo subject does not match the signed ID token")
        return userinfo

    def retrieve_matching_jwk(self, token):
        endpoint = settings.OIDC_OP_JWKS_ENDPOINT
        expected_host = f"cognito-idp.{settings.COGNITO_REGION}.amazonaws.com"
        self._require_https_host(endpoint, expected_host, f"/{settings.COGNITO_USER_POOL_ID}/.well-known/jwks.json")
        session = self._request_session()
        try:
            response = session.get(endpoint, headers={"Accept": "application/json"}, verify=True,
                                   timeout=settings.OIDC_TIMEOUT, allow_redirects=False, stream=True)
            try:
                if response.status_code != 200:
                    raise SuspiciousOperation(f"OIDC JWKS endpoint returned HTTP {response.status_code}")
                jwks = self._response_json(response)
            finally:
                response.close()
        finally:
            session.close()

        header = jwt.get_unverified_header(token)
        token_kid = header.get("kid")
        token_alg = header.get("alg")
        if not token_kid or token_alg != settings.OIDC_RP_SIGN_ALGO:
            raise SuspiciousOperation("OIDC signing key or algorithm is invalid")
        for jwk in jwks.get("keys", []):
            if not isinstance(jwk, dict) or jwk.get("kid") != token_kid:
                continue
            if jwk.get("alg") and jwk["alg"] != token_alg:
                continue
            try:
                return jwt.PyJWK(jwk)
            except jwt.PyJWTError:
                break
        raise SuspiciousOperation("No matching trusted OIDC signing key")

    def verify_token(self, token, **kwargs):
        if not isinstance(token, str) or len(token) > 32_768 or token.count(".") != 2:
            raise SuspiciousOperation("OIDC ID token is missing or malformed")
        claims = super().verify_token(token, **kwargs)
        if not isinstance(claims, dict):
            raise SuspiciousOperation("Invalid OIDC ID token payload")

        client_id = settings.COGNITO_CLIENT_ID
        aud = claims.get("aud")
        if isinstance(aud, str):
            audience_ok = aud == client_id
        elif isinstance(aud, list):
            audience_ok = client_id in aud and (len(aud) == 1 or claims.get("azp") == client_id)
        else:
            audience_ok = False

        now = int(time.time())
        try:
            issued_at = int(claims["iat"])
            expires_at = int(claims["exp"])
        except (KeyError, TypeError, ValueError):
            raise SuspiciousOperation("OIDC ID token is missing valid time claims")

        if claims.get("iss") != settings.COGNITO_ISSUER:
            raise SuspiciousOperation("OIDC issuer mismatch")
        if not audience_ok:
            raise SuspiciousOperation("OIDC audience mismatch")
        if claims.get("token_use") != "id":
            raise SuspiciousOperation("Cognito token is not an ID token")
        if not claims.get("sub") or issued_at > now + 60 or expires_at <= now or expires_at <= issued_at:
            raise SuspiciousOperation("OIDC ID token subject or time claims are invalid")
        return claims

    def verify_claims(self, claims):
        verified = claims.get("email_verified") is True or str(claims.get("email_verified", "")).lower() == "true"
        return bool(claims.get("sub")) and verified and super().verify_claims(claims)

    @staticmethod
    def _username(claims):
        subject = str(claims.get("sub", ""))
        if not subject or len(subject) > 140:
            return ""
        return f"cognito_{subject}"

    def filter_users_by_claims(self, claims):
        username = self._username(claims)
        User = get_user_model()
        return User.objects.filter(username=username) if username else User.objects.none()

    def create_user(self, claims):
        username = self._username(claims)
        if not username:
            raise SuspiciousOperation("OIDC subject is not a valid user identifier")
        User = get_user_model()
        user = User(username=username, email="", is_active=True, is_staff=False, is_superuser=False)
        user.set_unusable_password()
        user.save()
        return user

    def update_user(self, user, claims):
        # Keep the local profile minimal; identity remains anchored to Cognito's immutable sub.
        user.email = ""
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.set_unusable_password()
        user.save(update_fields=["email", "is_active", "is_staff", "is_superuser", "password"])
        return user
