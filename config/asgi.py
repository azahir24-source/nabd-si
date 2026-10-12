import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.conf import settings
from django.core.asgi import get_asgi_application

if settings.APP_ENV == "build":
    raise RuntimeError("APP_ENV=build is for static collection only; set APP_ENV=production for the web task.")
if settings.APP_ROLE != "web":
    raise RuntimeError("Only APP_ROLE=web may start the public WSGI/ASGI application.")

application = get_asgi_application()
