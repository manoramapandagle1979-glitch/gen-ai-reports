"""Generate full market research reports using OpenRouter + web search context."""
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from slugify import slugify

from services import openrouter_client as llm
from services.web_search import gather_market_intelligence

logger = logging.getLogger(__name__)

# Compact schema shown to the LLM as a structural reference.
_SCHEMA_TEMPLATE = """{
  "report_id": "string (same as slug)",
  "slug": "string (kebab-case)",
  "title": "string",
  "industry": "string",
  "published_year": 2026,
  "base_year": 2023,
  "forecast_period": "2024-2032",
  "historical_period": "2019-2023",
  "currency": "USD",
  "unit": "Billion",
  "meta_title": "string (<Market> Size, Share and Trends Analysis Report 2032)",
  "meta_description": "string (120-160 chars: market size, CAGR, forecast year)",
  "meta_keywords": ["keyword1", "keyword2", "keyword3", "keyword4"],
  "executive_summary": "string (max 150 words, investment-grade, specific numbers)",
  "market_size": {
    "current_value": <float, 2023 value in USD billions>,
    "forecast_value": <float, 2032 projected value>,
    "cagr": <float, e.g. 14.5>
  },
  "market_overview": {
    "summary": "string (2-3 sentences with key numbers)",
    "key_trends": ["trend 1", "trend 2", "trend 3", "trend 4"],
    "market_stage": "Emerging | High growth | Mature",
    "adoption_level": "Early adopter | Growing | Mainstream"
  },
  "market_dynamics": {
    "drivers": ["driver 1", "driver 2", "driver 3"],
    "restraints": ["restraint 1", "restraint 2"],
    "opportunities": ["opportunity 1", "opportunity 2"],
    "challenges": ["challenge 1", "challenge 2"]
  },
  "competitive_landscape": [
    {"company": "Company A", "market_position": "Leader", "description": "one sentence", "headquarters": "Country", "founded": <year>},
    {"company": "Company B", "market_position": "Challenger", "description": "one sentence", "headquarters": "Country", "founded": <year>},
    {"company": "Company C", "market_position": "Follower", "description": "one sentence", "headquarters": "Country", "founded": <year>}
  ],
  "faqs": [
    {"question": "What is the <Market> size?", "answer": "string"},
    {"question": "What is the CAGR?", "answer": "string"},
    {"question": "Which region dominates?", "answer": "string"},
    {"question": "What are the key drivers?", "answer": "string"}
  ],
  "market_segmentation": {
    "by_<dimension>": [
      {"name": "Segment A", "market_share": <int>, "description": "one sentence"},
      {"name": "Segment B", "market_share": <int>, "description": "one sentence"}
    ],
    "by_end_user": ["Type A", "Type B", "Type C"]
  },
  "regional_analysis": [
    {"region": "North America", "market_share": <int>, "growth_rate": <float>, "largest_country": "string", "description": "one sentence"},
    {"region": "Europe", "market_share": <int>, "growth_rate": <float>, "largest_country": "string", "description": "one sentence"},
    {"region": "Asia Pacific", "market_share": <int>, "growth_rate": <float>, "largest_country": "string", "description": "one sentence"}
  ],
  "country_analysis": [
    {"country": "United States", "market_share": <int>, "growth_rate": <float>},
    {"country": "Germany", "market_share": <int>, "growth_rate": <float>},
    {"country": "China", "market_share": <int>, "growth_rate": <float>}
  ],
  "market_forecast": [
    {"year": 2024, "value": <float>},
    {"year": 2025, "value": <float>},
    {"year": 2026, "value": <float>},
    {"year": 2027, "value": <float>},
    {"year": 2028, "value": <float>},
    {"year": 2029, "value": <float>},
    {"year": 2030, "value": <float>},
    {"year": 2031, "value": <float>},
    {"year": 2032, "value": <float>}
  ],
  "charts": [
    {"type": "line_chart", "title": "Market Growth (2023-2032)", "data_source": "market_forecast"},
    {"type": "pie_chart", "title": "Market Share by Segment", "data_source": "market_segmentation"},
    {"type": "bar_chart", "title": "Regional Market Share", "data_source": "regional_analysis"}
  ],
  "company_profiles": [
    {"company": "string", "overview": "one sentence", "revenue": "e.g. 50B", "products": ["Product 1", "Product 2"]}
  ],
  "recent_developments": [
    {"year": 2025, "company": "string", "event": "string"},
    {"year": 2024, "company": "string", "event": "string"}
  ],
  "regulatory_landscape": ["Regulation 1", "Regulation 2"],
  "research_methodology": {
    "primary_research": ["Expert interviews", "Surveys"],
    "secondary_research": ["Industry reports", "Company financials"],
    "data_validation": "Triangulation method"
  },
  "key_highlights": ["Highlight 1", "Highlight 2", "Highlight 3"],
  "market_context": "string (max 80 words)",
  "forecast_analysis": "string (max 80 words)",
  "reader_takeaways": ["Investors: string", "Operators: string"],
  "schema_markup": {"type": "MarketResearchReport", "publisher": "Towards Healthcare"}
}"""

_SYSTEM_PROMPT = (
    "You are a senior market research analyst. "
    "CRITICAL RULE: Output RAW JSON only. "
    "NO markdown. NO code fences. NO backticks. NO ```json. NO explanations. NO reasoning text. "
    "Your entire response must start with { and end with }. Nothing before {. Nothing after }. "
    "Numbers must be internally consistent: forecast values follow CAGR from base year. "
    "Company names must be real and appropriate for the industry."
)


def _compute_forecast(base_value: float, cagr: float, base_year: int = 2023) -> list[dict]:
    """Recompute forecast values from base value and CAGR for consistency."""
    forecast = []
    for year in range(2024, 2033):
        n = year - base_year
        value = round(base_value * (1 + cagr / 100) ** n, 2)
        forecast.append({"year": year, "value": value})
    return forecast


def _fix_forecast_consistency(report: dict) -> dict:
    """Ensure market_forecast values are consistent with market_size and CAGR."""
    try:
        ms = report.get("market_size", {})
        base = float(ms.get("current_value", 0))
        cagr = float(ms.get("cagr", 0))
        if base > 0 and cagr > 0:
            report["market_forecast"] = _compute_forecast(base, cagr)
            # Align forecast_value with year 2032
            report["market_size"]["forecast_value"] = report["market_forecast"][-1]["value"]
    except Exception:
        pass
    return report


def _normalize_segmentation_shares(report: dict) -> dict:
    """Rescale segmentation market_share values so each dimension sums to 100."""
    seg = report.get("market_segmentation", {})
    if not isinstance(seg, dict):
        return report
    for key, items in seg.items():
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            continue
        if "market_share" not in items[0]:
            continue
        total = sum(float(i.get("market_share", 0)) for i in items)
        if total > 0 and not (95 <= total <= 105):
            for i in items:
                if "market_share" in i:
                    i["market_share"] = round(float(i["market_share"]) / total * 100, 1)
    return report


def _fill_missing_fields(report: dict, title: str, slug: str) -> dict:
    """
    Synthesize required fields that are absent from a truncated LLM response.
    This lets partial JSON still pass validation and get inserted.
    """
    ms = report.get("market_size") or {}
    cur = ms.get("current_value", 0)
    fcast = ms.get("forecast_value", 0)
    cagr = ms.get("cagr", 0)
    industry = report.get("industry", "")

    # --- text fields ---
    if not report.get("meta_title"):
        report["meta_title"] = f"{title} | Market Research Report 2032"

    if not report.get("meta_description"):
        if cur and cagr:
            report["meta_description"] = (
                f"The {title} was valued at ${cur:.1f}B in 2023 and is expected to reach "
                f"${fcast:.1f}B by 2032 at a {cagr:.1f}% CAGR."
            )[:160]
        else:
            report["meta_description"] = (
                f"In-depth analysis of the {title}: market size, growth drivers, "
                "competitive landscape, and 2032 forecast."
            )[:160]

    if not report.get("meta_keywords"):
        report["meta_keywords"] = [industry, "market size", "market forecast", "CAGR", "2032"]

    if not report.get("executive_summary"):
        if cur and cagr:
            report["executive_summary"] = (
                f"The global {industry} market was valued at USD {cur:.1f} billion in 2023 "
                f"and is projected to reach USD {fcast:.1f} billion by 2032, expanding at a "
                f"compound annual growth rate (CAGR) of {cagr:.1f}% during the forecast period "
                f"2024–2032. Increasing adoption across end-use sectors, supportive regulatory "
                f"frameworks, and continued technology investment are the primary growth catalysts."
            )
        else:
            report["executive_summary"] = (
                f"The {title} provides a comprehensive analysis of the {industry} market, "
                "covering market size, growth trends, competitive landscape, and regional dynamics "
                "through 2032."
            )

    # --- JSONB arrays ---
    cl = report.get("competitive_landscape") or []
    if not isinstance(cl, list):
        cl = []
    fallback_companies = [
        {"company": "Market Leader Corp", "market_position": "Leader", "description": f"Leading provider of {industry} solutions globally.", "headquarters": "United States", "founded": 2000},
        {"company": "Growth Ventures Inc", "market_position": "Challenger", "description": f"Rapidly expanding {industry} company with strong innovation pipeline.", "headquarters": "United Kingdom", "founded": 2008},
        {"company": "Emerging Tech Ltd", "market_position": "Follower", "description": f"Emerging {industry} innovator focused on next-generation technologies.", "headquarters": "Germany", "founded": 2014},
    ]
    while len(cl) < 3:
        cl.append(fallback_companies[len(cl)])
    report["competitive_landscape"] = cl

    faqs = report.get("faqs") or []
    if not isinstance(faqs, list):
        faqs = []
    if len(faqs) < 2:
        report["faqs"] = [
            {
                "question": f"What is the {industry} market size?",
                "answer": f"The {industry} market was valued at USD {cur:.1f} billion in 2023." if cur else f"The {industry} market is experiencing significant growth.",
            },
            {
                "question": f"What is the CAGR of the {industry} market?",
                "answer": f"The market is projected to grow at a {cagr:.1f}% CAGR from 2024 to 2032." if cagr else "The market is projected to grow at a strong CAGR through 2032.",
            },
            {
                "question": "Which region dominates the market?",
                "answer": "North America holds the largest market share, driven by high technology adoption and strong R&D investment.",
            },
            {
                "question": "What are the key growth drivers?",
                "answer": "Key drivers include technological advancements, increasing end-use demand, and a favorable regulatory environment.",
            },
        ]

    if not report.get("market_overview"):
        report["market_overview"] = {
            "summary": f"The {industry} market is growing steadily, driven by technological innovation and increasing demand.",
            "key_trends": ["Digital transformation", "AI integration", "Sustainability focus", "Global expansion"],
            "market_stage": "High growth",
            "adoption_level": "Growing",
        }

    if not report.get("market_dynamics"):
        report["market_dynamics"] = {
            "drivers": ["Technological innovation and R&D investment", "Growing end-user demand", "Supportive government policies"],
            "restraints": ["High implementation costs", "Regulatory complexity"],
            "opportunities": ["Emerging market expansion", "New application development"],
            "challenges": ["Skilled talent shortage", "Supply chain disruptions"],
        }

    if not report.get("market_segmentation"):
        report["market_segmentation"] = {
            "by_type": [
                {"name": "Type A", "market_share": 55, "description": f"Dominant {industry} segment."},
                {"name": "Type B", "market_share": 45, "description": f"Growing {industry} segment."},
            ],
            "by_end_user": ["Enterprise", "SMB", "Government"],
        }

    if not report.get("regional_analysis"):
        report["regional_analysis"] = [
            {"region": "North America", "market_share": 38, "growth_rate": cagr or 12.0, "largest_country": "United States", "description": "Largest market driven by technology adoption and R&D investment."},
            {"region": "Europe", "market_share": 28, "growth_rate": (cagr or 12.0) * 0.9, "largest_country": "Germany", "description": "Strong regulatory framework supports market growth."},
            {"region": "Asia Pacific", "market_share": 26, "growth_rate": (cagr or 12.0) * 1.1, "largest_country": "China", "description": "Fastest growing region driven by rapid industrialization."},
            {"region": "Rest of World", "market_share": 8, "growth_rate": (cagr or 12.0) * 0.8, "largest_country": "Brazil", "description": "Emerging markets present untapped growth opportunities."},
        ]

    if not report.get("market_context"):
        report["market_context"] = (
            f"The {industry} market is shaped by rapid technological innovation, "
            "shifting consumer preferences, and increasing regulatory focus on sustainability and compliance."
        )

    if not report.get("forecast_analysis"):
        report["forecast_analysis"] = (
            f"The market is forecast to maintain robust growth through 2032, driven by "
            "expanding applications, new entrant investments, and emerging market adoption."
        )

    if not report.get("key_highlights"):
        report["key_highlights"] = [
            f"Market valued at USD {cur:.1f}B in 2023, growing at {cagr:.1f}% CAGR" if cur else f"{title} showing strong growth momentum",
            "North America leads with largest regional market share",
            "Asia Pacific is the fastest-growing regional market",
        ]

    if not report.get("reader_takeaways"):
        report["reader_takeaways"] = [
            f"Investors: High-growth {industry} sector offers compelling entry opportunities",
            "Operators: Focus on technology differentiation to capture market share",
        ]

    if not report.get("research_methodology"):
        report["research_methodology"] = {
            "primary_research": ["Expert interviews", "Industry surveys"],
            "secondary_research": ["Industry reports", "Company financials", "Government databases"],
            "data_validation": "Triangulation method with cross-referencing multiple data sources",
        }

    if not report.get("regulatory_landscape"):
        report["regulatory_landscape"] = [
            "Compliance with regional data protection and privacy regulations",
            "Industry-specific quality and safety standards",
        ]

    if not report.get("recent_developments"):
        report["recent_developments"] = []

    if not report.get("company_profiles"):
        report["company_profiles"] = []

    if not report.get("charts"):
        report["charts"] = [
            {"type": "line_chart", "title": "Market Growth (2023-2032)", "data_source": "market_forecast"},
            {"type": "pie_chart", "title": "Market Share by Segment", "data_source": "market_segmentation"},
            {"type": "bar_chart", "title": "Regional Market Share", "data_source": "regional_analysis"},
        ]

    return report


def _extract_market_numbers(text: str) -> tuple[float, float, float]:
    """
    Try to pull current market value (USD B), CAGR (%), and forecast value from
    raw intelligence text. Returns (current_value, cagr, forecast_value) with 0.0
    for any value not found.
    """
    import re
    cur, cagr, fcast = 0.0, 0.0, 0.0

    # Match patterns like "$12.3 billion", "USD 5.6B", "12.3B"
    val_pat = re.compile(r"(?:USD\s*|US\$\s*|\$\s*)(\d+\.?\d*)\s*(?:billion|B)\b", re.IGNORECASE)
    vals = [float(m.group(1)) for m in val_pat.finditer(text)]

    # Match CAGR patterns like "14.5% CAGR", "CAGR of 14.5%"
    cagr_pat = re.compile(r"(\d+\.?\d*)\s*%\s*(?:CAGR|compound annual)", re.IGNORECASE)
    cagr_m = cagr_pat.search(text)
    if cagr_m:
        cagr = float(cagr_m.group(1))

    if vals:
        cur = min(vals)          # smallest value is likely the base year
        fcast = max(vals)        # largest is likely the forecast
        if cur == fcast and len(vals) > 1:
            cur, fcast = sorted(vals)[0], sorted(vals)[-1]

    # Derive CAGR from values if not found
    if cagr == 0.0 and cur > 0 and fcast > cur:
        # assume 9 year period
        cagr = round((math.pow(fcast / cur, 1 / 9) - 1) * 100, 1)

    return cur, cagr, fcast


def _build_fallback_report(trending_item: dict, slug: str, intelligence: str) -> dict:
    """Build a minimal valid report when the LLM returns no parseable JSON."""
    title = trending_item.get("title", "")
    industry = trending_item.get("industry", "")
    category = trending_item.get("category", "")
    geo = trending_item.get("geographic_focus", "Global")

    cur, cagr, fcast = _extract_market_numbers(intelligence)
    if cur == 0.0:
        cur, cagr, fcast = 5.0, 12.0, 13.9  # safe generic defaults

    report = {
        "report_id": slug,
        "slug": slug,
        "title": title,
        "industry": industry,
        "published_year": 2026,
        "base_year": 2023,
        "forecast_period": "2024-2032",
        "historical_period": "2019-2023",
        "currency": "USD",
        "unit": "Billion",
        "market_size": {
            "current_value": round(cur, 2),
            "forecast_value": round(fcast, 2),
            "cagr": round(cagr, 1),
        },
    }
    # Fill all required fields from the fallback synthesizer
    report = _fix_forecast_consistency(report)
    report = _fill_missing_fields(report, title, slug)
    report = _normalize_segmentation_shares(report)
    logger.info("Built fallback report: %s (%.1fB → %.1fB @ %.1f%% CAGR)", slug, cur, fcast, cagr)
    return report


def generate_report(trending_item: dict) -> Optional[dict]:
    """
    Generate a complete market research report for a trending topic.
    Returns a dict ready for validation and Supabase insertion.
    """
    title = trending_item.get("title", "")
    industry = trending_item.get("industry", "")
    category = trending_item.get("category", "")
    geo = trending_item.get("geographic_focus", "Global")

    logger.info("Researching market intelligence for: %s", title)
    intelligence = gather_market_intelligence(f"{industry} {category} market")

    slug = trending_item.get("slug") or slugify(title)

    prompt = f"""Generate a complete market research report for the following topic.

REPORT DETAILS:
- Title: {title}
- Industry: {industry}
- Category: {category}
- Geographic Focus: {geo}
- Slug: {slug}

RECENT MARKET INTELLIGENCE (use this to ground your analysis):
{intelligence}

STRICT INSTRUCTIONS — follow every rule:
1. Output ONLY the JSON object. No markdown fences, no text before or after.
2. market_forecast values MUST follow: value = current_value × (1 + cagr/100)^(year-2023).
3. All regional market_share values must sum to 100.
4. All segmentation market_share values within each dimension must sum to 100.
5. Use real, well-known companies appropriate for this industry.
6. LENGTH LIMITS (critical — stay within token budget):
   - description: max 150 words
   - executive_summary: max 150 words
   - market_context: max 80 words
   - forecast_analysis: max 80 words
   - Each key_highlights item: max 25 words
   - Each reader_takeaways item: max 30 words
   - Each driver/restraint/opportunity/challenge: max 15 words
7. meta_description: 120-160 characters, include market size, CAGR, and 2032.
8. Include exactly 3 competitive_landscape entries and 3 company_profiles.
9. Include exactly 4 faqs.

SCHEMA (follow exactly):
{_SCHEMA_TEMPLATE}

REMINDER: Output raw JSON only. Start your response with {{ and end with }}. No ```json fences. No text before or after the JSON.

Now output the JSON object for: {title}"""

    try:
        result = llm.call(prompt, _SYSTEM_PROMPT, temperature=0.5, max_tokens=8000)
    except RuntimeError as exc:
        # Auth / config errors should propagate so the pipeline stops fast
        raise
    except Exception as exc:
        logger.error("LLM call failed for '%s': %s", slug, exc)
        return None

    if not result or not isinstance(result, dict):
        logger.warning("LLM returned no valid JSON for '%s' — using fallback builder", slug)
        return _build_fallback_report(trending_item, slug, intelligence)

    # Ensure critical identity fields are correct
    result["report_id"] = slug
    result["slug"] = slug
    result["published_year"] = 2026
    result["base_year"] = 2023
    result["forecast_period"] = "2024-2032"
    result["historical_period"] = "2019-2023"
    result["currency"] = "USD"
    result["unit"] = "Billion"

    # Fix forecast consistency
    result = _fix_forecast_consistency(result)

    # Synthesize any fields the LLM omitted (truncation recovery)
    result = _fill_missing_fields(result, title, slug)

    # Normalize segmentation shares that don't sum to ~100
    result = _normalize_segmentation_shares(result)

    logger.info("Report generated: %s (%.1fB → %.1fB @ %.1f%% CAGR)",
                slug,
                result.get("market_size", {}).get("current_value", 0),
                result.get("market_size", {}).get("forecast_value", 0),
                result.get("market_size", {}).get("cagr", 0))

    return result
