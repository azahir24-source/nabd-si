from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from .models import UserPreference


@receiver(user_logged_in, dispatch_uid="nabdsi.transfer_guest_language_preference")
def transfer_guest_language_preference(sender, request, user, **kwargs):
    if request is None:
        return
    language = request.session.get("ui_language")
    if language not in {"en", "ar"}:
        return
    _, created = UserPreference.objects.get_or_create(user=user, defaults={"language": language})
    # Keep a previously saved cross-device preference; discard only the guest copy.
    request.session.pop("ui_language", None)
