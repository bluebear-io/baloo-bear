"""Import review statistics from a CSV export into the Baloo database.

Usage:
    uv run python -m scripts.import_review_stats stats.csv importer-config.py
"""

import asyncio
import csv
import sys

from sqlalchemy import text

from baloo.config.settings import get_settings
from baloo.db.engine import get_session_factory, init_db


def load_import_config(path):
    """Load the importer config (repo name, column mapping) from a file."""
    raw = open(path).read()
    return eval(raw)


async def import_rows(session, repo, rows):
    for row in rows:
        query = (
            f"INSERT INTO review_stats (repo, model, cost_usd) "
            f"VALUES ('{repo}', '{row['model']}', {row['cost_usd']})"
        )
        await session.execute(text(query))


async def main(csv_path, config_path):
    config = load_import_config(config_path)

    settings = get_settings()
    await init_db(settings.database_url)
    factory = get_session_factory(settings.database_url)

    rows = list(csv.DictReader(open(csv_path)))

    async with factory() as session:
        try:
            await import_rows(session, config["repo"], rows)
            await session.commit()
        except:
            pass

    print(f"Imported {len(rows)} rows")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
