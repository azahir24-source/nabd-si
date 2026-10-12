import uuid
from django.conf import settings
from django.db import models


class Source(models.Model):
    class Category(models.TextChoices):
        AI = "ai", "Artificial intelligence"
        CYBER = "cybersecurity", "Cybersecurity"
        MIXED = "mixed", "AI and cybersecurity"

    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=160)
    feed_url = models.URLField(max_length=2048)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.MIXED)
    language = models.CharField(max_length=2, choices=(("en", "English"), ("ar", "Arabic")), default="en")
    enabled = models.BooleanField(default=True)
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Article(models.Model):
    class Category(models.TextChoices):
        AI = "ai", "Artificial intelligence"
        CYBER = "cybersecurity", "Cybersecurity"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        HIGH = "high", "High priority"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, related_name="articles")
    url = models.URLField(max_length=2048, unique=True)
    original_language = models.CharField(max_length=2, choices=(("en", "English"), ("ar", "Arabic")), default="en")
    title_en = models.CharField(max_length=500, blank=True)
    title_ar = models.CharField(max_length=500, blank=True)
    summary_en = models.TextField(blank=True)
    summary_ar = models.TextField(blank=True)
    bullets_en = models.JSONField(default=list, blank=True)
    bullets_ar = models.JSONField(default=list, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.AI, db_index=True)
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.NORMAL, db_index=True)
    published_at = models.DateTimeField(db_index=True)
    ingested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    is_demo = models.BooleanField(default=False)

    class Meta:
        ordering = ["-published_at", "-ingested_at"]
        indexes = [
            models.Index(fields=["category", "-published_at"], name="article_category_date_idx"),
            models.Index(fields=["-published_at", "id"], name="article_published_id_idx"),
        ]

    def __str__(self):
        return self.title_en or self.title_ar or str(self.id)


class Bookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="news_bookmarks")
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="bookmarked_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "article"], name="unique_user_article_bookmark"),
        ]
        indexes = [models.Index(fields=["user", "-created_at"], name="bookmark_user_date_idx")]

    def __str__(self):
        return f"{self.user_id}:{self.article_id}"


class UserPreference(models.Model):
    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        ARABIC = "ar", "Arabic"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences")
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.ENGLISH)
    updated_at = models.DateTimeField(auto_now=True)


class IngestionRun(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at"]
