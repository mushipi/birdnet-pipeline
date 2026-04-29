import json
import sqlite3
import time
import requests
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "bird_calls.db"
CACHE_PATH = BASE_DIR / "species_cache.json"

def load_cache():
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def fetch_inaturalist_metadata(common_name):
    """iNaturalist Search API で種情報を検索し、和名と学名を取得するわ。"""
    print(f"  [iNat] Searching: {common_name}...")
    try:
        # 共通名で検索 (English common name -> iNat taxon)
        url = "https://api.inaturalist.org/v1/taxa"
        params = {"q": common_name, "locale": "ja", "is_active": "true", "per_page": 1}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        
        if not results:
            return None

        taxon = results[0]
        metadata = {
            "scientific_name": taxon.get("name"),
            "species_jp": taxon.get("preferred_common_name"),
            "taxon_id": taxon.get("id"),
            "icon_url": taxon.get("default_photo", {}).get("square_url")
        }
        return metadata
    except Exception as e:
        print(f"  [Error] {e}")
        return None

def update():
    cache = load_cache()
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        # species (English name) があるが scientific_name または species_jp が NULL のものを対象にする
        rows = conn.execute("SELECT DISTINCT species FROM detections WHERE species IS NOT NULL AND (scientific_name IS NULL OR species_jp IS NULL)").fetchall()
        
        if not rows:
            print("更新対象のレコードはないわ。")
            return

        print(f"{len(rows)} 種のメタデータを取得するわね。")
        
        for row in rows:
            common_name = row["species"]
            
            if common_name in cache:
                meta = cache[common_name]
                print(f"  [Cache] {common_name} -> {meta.get('species_jp')}")
            else:
                meta = fetch_inaturalist_metadata(common_name)
                if meta:
                    cache[common_name] = meta
                    save_cache(cache)
                    # API への負荷軽減
                    time.sleep(1)
                else:
                    print(f"  [Warn] {common_name} の情報を取得できなかったわ。")
                    continue
            
            # DB 更新
            conn.execute(
                "UPDATE detections SET scientific_name = ?, species_jp = ? WHERE species = ?",
                (meta.get("scientific_name"), meta.get("species_jp"), common_name)
            )
            print(f"  [DB] Updated {common_name}")
        
        conn.commit()
    
    print("完了！")

if __name__ == "__main__":
    update()
