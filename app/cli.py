from __future__ import annotations

import argparse
import asyncio
import json

from app.ai import list_gemini_models, run_ai_analysis
from app.config import get_settings
from app.db import Database
from app.offline_import import import_strava_zip
from app.sync import export_from_database, sync_activities


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync and export 2026 Strava running activities"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync", help="Fetch new runs and export")
    sync_parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Re-download stored runs (uses more Strava API requests)",
    )
    subparsers.add_parser("export", help="Regenerate exports from SQLite")
    subparsers.add_parser(
        "analyze",
        help="Create deterministic analysis and optionally ask Gemini",
    )
    subparsers.add_parser(
        "list-models",
        help="List Gemini generateContent models available to your API key",
    )
    subparsers.add_parser("init", help="Initialize the SQLite database")
    import_parser = subparsers.add_parser(
        "import-zip", help="Import a Strava account export ZIP without the API"
    )
    import_parser.add_argument("zip_path", help="Path to the Strava ZIP file")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()
    database = Database(settings)
    if args.command == "init":
        database.initialize()
        result = {"status": "ok", "database": str(database.path.resolve())}
    elif args.command == "export":
        result = export_from_database(settings, database)
    elif args.command == "analyze":
        result = asyncio.run(run_ai_analysis(settings, database))
    elif args.command == "list-models":
        result = asyncio.run(list_gemini_models(settings))
    elif args.command == "import-zip":
        try:
            result = import_strava_zip(args.zip_path, settings, database)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        result = asyncio.run(
            sync_activities(
                settings,
                database,
                refresh_existing=args.refresh_existing,
            )
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
