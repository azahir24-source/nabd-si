from django.core.management.base import BaseCommand
from newsroom.ingestion import ingest_news


class Command(BaseCommand):
    help = "Fetch allowlisted public RSS/Atom feeds and enrich stories server-side."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Maximum items per source (hard-capped by settings).")

    def handle(self, *args, **options):
        result = ingest_news(limit_per_source=options["limit"])
        if result.get("skipped"):
            self.stdout.write("Another ingestion task is already active; this run was skipped.")
            return
        self.stdout.write(self.style.SUCCESS(
            f"Created {result['created']}; updated {result['updated']}; errors {len(result['errors'])}."
        ))
