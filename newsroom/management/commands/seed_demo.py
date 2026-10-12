from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from newsroom.models import Article, Source


DEMO_STORIES = [
    {
        "slug": "demo-ai-research",
        "url": "https://example.org/demo/efficient-models",
        "category": Article.Category.AI,
        "priority": Article.Priority.NORMAL,
        "title_en": "Researchers report a more efficient way to train compact AI models",
        "title_ar": "باحثون يعلنون طريقة أكثر كفاءة لتدريب نماذج ذكاء اصطناعي صغيرة",
        "summary_en": "A research team describes a training approach designed to reduce compute needs for smaller language models. The result is an early research report and has not yet been independently validated.",
        "summary_ar": "يشرح فريق بحثي أسلوباً تدريبياً يهدف إلى تقليل متطلبات الحوسبة للنماذج اللغوية الصغيرة. وتظل النتائج أولية ولم تخضع بعد للتحقق المستقل.",
        "bullets_en": ["The report focuses on smaller language models.", "Independent validation is still needed."],
        "bullets_ar": ["يركز التقرير على النماذج اللغوية الصغيرة.", "لا تزال النتائج بحاجة إلى تحقق مستقل."],
    },
    {
        "slug": "demo-cyber-advisory",
        "url": "https://example.org/demo/security-advisory",
        "category": Article.Category.CYBER,
        "priority": Article.Priority.HIGH,
        "title_en": "Security teams are urged to review a newly published software advisory",
        "title_ar": "دعوة فرق الأمن إلى مراجعة تحذير جديد بشأن أحد البرامج",
        "summary_en": "A software publisher has issued a security notice and recommends customers review the affected versions. Check the original advisory for product scope, fixed releases, and mitigation steps.",
        "summary_ar": "أصدر أحد ناشري البرمجيات إشعاراً أمنياً، وأوصى العملاء بمراجعة الإصدارات المتأثرة. يُرجى الرجوع إلى التحذير الأصلي للتحقق من نطاق المنتجات والإصدارات المصححة وخطوات التخفيف.",
        "bullets_en": ["Confirm whether deployed versions are affected.", "Use the publisher's advisory for remediation guidance."],
        "bullets_ar": ["تحقق من تأثر الإصدارات المستخدمة لديك.", "اتبع إرشادات المعالجة الواردة في التحذير الأصلي."],
    },
    {
        "slug": "demo-ai-policy",
        "url": "https://example.org/demo/ai-policy",
        "category": Article.Category.AI,
        "priority": Article.Priority.NORMAL,
        "title_en": "Public consultation opens on proposed rules for high-impact AI systems",
        "title_ar": "بدء مشاورة عامة حول قواعد مقترحة لأنظمة الذكاء الاصطناعي عالية التأثير",
        "summary_en": "A public consultation invites feedback on governance proposals for AI systems used in consequential settings. The draft is open for comment; it is not yet a final regulation.",
        "summary_ar": "تدعو مشاورة عامة إلى إبداء الرأي بشأن مقترحات حوكمة أنظمة الذكاء الاصطناعي المستخدمة في مجالات مؤثرة. ولا تزال المسودة مفتوحة للتعليقات وليست لائحة نهائية.",
        "bullets_en": ["The proposals remain under consultation.", "Check the source for deadlines and scope."],
        "bullets_ar": ["لا تزال المقترحات قيد المشاورة.", "راجع المصدر لمعرفة المواعيد والنطاق."],
    },
]


class Command(BaseCommand):
    help = "Load clearly labelled synthetic stories for local UI preview only."

    def handle(self, *args, **options):
        if settings.APP_ENV == "production":
            raise CommandError("Demo content is disabled in production.")
        source, _ = Source.objects.get_or_create(
            slug="demo-desk",
            defaults={"name": "NABD SI Demo Desk", "feed_url": "https://example.org/feed.xml", "category": "mixed", "language": "en", "enabled": False},
        )
        now = timezone.now()
        for index, story in enumerate(DEMO_STORIES):
            Article.objects.update_or_create(
                url=story["url"],
                defaults={
                    "source": source,
                    "original_language": "en",
                    "title_en": story["title_en"],
                    "title_ar": story["title_ar"],
                    "summary_en": story["summary_en"],
                    "summary_ar": story["summary_ar"],
                    "bullets_en": story["bullets_en"],
                    "bullets_ar": story["bullets_ar"],
                    "category": story["category"],
                    "priority": story["priority"],
                    "published_at": now - timedelta(minutes=index * 17),
                    "is_demo": True,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(DEMO_STORIES)} synthetic demo stories."))
