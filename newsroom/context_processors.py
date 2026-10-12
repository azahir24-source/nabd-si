from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist


def ui_settings(request):
    language = request.session.get("ui_language", "en")
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        try:
            language = user.preferences.language
        except ObjectDoesNotExist:
            pass
    if language not in {"en", "ar"}:
        language = "en"
    return {
        "ui_language": language,
        "ui_direction": "rtl" if language == "ar" else "ltr",
        "is_oidc_configured": settings.OIDC_CONFIGURED,
    }
