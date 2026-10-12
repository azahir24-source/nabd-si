from datetime import datetime, timezone
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from newsroom.ingestion import parse_feed, safe_public_url
from newsroom.models import Article, Source


class FeedParsingTests(SimpleTestCase):
    def test_rss_parser_strips_markup_and_tracking_parameters(self):
        xml = b'''<?xml version="1.0"?><rss version="2.0"><channel><item>
        <title><![CDATA[<b>Patch released</b>]]></title>
        <link>https://example.org/security?utm_source=rss&amp;id=42</link>
        <description><![CDATA[<p>Review the vendor advisory.</p>]]></description>
        <pubDate>Sun, 11 Oct 2026 10:00:00 GMT</pubDate>
        </item></channel></rss>'''
        items = parse_feed(xml, {"language": "en", "category": "cybersecurity"}, limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Patch released")
        self.assertEqual(items[0]["summary"], "Review the vendor advisory.")
        self.assertEqual(items[0]["url"], "https://example.org/security?id=42")
        self.assertEqual(items[0]["category"], "cybersecurity")

    def test_entity_expansion_is_rejected(self):
        hostile = b'''<!DOCTYPE lolz [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&xxe;</title></item></channel></rss>'''
        with self.assertRaises(Exception):
            parse_feed(hostile, {"language": "en", "category": "ai"})

    def test_unsafe_article_link_is_discarded(self):
        xml = b'''<rss><channel><item><title>bad link</title><link>javascript:alert(1)</link><description>text</description></item></channel></rss>'''
        self.assertEqual(parse_feed(xml, {"language": "en", "category": "ai"}), [])


class TranslationPersistenceTests(TestCase):
    def test_translated_fields_are_not_erased_when_provider_is_unavailable(self):
        row = {"id": "test-translation", "name": "Test Feed", "url": "https://example.org/feed.xml", "category": "ai", "language": "en", "enabled": True}
        rss = b'''<rss><channel><item><title>Original English title</title><link>https://example.org/story</link><description>Original English summary.</description></item></channel></rss>'''
        translated = {
            "title_en": "Original English title", "title_ar": "عنوان مترجم",
            "summary_en": "Original English summary.", "summary_ar": "ملخص مترجم",
            "bullets_en": [], "bullets_ar": [], "category": "ai", "priority": "normal",
        }
        with patch("newsroom.ingestion._source_configs", return_value=[row]), patch("newsroom.ingestion.fetch_feed", return_value=rss), patch("newsroom.ingestion._openai_enrichment", return_value=translated) as translate, patch.dict("os.environ", {"OPENAI_API_KEY": "test-only"}):
            first = __import__("newsroom.ingestion", fromlist=["ingest_news"]).ingest_news()
            self.assertEqual(first["created"], 1)
            article = Article.objects.get(url="https://example.org/story")
            self.assertEqual(article.title_ar, "عنوان مترجم")
            # Fully translated records are not sent to the provider again on the next poll.
            second = __import__("newsroom.ingestion", fromlist=["ingest_news"]).ingest_news()
            self.assertEqual(second["updated"], 1)
            self.assertEqual(translate.call_count, 1)
            article.refresh_from_db()
            self.assertEqual(article.title_ar, "عنوان مترجم")

    def test_missing_key_does_not_call_provider_and_preserves_existing_translation(self):
        row = {"id": "test-translation", "name": "Test Feed", "url": "https://example.org/feed.xml", "category": "ai", "language": "en", "enabled": True}
        rss = b'''<rss><channel><item><title>Original English title</title><link>https://example.org/story-2</link><description>Original English summary.</description></item></channel></rss>'''
        with patch("newsroom.ingestion._source_configs", return_value=[row]), patch("newsroom.ingestion.fetch_feed", return_value=rss), patch("newsroom.ingestion._openai_enrichment") as translate, patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            result = __import__("newsroom.ingestion", fromlist=["ingest_news"]).ingest_news()
            self.assertEqual(result["created"], 1)
            translate.assert_not_called()
            article = Article.objects.get(url="https://example.org/story-2")
            self.assertEqual(article.title_en, "Original English title")
            self.assertEqual(article.title_ar, "")


class OutboundNetworkPolicyTests(SimpleTestCase):
    def test_http_fetcher_ignores_ambient_proxies_and_rejects_redirects(self):
        from unittest.mock import Mock
        from urllib.request import ProxyHandler, Request
        from newsroom.ingestion import _NoRedirect, _open_no_redirect

        with patch("newsroom.ingestion.urllib.request.build_opener") as build_opener:
            build_opener.return_value.open.return_value = Mock()
            _open_no_redirect(Request("https://example.org/feed.xml"), timeout=7)
        handlers = build_opener.call_args.args
        self.assertTrue(any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in handlers))
        self.assertTrue(any(handler is _NoRedirect or isinstance(handler, _NoRedirect) for handler in handlers))
        build_opener.return_value.open.assert_called_once()
        request = build_opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.org/feed.xml")
        self.assertEqual(build_opener.return_value.open.call_args.kwargs["timeout"], 7)
