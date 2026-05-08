"""One-time script to patch existing Supabase records with correct category mappings."""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

from supabase import create_client
from services.supabase_service import _map_industry_to_category

client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
rows = client.table("neograph_reports").select("slug,industry").execute().data or []
print(f"Found {len(rows)} records")

for r in rows:
    original = r["industry"] or ""
    mapped = _map_industry_to_category(original)
    if mapped != original:
        client.table("neograph_reports").update({"industry": mapped}).eq("slug", r["slug"]).execute()
        print(f"  UPDATED {r['slug']}: '{original}' -> '{mapped}'")
    else:
        print(f"  OK      {r['slug']}: '{original}'")

print("Done.")
