"""
APScheduler-based runner — executes the pipeline every 10 minutes.
Run with:  python scheduler.py
"""
import logging
import sys
import time
from pathlib import Path

# Must load env and set up path before importing main
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).parent))

import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from main import run_pipeline_cycle

logger = logging.getLogger(__name__)


def _job_wrapper():
    """Wrap the pipeline cycle so scheduler catches and logs exceptions."""
    try:
        stats = run_pipeline_cycle()
        logger.info("Scheduled cycle stats: %s", stats)
    except Exception as exc:
        logger.exception("Unhandled exception in scheduled cycle: %s", exc)


if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        _job_wrapper,
        trigger=IntervalTrigger(minutes=10),
        id="market_research_pipeline",
        name="Market Research Generation Pipeline",
        replace_existing=True,
        max_instances=1,        # never overlap cycles
        misfire_grace_time=120, # allow 2 min grace if a cycle runs long
    )

    logger.info("Scheduler started — pipeline runs every 10 minutes.")
    logger.info("Running first cycle immediately on startup…")

    # Run immediately on startup so we don't wait 10 min for the first report
    _job_wrapper()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
