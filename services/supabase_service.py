"""Supabase service — insert reports into neograph_reports table."""
import logging
import os
import time
from typing import Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)

TABLE = "neograph_reports"

# Columns that exist as plain text/int in the table
_TEXT_INT_COLUMNS = {
    "report_id", "slug", "title", "industry",
    "published_year", "base_year", "forecast_period", "historical_period",
    "currency", "unit", "executive_summary", "market_context",
    "forecast_analysis", "meta_title", "meta_description",
}

# Columns stored as JSONB
_JSONB_COLUMNS = {
    "meta_keywords",
    "market_size", "market_overview", "market_dynamics", "market_segmentation",
    "regional_analysis", "country_analysis", "market_forecast", "charts",
    "competitive_landscape", "company_profiles", "recent_developments",
    "regulatory_landscape", "research_methodology", "faqs",
    "key_highlights", "reader_takeaways",
}

_ALL_COLUMNS = _TEXT_INT_COLUMNS | _JSONB_COLUMNS

# Maps pipeline industry names → valid frontend category names (from data/categories.json)
_INDUSTRY_TO_CATEGORY: dict[str, str] = {
    # Healthcare IT / Digital Health
    "Healthcare AI": "Healthcare IT",
    "Healthcare Technology": "Healthcare IT",
    "Artificial Intelligence": "Healthcare IT",
    "Clinical Decision Support": "Healthcare IT",
    "Remote Patient Monitoring": "Healthcare IT",
    "Telemedicine": "Healthcare IT",
    "Digital Health": "Healthcare IT",
    "Digital Therapeutics": "Healthcare IT",
    "Mental Health Technology": "Healthcare IT",
    "Health Information Technology": "Healthcare IT",
    "Telehealth": "Healthcare IT",
    "mHealth": "Healthcare IT",
    "EHR": "Healthcare IT",
    # Medical Imaging
    "Medical Imaging AI": "Medical Imaging",
    "Medical Imaging": "Medical Imaging",
    "Radiology AI": "Medical Imaging",
    "Diagnostic Imaging": "Medical Imaging",
    # Pharmaceuticals
    "Drug Discovery AI": "Pharmaceuticals",
    "Pharmaceutical Technology": "Pharmaceuticals",
    "Pharmaceutical": "Pharmaceuticals",
    "Pharmaceuticals": "Pharmaceuticals",
    "Drug Discovery": "Pharmaceuticals",
    "Biosimilars": "Pharmaceuticals",
    "Antibody Drug Conjugates": "Pharmaceuticals",
    "RNA Therapeutics": "Pharmaceuticals",
    "Oncology": "Pharmaceuticals",
    "Rare Disease": "Pharmaceuticals",
    # Biotechnology
    "Biotechnology": "Biotechnology",
    "Precision Medicine": "Biotechnology",
    "Genomics": "Biotechnology",
    "Gene Therapy": "Biotechnology",
    "Cell & Gene Therapy Manufacturing": "Biotechnology",
    "CRISPR Technology": "Biotechnology",
    "Microbiome Therapeutics": "Biotechnology",
    "Synthetic Biology": "Biotechnology",
    # Medical Devices
    "Medical Devices": "Medical Devices",
    "Wearable Medical Devices": "Medical Devices",
    "Surgical Robotics": "Medical Devices",
    "Continuous Glucose Monitoring": "Medical Devices",
    "Wearables": "Medical Devices",
    "Point of Care Diagnostics": "Medical Devices",
    # Clinical Diagnostics
    "Clinical Diagnostics": "Clinical Diagnostics",
    "In Vitro Diagnostics": "Clinical Diagnostics",
    "Molecular Diagnostics": "Clinical Diagnostics",
    # Healthcare Services
    "Healthcare Services": "Healthcare Services",
    "Home Healthcare": "Healthcare Services",
    "Ambulatory Care": "Healthcare Services",
    # Laboratory Equipment
    "Laboratory Equipment": "Laboratory Equipment",
    "Lab Automation": "Laboratory Equipment",
    # Life Sciences (catch-all for non-specific topics)
    "Life Sciences": "Life Sciences",
    "Bioinformatics": "Life Sciences",
    "Clinical Trials": "Life Sciences",
    # Dental
    "Dental": "Dental",
    # Animal Health
    "Animal Health": "Animal Health",
    "Veterinary": "Animal Health",
}

# Default for any industry not in the map
_DEFAULT_CATEGORY = "Life Sciences"


def _map_industry_to_category(industry: str) -> str:
    """Return the closest valid frontend category for a pipeline industry name."""
    if not industry:
        return _DEFAULT_CATEGORY
    # Exact match
    if industry in _INDUSTRY_TO_CATEGORY:
        return _INDUSTRY_TO_CATEGORY[industry]
    # Case-insensitive partial match on any key
    lower = industry.lower()
    for key, cat in _INDUSTRY_TO_CATEGORY.items():
        if key.lower() in lower or lower in key.lower():
            return cat
    return _DEFAULT_CATEGORY


def _prepare_row(report: dict) -> dict:
    """Extract only the columns that exist in neograph_reports."""
    row: dict = {}

    # Flat text/int columns
    for col in _TEXT_INT_COLUMNS:
        if col in report:
            row[col] = report[col]

    # Remap industry to a valid frontend category name
    if "industry" in row:
        row["industry"] = _map_industry_to_category(row["industry"])

    # JSONB columns
    for col in _JSONB_COLUMNS:
        if col in report:
            row[col] = report[col]

    # Flatten SEO subobject if the report still has it nested
    seo = report.get("seo", {})
    if isinstance(seo, dict):
        if "meta_title" not in row and seo.get("meta_title"):
            row["meta_title"] = seo["meta_title"]
        if "meta_description" not in row and seo.get("meta_description"):
            row["meta_description"] = seo["meta_description"]
        keywords = seo.get("keywords") or seo.get("meta_keywords")
        if "meta_keywords" not in row and keywords:
            row["meta_keywords"] = keywords

    return row


class SupabaseService:
    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        self._client: Client = create_client(url, key)

    def get_existing_slugs(self) -> set[str]:
        """Return the set of slugs already in the table."""
        try:
            data = self._client.table(TABLE).select("slug").execute()
            return {row["slug"] for row in (data.data or [])}
        except Exception as exc:
            logger.error("Failed to fetch existing slugs: %s", exc)
            return set()

    def insert_report(self, report: dict, max_retries: int = 3) -> bool:
        """
        Insert a validated report into Supabase.
        Returns True on success, False on permanent failure.
        """
        slug = report.get("slug", "unknown")
        row = _prepare_row(report)

        for attempt in range(max_retries):
            try:
                result = (
                    self._client.table(TABLE)
                    .upsert(row, on_conflict="slug")
                    .execute()
                )
                if result.data:
                    logger.info("Inserted report: %s", slug)
                    return True
                logger.warning(
                    "Upsert for '%s' returned no data (attempt %d): %s",
                    slug, attempt + 1, result
                )
            except Exception as exc:
                logger.error(
                    "Insert error for '%s' (attempt %d): %s",
                    slug, attempt + 1, exc
                )
                if attempt < max_retries - 1:
                    time.sleep(4 * (attempt + 1))

        return False

    def count_reports(self) -> int:
        """Return total row count in the reports table."""
        try:
            result = (
                self._client.table(TABLE)
                .select("report_id", count="exact")
                .execute()
            )
            return result.count or 0
        except Exception as exc:
            logger.error("Count query failed: %s", exc)
            return -1
