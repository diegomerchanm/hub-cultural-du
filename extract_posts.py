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

# 2. Target configuration (Posts timeline)
TARGET_USERNAME = "consuladocolparis"
run_input = {
    "username": [TARGET_USERNAME],
    "resultsLimit": 50 # Hard limit to capture recent history & control costs
}

print(f"🚀 Launching POSTS timeline spider for @{TARGET_USERNAME}...")

# 3. Execute Post-specific Actor in the Apify Cloud
# Using the dedicated post scraper for granular feed extraction
ACTOR_ID = "apify/instagram-post-scraper"
run = client.actor(ACTOR_ID).call(run_input=run_input)

print("📥 Downloading timeline dataset...")

# 4. Fetch the dataset using dot notation
dataset_id = run.default_dataset_id
dataset_items = client.dataset(dataset_id).list_items().items

# 5. FinOps: Log query cost
run_cost = run.usage_total_usd or 0.0
print(f"💰 FINOPS - Cost of this timeline query: ${run_cost:.4f} USD")

# 6. Save timeline payload to local disk
os.makedirs("data_raw", exist_ok=True)
filepath = f"data_raw/posts_{TARGET_USERNAME}.json"

with open(filepath, "w", encoding="utf-8") as f:
    json.dump(dataset_items, f, ensure_ascii=False, indent=4)

print(f"✅ Success! {len(dataset_items)} posts saved in '{filepath}'")