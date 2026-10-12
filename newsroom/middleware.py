from django.conf import settings
from django.conf import settings
from django.contrib.auth import logout
from django.utils import timezone


class SessionTimeoutMiddleware:
    """Enforce a 30-minute idle timeout and an 8-hour absolute authenticated session."""

    IDLE_TIMEOUT_SECONDS = 30 * 60
    ABSOLUTE_TIMEOUT_SECONDS = 8 * 60 * 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            now = int(timezone.now().timestamp())
            started = request.session.get("_nabd_auth_started")
            last_seen = request.session.get("_nabd_last_seen")
            expired = (
                (last_seen is not None and now - int(last_seen) > self.IDLE_TIMEOUT_SECONDS)
                or (started is not None and now - int(started) > self.ABSOLUTE_TIMEOUT_SECONDS)
            )
            if expired:
                logout(request)
            else:
                request.session.setdefault("_nabd_auth_started", now)
                request.session["_nabd_last_seen"] = now
        return self.get_response(request)


class SecurityResponseHeadersMiddleware:
    """A restrictive same-origin policy for the self-hosted, script-light UI."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        production = getattr(request, "is_secure", lambda: False)()
        directives = [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "manifest-src 'self'",
            "worker-src 'self'",
        ]
        if production:
            directives.append("upgrade-insecure-requests")
        response["Content-Security-Policy"] = "; ".join(directives)
        response["Referrer-Policy"] = "same-origin"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["Cross-Origin-Opener-Policy"] = "same-origin"
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        if not request.path.startswith(settings.STATIC_URL):
            response["Cache-Control"] = "private, no-store, max-age=0"
            response["Pragma"] = "no-cache"
        response["X-Permitted-Cross-Domain-Policies"] = "none"
        return response


class StrictHostMiddleware:
    """Reject unlisted Host headers while allowing private ALB health checks only."""

    def __init__(self, get_response):
        self.get_response = get_response
        import ipaddress
        self.health_networks = tuple(ipaddress.ip_network(value, strict=False) for value in settings.DJANGO_HEALTHCHECK_CIDRS)

    def __call__(self, request):
        from ipaddress import ip_address
        from urllib.parse import urlsplit
        from django.http import HttpResponseBadRequest
        from django.http.request import validate_host

        raw_host = request.META.get("HTTP_HOST", "")
        if not raw_host:
            server_name = request.META.get("SERVER_NAME", "")
            server_port = request.META.get("SERVER_PORT", "")
            raw_host = f"{server_name}:{server_port}" if server_name and server_port else server_name
        try:
            parsed = urlsplit("//" + raw_host)
            hostname = parsed.hostname
            # Reject credentials, paths and malformed authorities; ports are allowed for local dev.
            port = parsed.port  # Accessing it rejects malformed/out-of-range ports.
            if port is not None and not 1 <= port <= 65535:
                return HttpResponseBadRequest("Invalid host")
            if not hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                return HttpResponseBadRequest("Invalid host")
            host = hostname.encode("idna").decode("ascii").lower()
        except (ValueError, UnicodeError):
            return HttpResponseBadRequest("Invalid host")

        if validate_host(host, settings.ALLOWED_HOSTS):
            return self.get_response(request)

        # ALB IP target-group checks use a private IP Host header. Permit that value
        # only on the side-effect-free readiness endpoint and only from configured CIDRs.
        if request.path == "/healthz" and request.method == "GET" and settings.IS_PRODUCTION:
            try:
                ip = ip_address(host)
                if ip.is_private and any(ip in network for network in self.health_networks):
                    request.META["HTTP_HOST"] = settings.ALLOWED_HOSTS[0]
                    return self.get_response(request)
            except ValueError:
                pass
        return HttpResponseBadRequest("Invalid host")
