"""
Pipeline orchestrator — one cycle: discover trends → generate reports → insert into Supabase.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the pipeline directory
load_dotenv(Path(__file__).parent / ".env", override=True)

# Configure logging before importing services
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Ensure services/ is importable
sys.path.insert(0, str(Path(__file__).parent))

from services.trend_discovery import discover_trends, load_trending_reports
from services.report_generator import generate_report
from services.supabase_service import SupabaseService
from services.validator import validate_report

DATA_DIR = Path(__file__).parent / "data"
GENERATED_FILE = DATA_DIR / "generated_reports.json"

# How many reports to attempt per pipeline cycle
REPORTS_PER_CYCLE = int(os.getenv("REPORTS_PER_CYCLE", "3"))


def _load_generated_index() -> dict[str, str]:
    """Return {slug: generated_at} for already-generated reports."""
    if GENERATED_FILE.exists():
        try:
            records = json.loads(GENERATED_FILE.read_text(encoding="utf-8"))
            return {r["slug"]: r.get("generated_at", "") for r in records}
        except Exception:
            pass
    return {}


def _record_generated(report: dict) -> None:
    """Append a lightweight record to generated_reports.json."""
    DATA_DIR.mkdir(exist_ok=True)
    records: list = []
    if GENERATED_FILE.exists():
        try:
            records = json.loads(GENERATED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    records.append({
        "slug": report["slug"],
        "title": report.get("title", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })
    GENERATED_FILE.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def run_pipeline_cycle() -> dict:
    """
    Execute one full pipeline cycle.
    Returns a summary dict with cycle stats.
    """
    logger.info("=" * 60)
    logger.info("Pipeline cycle started — %s", datetime.now(timezone.utc).isoformat())
    logger.info("=" * 60)

    # Fast-fail if API key is invalid — saves 3× retry wasted time
    from services.openrouter_client import test_connection
    if not test_connection():
        raise RuntimeError(
            "OpenRouter connection failed. Set a valid OPENROUTER_API_KEY in pipeline/.env "
            "(get one free at https://openrouter.ai/keys)"
        )

    stats = {
        "cycle_start": datetime.now(timezone.utc).isoformat(),
        "attempted": 0,
        "generated": 0,
        "validated": 0,
        "inserted": 0,
        "skipped_duplicate": 0,
        "errors": 0,
    }

    # 1. Refresh trending index
    logger.info("Step 1 — Refreshing trending report index…")
    try:
        discover_trends()
    except Exception as exc:
        logger.error("Trend discovery failed: %s", exc, exc_info=True)

    # 2. Determine which slugs still need generation
    trending = load_trending_reports()
    if not trending:
        logger.warning("No trending topics found — skipping generation.")
        return stats

    try:
        db = SupabaseService()
        existing_in_db = db.get_existing_slugs()
    except Exception as exc:
        logger.error("Supabase connection failed: %s", exc)
        return stats

    generated_index = _load_generated_index()
    all_processed = existing_in_db | set(generated_index.keys())

    pending = [t for t in trending if t["slug"] not in all_processed]
    logger.info(
        "Trending: %d | In DB: %d | Locally generated: %d | Pending: %d",
        len(trending), len(existing_in_db), len(generated_index), len(pending),
    )

    if not pending:
        logger.info("All trending topics already processed — nothing to generate.")
        return stats

    # Sort by demand score, take top N for this cycle
    pending.sort(key=lambda x: x.get("demand_score", 0), reverse=True)
    batch = pending[:REPORTS_PER_CYCLE]
    logger.info("Generating %d report(s) this cycle…", len(batch))

    # 3. Generate → validate → insert
    for item in batch:
        slug = item["slug"]
        stats["attempted"] += 1
        logger.info("--- Processing: %s", item["title"])

        try:
            report = generate_report(item)
        except Exception as exc:
            logger.error("Generation exception for '%s': %s", slug, exc, exc_info=True)
            stats["errors"] += 1
            continue

        if not report:
            logger.warning("No report returned for '%s'", slug)
            stats["errors"] += 1
            continue

        stats["generated"] += 1

        is_valid, validation_errors = validate_report(report)
        if not is_valid:
            logger.warning(
                "Report '%s' failed validation (%d errors): %s",
                slug, len(validation_errors), validation_errors[:3],
            )
            stats["errors"] += 1
            continue

        stats["validated"] += 1

        success = db.insert_report(report)
        if success:
            _record_generated(report)
            stats["inserted"] += 1
            logger.info("✓ Inserted: %s", slug)
        else:
            logger.error("✗ Insert failed for: %s", slug)
            stats["errors"] += 1

    stats["cycle_end"] = datetime.now(timezone.utc).isoformat()
    total_in_db = db.count_reports()
    logger.info(
        "Cycle complete — attempted: %d | inserted: %d | errors: %d | total in DB: %d",
        stats["attempted"], stats["inserted"], stats["errors"], total_in_db,
    )
    return stats


if __name__ == "__main__":
    run_pipeline_cycle()
