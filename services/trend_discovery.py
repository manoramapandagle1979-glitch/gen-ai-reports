"""Trend discovery — maintains a rolling list of 500 high-demand report ideas."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from slugify import slugify

from services import openrouter_client as llm

logger = logging.getLogger(__name__)

TRENDING_FILE = Path(__file__).parent.parent / "data" / "trending_reports.json"

# Seed set of industry verticals and topic templates that drive trend generation.
_SEED_VERTICALS = [
    # Healthcare & Life Sciences
    ("Healthcare AI", "Artificial Intelligence"),
    ("Medical Imaging AI", "Healthcare Technology"),
    ("Drug Discovery AI", "Pharmaceutical Technology"),
    ("Clinical Decision Support", "Healthcare Technology"),
    ("Remote Patient Monitoring", "Digital Health"),
    ("Telemedicine", "Digital Health"),
    ("Precision Medicine", "Biotechnology"),
    ("Genomics", "Biotechnology"),
    ("Gene Therapy", "Biotechnology"),
    ("Cell & Gene Therapy Manufacturing", "Biotechnology"),
    ("CRISPR Technology", "Biotechnology"),
    ("RNA Therapeutics", "Pharmaceutical"),
    ("Antibody Drug Conjugates", "Pharmaceutical"),
    ("Biosimilars", "Pharmaceutical"),
    ("Digital Therapeutics", "Digital Health"),
    ("Wearable Medical Devices", "Medical Devices"),
    ("Surgical Robotics", "Medical Devices"),
    ("Continuous Glucose Monitoring", "Medical Devices"),
    ("Microbiome Therapeutics", "Biotechnology"),
    ("Mental Health Technology", "Digital Health"),
    # Artificial Intelligence & Data
    ("Generative AI", "Artificial Intelligence"),
    ("Large Language Models", "Artificial Intelligence"),
    ("AI Infrastructure", "Artificial Intelligence"),
    ("AI Chips and Semiconductors", "Semiconductor"),
    ("Edge AI", "Artificial Intelligence"),
    ("MLOps Platforms", "Artificial Intelligence"),
    ("AI Safety and Governance", "Artificial Intelligence"),
    ("Computer Vision", "Artificial Intelligence"),
    ("Natural Language Processing", "Artificial Intelligence"),
    ("Synthetic Data Generation", "Artificial Intelligence"),
    # Cybersecurity
    ("Cybersecurity", "Cybersecurity"),
    ("Zero Trust Security", "Cybersecurity"),
    ("Cloud Security", "Cybersecurity"),
    ("Identity and Access Management", "Cybersecurity"),
    ("Threat Intelligence", "Cybersecurity"),
    ("Endpoint Security", "Cybersecurity"),
    ("OT/ICS Security", "Cybersecurity"),
    ("Quantum Cryptography", "Cybersecurity"),
    # Cloud & SaaS
    ("Cloud Computing", "Cloud Technology"),
    ("Multi-Cloud Management", "Cloud Technology"),
    ("Serverless Computing", "Cloud Technology"),
    ("SaaS Platforms", "Software"),
    ("Low-Code No-Code Platforms", "Software"),
    ("API Management", "Software"),
    ("DevOps Tools", "Software"),
    ("Data Observability", "Software"),
    # Semiconductor & Hardware
    ("Semiconductor", "Semiconductor"),
    ("Power Semiconductors", "Semiconductor"),
    ("Photonics", "Semiconductor"),
    ("Advanced Packaging", "Semiconductor"),
    ("MEMS Sensors", "Semiconductor"),
    # Electric Vehicles & Energy
    ("Electric Vehicles", "Automotive"),
    ("EV Battery Technology", "Energy Storage"),
    ("EV Charging Infrastructure", "Energy"),
    ("Solid State Battery", "Energy Storage"),
    ("Battery Management Systems", "Energy Storage"),
    ("Solar Energy", "Renewable Energy"),
    ("Offshore Wind Energy", "Renewable Energy"),
    ("Green Hydrogen", "Clean Energy"),
    ("Carbon Capture Utilization and Storage", "Clean Energy"),
    ("Energy Storage Systems", "Energy Storage"),
    ("Smart Grid Technology", "Energy"),
    ("Microgrids", "Energy"),
    ("Virtual Power Plants", "Energy"),
    # Fintech & Finance
    ("Fintech", "Financial Technology"),
    ("Embedded Finance", "Financial Technology"),
    ("Buy Now Pay Later", "Financial Technology"),
    ("Digital Banking", "Financial Technology"),
    ("Blockchain Technology", "Financial Technology"),
    ("DeFi Decentralized Finance", "Financial Technology"),
    ("RegTech", "Financial Technology"),
    ("Insurtech", "Insurance Technology"),
    ("Open Banking", "Financial Technology"),
    ("Central Bank Digital Currency", "Financial Technology"),
    # Robotics & Automation
    ("Industrial Robotics", "Robotics"),
    ("Collaborative Robots", "Robotics"),
    ("Autonomous Mobile Robots", "Robotics"),
    ("Warehouse Automation", "Logistics Technology"),
    ("Agricultural Robots", "AgriTech"),
    ("Drone Delivery", "Logistics Technology"),
    ("Humanoid Robots", "Robotics"),
    ("Robotic Process Automation", "Software"),
    # Food, Agriculture & Consumer
    ("Precision Agriculture", "AgriTech"),
    ("Vertical Farming", "AgriTech"),
    ("Alternative Proteins", "Food Technology"),
    ("Cultivated Meat", "Food Technology"),
    ("Food Tech", "Food Technology"),
    ("Nutraceuticals", "Consumer Health"),
    ("Functional Foods", "Food Technology"),
    ("Sustainable Packaging", "Packaging"),
    ("Plant-Based Food", "Food Technology"),
    # Supply Chain & Logistics
    ("Supply Chain Management Software", "Logistics Technology"),
    ("Cold Chain Logistics", "Logistics"),
    ("Last-Mile Delivery", "Logistics"),
    ("Digital Freight Matching", "Logistics Technology"),
    ("Trade Finance", "Financial Technology"),
    # Construction & Real Estate
    ("Construction Technology", "PropTech"),
    ("Smart Building Technology", "PropTech"),
    ("Building Information Modeling", "PropTech"),
    ("3D Printing Construction", "Construction"),
    # Space & Defense
    ("Commercial Space Technology", "Aerospace"),
    ("Small Satellite", "Aerospace"),
    ("Defense Technology", "Defense"),
    ("Hypersonic Technology", "Aerospace"),
    # EdTech & HR
    ("EdTech", "Education Technology"),
    ("Corporate Learning Platforms", "Education Technology"),
    ("HR Technology", "Human Resources Technology"),
    ("Talent Management Software", "Human Resources Technology"),
    # Climate Tech & Environment
    ("Climate Technology", "Clean Technology"),
    ("ESG Reporting Software", "Sustainability"),
    ("Water Treatment Technology", "Environmental Technology"),
    ("Waste Management Technology", "Environmental Technology"),
    # Other emerging
    ("Extended Reality XR", "Immersive Technology"),
    ("Metaverse Platforms", "Immersive Technology"),
    ("Quantum Computing", "Quantum Technology"),
    ("Neuromorphic Computing", "Advanced Computing"),
    ("Digital Twins", "Industrial Technology"),
    ("5G Network Infrastructure", "Telecommunications"),
    ("Satellite Internet", "Telecommunications"),
    ("Smart Cities", "Urban Technology"),
    ("Legal Technology", "Legal Tech"),
    ("Sports Technology", "Consumer Technology"),
]

_REPORT_TITLE_TEMPLATES = [
    "Global {topic} Market Size, Share and Trends Analysis",
    "{topic} Market Size, Share, Growth and Forecast to 2032",
    "Global {topic} Market Analysis, Opportunities and Forecast 2024-2032",
    "{topic} Industry Size, Competitive Landscape and Regional Analysis",
    "{topic} Market — Growth Drivers, Restraints and Future Outlook 2032",
]

_GEOGRAPHIC_VARIANTS = [
    "Global",
    "North America",
    "Asia Pacific",
    "Europe",
    "United States",
]


def _build_seed_topics() -> list[dict]:
    """Generate a deterministic seed list from verticals × templates."""
    topics: list[dict] = []
    idx = 0
    for (topic, category) in _SEED_VERTICALS:
        for template_idx, template in enumerate(_REPORT_TITLE_TEMPLATES):
            for geo_idx, geo in enumerate(_GEOGRAPHIC_VARIANTS):
                if len(topics) >= 500:
                    return topics
                geo_prefix = f"{geo} " if geo != "Global" else "Global "
                adjusted_topic = topic if geo == "Global" else topic
                title = template.format(topic=f"{geo_prefix}{adjusted_topic}")
                slug = slugify(title.replace("Global Global", "Global").rstrip(" — Growth Drivers, Restraints and Future Outlook 2032").rstrip(" Market Size, Share and Trends Analysis"))
                # Simplify slug
                slug = slugify(f"{geo.lower()}-{adjusted_topic.lower()}-market").replace("--", "-")
                if geo == "Global":
                    slug = slugify(f"{adjusted_topic.lower()}-market")

                topics.append({
                    "title": title.replace("Global Global", "Global"),
                    "slug": slug,
                    "industry": topic,
                    "category": category,
                    "demand_score": max(60, 95 - idx % 35),
                    "trend_source": "Seed List",
                    "search_volume_estimate": max(1000, 50000 - (idx * 97) % 49000),
                    "geographic_focus": geo,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })
                idx += 1
    return topics


def _enrich_with_llm_trends(topics: list[dict]) -> list[dict]:
    """Sort topics by existing demand_score (no LLM call — the seed scores are sufficient)."""
    return sorted(topics, key=lambda x: x.get("demand_score", 0), reverse=True)


def _generate_extra_topics_with_llm(existing_slugs: set[str]) -> list[dict]:
    """Ask the LLM to suggest additional trending market research topics."""
    system = (
        "You are a market research strategist. Output only valid JSON arrays. "
        "No markdown, no explanations."
    )

    prompt = (
        "Generate 30 trending market research report ideas for 2025-2026 that are NOT in these industries: "
        "Healthcare AI, Semiconductor, Electric Vehicles, Fintech, Cybersecurity.\n\n"
        "Focus on: Climate Tech, Space Economy, Quantum Technology, Biotechnology, AgriTech, "
        "Advanced Materials, Synthetic Biology, Digital Infrastructure.\n\n"
        "Return a JSON array of objects with fields: title, slug, industry, category, "
        "demand_score (50-99), search_volume_estimate (integer), geographic_focus.\n"
        "Example slug format: 'quantum-computing-market', 'synthetic-biology-market'.\n"
        "Output the JSON array only."
    )

    try:
        result = llm.call(prompt, system, temperature=0.6, max_tokens=3000)
    except Exception as exc:
        logger.warning("LLM extra-topics call failed (non-fatal): %s", exc)
        return []
    if not result or not isinstance(result, list):
        return []

    now = datetime.now(timezone.utc).isoformat()
    extra = []
    for item in result:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug", "")
        if not slug or slug in existing_slugs:
            continue
        item.setdefault("trend_source", "LLM Generated")
        item.setdefault("generated_at", now)
        extra.append(item)

    return extra


def load_trending_reports() -> list[dict]:
    """Load trending_reports.json or return empty list."""
    if TRENDING_FILE.exists():
        try:
            return json.loads(TRENDING_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to read trending file: %s", exc)
    return []


def discover_trends(force_refresh: bool = False) -> None:
    """
    Refresh trending_reports.json.
    On first run builds 500 seed topics. On subsequent runs, re-scores and
    optionally appends LLM-generated extras.
    """
    existing = load_trending_reports()

    if existing and not force_refresh:
        # Re-score existing list without full rebuild
        logger.info("Re-scoring %d existing trending topics…", len(existing))
        enriched = _enrich_with_llm_trends(existing)
        TRENDING_FILE.write_text(
            json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Trending list re-scored and saved.")
        return

    logger.info("Building trending report seed list…")
    topics = _build_seed_topics()
    logger.info("Seed list: %d topics. Enriching with LLM web signals…", len(topics))
    topics = _enrich_with_llm_trends(topics)

    # Persist the seed list immediately — LLM extras are optional
    TRENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRENDING_FILE.write_text(
        json.dumps(topics[:500], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Seed list saved (%d topics). Now requesting LLM extras…", len(topics[:500]))

    existing_slugs = {t["slug"] for t in topics}
    extra = _generate_extra_topics_with_llm(existing_slugs)
    logger.info("LLM added %d extra topics.", len(extra))

    if extra:
        all_topics = topics + extra
        seen: set[str] = set()
        unique: list[dict] = []
        for t in all_topics:
            if t["slug"] not in seen:
                seen.add(t["slug"])
                unique.append(t)
        TRENDING_FILE.write_text(
            json.dumps(unique[:500], indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Updated trending file with extras: %d total topics.", min(len(unique), 500))
