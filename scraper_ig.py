import os
import json
from apify_client import ApifyClient
from dotenv import load_dotenv

# 1. Load environment variables securely
load_dotenv()
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

if not APIFY_TOKEN:
    raise ValueError("Error: APIFY_TOKEN not found in .env file")

client = ApifyClient(APIFY_TOKEN)

# 2. Configuration: Target the real Colombian Consulate in Paris
run_input = {
    "usernames": ["consuladocolparis"], 
    "resultsType": "following", # We want the edges (who they follow)
    "maxItems": 100 # Hard limit for FinOps
}

print("🚀 Launching spider on Apify... (This may take 1-2 minutes)")

# 3. Run the Actor in the cloud
# REEMPLAZA ESTO por el ID del scraper de Followings que encuentres en Apify Store
ACTOR_ID = "apify/instagram-profile-scraper" 
run = client.actor(ACTOR_ID).call(run_input=run_input)

print("📥 Downloading network dataset...")

dataset_id = run.default_dataset_id
dataset_items = client.dataset(dataset_id).list_items().items

# 4. FinOps: Cost tracking for this query
run_cost = run.usage_total_usd or 0.0
print(f"💰 FINOPS - Cost of this query: ${run_cost:.4f} USD")

# 5. Save network data to local disk
os.makedirs("data_raw", exist_ok=True)
filepath = "data_raw/consuladocolparis_network.json"

with open(filepath, "w", encoding="utf-8") as f:
    json.dump(dataset_items, f, ensure_ascii=False, indent=4)

print(f"✅ Success! {len(dataset_items)} connections saved in '{filepath}'")