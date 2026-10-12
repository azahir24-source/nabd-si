from urllib.parse import urlencode
import hashlib
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import Article, Bookmark, UserPreference
from .ingestion import safe_public_url


TEXT = {
    "en": {
        "ai": "Artificial intelligence",
        "cybersecurity": "Cybersecurity",
        "mixed": "AI + cybersecurity",
        "high": "High priority",
        "normal": "News",
        "arabic_pending_title": "Arabic translation is being prepared",
        "arabic_pending_summary": "This story is not yet available in Arabic. Please check again shortly.",
        "english_pending_title": "English translation is being prepared",
        "english_pending_summary": "This story is not yet available in English. Please check again shortly.",
    },
    "ar": {
        "ai": "الذكاء الاصطناعي",
        "cybersecurity": "الأمن السيبراني",
        "mixed": "الذكاء الاصطناعي والأمن السيبراني",
        "high": "أولوية عالية",
        "normal": "خبر",
        "arabic_pending_title": "الترجمة العربية قيد الإعداد",
        "arabic_pending_summary": "هذا الخبر غير متاح بالعربية بعد. يرجى المحاولة لاحقاً.",
        "english_pending_title": "الترجمة الإنجليزية قيد الإعداد",
        "english_pending_summary": "هذا الخبر غير متاح بالإنجليزية بعد. يرجى المحاولة لاحقاً.",
    },
}


def _language(request):
    if request.user.is_authenticated:
        try:
            return request.user.preferences.language
        except UserPreference.DoesNotExist:
            return "en"
    return request.session.get("ui_language", "en") if request.session.get("ui_language") in {"ar", "en"} else "en"


def _prepare_articles(articles, language):
    labels = TEXT[language]
    for article in articles:
        if language == "ar":
            article.card_title = article.title_ar or labels["arabic_pending_title"]
            article.card_summary = article.summary_ar or labels["arabic_pending_summary"]
            article.card_bullets = article.bullets_ar if isinstance(article.bullets_ar, list) else []
        else:
            article.card_title = article.title_en or labels["english_pending_title"]
            article.card_summary = article.summary_en or labels["english_pending_summary"]
            article.card_bullets = article.bullets_en if isinstance(article.bullets_en, list) else []
        article.safe_url = safe_public_url(article.url)
        article.category_label = labels.get(article.category, labels["ai"])
        article.priority_label = labels["high"] if article.priority == Article.Priority.HIGH else labels["normal"]
    return articles


def _news_context(request, articles, *, saved_only=False, query="", category=""):
    language = _language(request)
    bookmarks = set()
    if request.user.is_authenticated:
        bookmarks = set(Bookmark.objects.filter(user=request.user, article__in=articles).values_list("article_id", flat=True))
    return {
        "articles": _prepare_articles(articles, language),
        "saved_ids": bookmarks,
        "saved_only": saved_only,
        "query": query,
        "selected_category": category,
        "language": language,
    }


@require_GET
def home(request):
    query = request.GET.get("q", "").strip()[:100]
    category = request.GET.get("category", "").strip()
    saved_only = request.GET.get("saved") == "1"
    queryset = Article.objects.select_related("source").all()
    if category in {Article.Category.AI, Article.Category.CYBER}:
        queryset = queryset.filter(category=category)
    elif category:
        category = ""
    if query:
        queryset = queryset.filter(title_en__icontains=query) | queryset.filter(title_ar__icontains=query) | queryset.filter(summary_en__icontains=query) | queryset.filter(summary_ar__icontains=query)
        queryset = queryset.select_related("source")
    if saved_only:
        if not request.user.is_authenticated:
            return redirect("sign_in")
        queryset = queryset.filter(bookmarked_by__user=request.user)
    queryset = queryset.order_by("-published_at", "-ingested_at").distinct()[:60]
    context = _news_context(request, list(queryset), saved_only=saved_only, query=query, category=category)
    context["page_title"] = "Your bookmarks" if saved_only and context["language"] == "en" else ("المحفوظات" if saved_only else "NABD SI")
    context["results_count"] = len(context["articles"])
    return render(request, "newsroom/home.html", context)


@require_GET
def bookmarks(request):
    if not request.user.is_authenticated:
        return redirect("sign_in")
    queryset = Article.objects.select_related("source").filter(bookmarked_by__user=request.user).order_by("-bookmarked_by__created_at")[:100]
    context = _news_context(request, list(queryset), saved_only=True)
    context["page_title"] = "Your bookmarks" if context["language"] == "en" else "المحفوظات"
    context["results_count"] = len(context["articles"])
    return render(request, "newsroom/home.html", context)


@login_required
def toggle_bookmark(request, article_id):
    if request.method != "POST":
        return HttpResponse(status=405)
    article = get_object_or_404(Article, id=article_id)
    bookmark, created = Bookmark.objects.get_or_create(user=request.user, article=article)
    if not created:
        bookmark.delete()
    return_to = request.POST.get("return_to", "home")
    if return_to == "bookmarks":
        return redirect("bookmarks")
    return redirect("home")


@require_POST
def set_language(request):
    language = request.POST.get("language", "")
    if language not in {"en", "ar"}:
        return HttpResponse(status=400)
    if request.user.is_authenticated:
        UserPreference.objects.update_or_create(user=request.user, defaults={"language": language})
    else:
        request.session["ui_language"] = language
    target = request.POST.get("next", "/")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = "/"
    return redirect(target)


@require_GET
def sign_in(request):
    if request.user.is_authenticated:
        return redirect("home")
    status = 200 if settings.OIDC_CONFIGURED else 503
    return render(request, "newsroom/sign_in.html", status=status)


@require_POST
def sign_out(request):
    logout(request)
    if settings.OIDC_CONFIGURED:
        return_uri = request.build_absolute_uri(reverse("home"))
        query = urlencode({"client_id": settings.COGNITO_CLIENT_ID, "logout_uri": return_uri})
        return redirect(f"{settings.COGNITO_DOMAIN}/logout?{query}")
    return redirect("home")


@require_GET
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unhealthy"}, status=503)
    return JsonResponse({"status": "ok"})


@require_GET
def service_worker(request):
    path = settings.BASE_DIR / "newsroom" / "static" / "newsroom" / "sw.js"
    body = path.read_text(encoding="utf-8")
    names = [
        "newsroom/site.css", "newsroom/manifest.webmanifest", "newsroom/icon.svg",
        "newsroom/icon-192.png", "newsroom/icon-512.png",
        "newsroom/fonts/tajawal-arabic-400.woff2", "newsroom/fonts/tajawal-arabic-500.woff2",
        "newsroom/fonts/tajawal-arabic-700.woff2", "newsroom/offline.html",
    ]
    versioned = [staticfiles_storage.url(name) for name in names]
    # Cache immutable build-hashed paths and the stable aliases required by the offline shell.
    stable = [settings.STATIC_URL + name for name in names]
    assets = list(dict.fromkeys(versioned + stable))
    version = hashlib.sha256("\n".join(versioned).encode("utf-8")).hexdigest()[:12]
    body = body.replace("__CACHE_NAME__", "nabd-si-static-" + version)
    body = body.replace("__STATIC_ASSETS__", json.dumps(assets))
    response = HttpResponse(body, content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, no-store"
    return response
