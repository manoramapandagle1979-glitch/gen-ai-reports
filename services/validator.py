"""Validate generated reports against required schema before Supabase insertion."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Fields that must be present and non-empty
_REQUIRED_TEXT = [
    "report_id", "slug", "title", "industry",
    "executive_summary", "meta_title", "meta_description",
]

_REQUIRED_JSONB = [
    "market_size", "market_overview", "market_dynamics",
    "market_segmentation", "regional_analysis", "market_forecast",
    "competitive_landscape", "faqs",
]

_MARKET_SIZE_KEYS = {"current_value", "forecast_value", "cagr"}


def _check_shares_sum(items: list, field: str = "market_share", label: str = "") -> list[str]:
    """Return errors if numeric shares don't sum to ~100."""
    errors = []
    total = sum(float(i.get(field, 0)) for i in items if isinstance(i, dict))
    if total > 0 and not (95 <= total <= 105):
        errors.append(f"{label} shares sum to {total:.1f}% (expected ~100)")
    return errors


def validate_report(report: dict) -> tuple[bool, list[str]]:
    """
    Validate a generated report dict.
    Returns (is_valid, list_of_error_messages).
    """
    errors: list[str] = []

    if not isinstance(report, dict):
        return False, ["Report is not a dict"]

    # Required text fields
    for field in _REQUIRED_TEXT:
        val = report.get(field)
        if not val or not str(val).strip():
            errors.append(f"Missing or empty required field: '{field}'")

    # Slug = report_id
    if report.get("slug") != report.get("report_id"):
        errors.append(f"slug '{report.get('slug')}' != report_id '{report.get('report_id')}'")

    # Slug format (no spaces, lowercase)
    slug = report.get("slug", "")
    if slug and (" " in slug or slug != slug.lower()):
        errors.append(f"slug '{slug}' must be lowercase with no spaces")

    # Required JSONB fields
    for field in _REQUIRED_JSONB:
        val = report.get(field)
        if not val:
            errors.append(f"Missing required JSONB field: '{field}'")

    # market_size numeric consistency
    ms = report.get("market_size", {})
    if isinstance(ms, dict):
        for key in _MARKET_SIZE_KEYS:
            v = ms.get(key)
            if v is None:
                errors.append(f"market_size.{key} is missing")
            elif not isinstance(v, (int, float)) or float(v) <= 0:
                errors.append(f"market_size.{key} must be a positive number, got {v!r}")

        current = float(ms.get("current_value", 0))
        forecast = float(ms.get("forecast_value", 0))
        if current > 0 and forecast > 0 and forecast < current:
            errors.append(
                f"market_size.forecast_value ({forecast}) < current_value ({current})"
            )

    # market_forecast: must have 9 entries (2024-2032), ascending values
    mf = report.get("market_forecast", [])
    if isinstance(mf, list):
        if len(mf) != 9:
            errors.append(f"market_forecast must have 9 entries (2024-2032), got {len(mf)}")
        years = [e.get("year") for e in mf if isinstance(e, dict)]
        expected_years = list(range(2024, 2033))
        if years != expected_years:
            errors.append(f"market_forecast years mismatch: {years}")
        values = [float(e.get("value", 0)) for e in mf if isinstance(e, dict)]
        if values and values != sorted(values):
            errors.append("market_forecast values are not monotonically increasing")

    # regional_analysis shares
    ra = report.get("regional_analysis", [])
    if isinstance(ra, list) and ra:
        errors.extend(_check_shares_sum(ra, "market_share", "regional_analysis"))

    # segmentation shares per segment
    seg = report.get("market_segmentation", {})
    if isinstance(seg, dict):
        for key, items in seg.items():
            if isinstance(items, list) and items and isinstance(items[0], dict):
                errors.extend(_check_shares_sum(items, "market_share", f"segmentation.{key}"))

    # FAQs
    faqs = report.get("faqs", [])
    if isinstance(faqs, list) and len(faqs) < 2:
        errors.append(f"faqs should have at least 2 entries, got {len(faqs)}")

    # competitive_landscape
    cl = report.get("competitive_landscape", [])
    if isinstance(cl, list) and len(cl) < 3:
        errors.append(f"competitive_landscape should have at least 3 entries, got {len(cl)}")

    # meta_description length
    md = report.get("meta_description", "")
    if md and len(md) > 320:
        errors.append(f"meta_description too long ({len(md)} chars, max 320)")

    is_valid = len(errors) == 0
    if not is_valid:
        logger.warning("Validation failed for '%s': %s", report.get("slug"), errors)
    return is_valid, errors
