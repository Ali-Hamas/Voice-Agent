"""CLI: rebuild the knowledge index for a restaurant.

Usage:
    python -m app.ingest <slug>
"""
import logging
import sys

from .config import LOG_LEVEL
from .db import get_restaurant_by_slug, init_db
from .rag import ingest_for


def main() -> None:
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")
    init_db()
    if len(sys.argv) < 2:
        print("Usage: python -m app.ingest <slug>")
        sys.exit(1)
    slug = sys.argv[1]
    if not get_restaurant_by_slug(slug):
        print(f"No restaurant with slug={slug!r}")
        sys.exit(2)
    n = ingest_for(slug)
    print(f"Indexed {n} chunks for {slug}.")


if __name__ == "__main__":
    main()
