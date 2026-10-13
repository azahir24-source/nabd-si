from django.contrib.auth import views as auth_views
from django.urls import include, path
from newsroom import views

urlpatterns = [
    path("", views.home, name="home"),
   path("account/sign-in/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="sign_in"),
    path("account/sign-out/", auth_views.LogoutView.as_view(), name="sign_out"),
    path("account/bookmarks/", views.bookmarks, name="bookmarks"),
    path("account/bookmark/<uuid:article_id>/", views.toggle_bookmark, name="toggle_bookmark"),
    path("preferences/language/", views.set_language, name="set_language"),
    path("healthz", views.healthz, name="healthz"),
    path("sw.js", views.service_worker, name="service_worker"),
    #path("oidc/", include("mozilla_django_oidc.urls")),
]
