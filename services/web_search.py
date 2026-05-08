"""
Market intelligence via OpenRouter web-search plugin.
No external search library required — the model searches the web directly.
"""
import logging
from services import openrouter_client as llm

logger = logging.getLogger(__name__)

_RESEARCH_SYSTEM = (
    "You are a market research analyst. "
    "Provide concise, factual market intelligence with specific numbers, company names, "
    "and recent developments. Focus on data points useful for writing a market research report."
)


def gather_market_intelligence(topic: str) -> str:
    """
    Use OpenRouter's web-search plugin to retrieve live market intelligence for a topic.
    Returns a text summary for use as LLM context in report generation.
    Falls back to training-knowledge if web search fails.
    """
    prompt = (
        f"Search the web and summarize key market intelligence for: {topic}\n\n"
        "Include:\n"
        "- Current market size (USD billions) and CAGR\n"
        "- Top 5-6 companies and their approximate market positions\n"
        "- 3-4 key growth drivers\n"
        "- Regional breakdown (North America / Europe / Asia Pacific shares)\n"
        "- 2-3 recent significant developments (funding, acquisitions, product launches) from 2024-2026\n"
        "- Key regulatory or policy developments\n\n"
        "Be specific with numbers. If you cannot find a figure, estimate based on comparable markets."
    )

    # Try with web search plugin first
    result = llm.call_text(
        prompt,
        system_prompt=_RESEARCH_SYSTEM,
        temperature=0.3,
        max_tokens=1500,
        web_search=True,
    )

    if result:
        logger.info("Web intelligence gathered for: %s (%d chars)", topic, len(result))
        return result

    # Fallback: ask without web search (uses training knowledge)
    logger.warning("Web search failed for '%s' — falling back to training knowledge", topic)
    result = llm.call_text(
        f"From your training knowledge, summarize key market intelligence for: {topic}\n"
        "Include market size, CAGR, top companies, regional breakdown, and recent trends.",
        system_prompt=_RESEARCH_SYSTEM,
        temperature=0.4,
        max_tokens=1000,
        web_search=False,
    )

    return result or f"No market intelligence available for: {topic}"
