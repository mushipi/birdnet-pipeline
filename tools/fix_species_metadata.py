"""
species_cache.json と DB の scientific_name / species_jp を正しい値に修正する。
update_metadata.py（iNaturalist 検索）が不正確な結果を入れたことへの対応。
"""

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_PATH = BASE_DIR / "species_cache.json"

with open(BASE_DIR / "settings.json", encoding="utf-8") as f:
    DB_PATH = json.load(f)["db_path"]

# 正しいマッピング（BirdNET common_name → 学名・和名）
# 主要な確定種のみ手動で修正
CORRECTIONS = {
    "Carrion Crow":                   ("Corvus corone",         "ハシボソガラス"),
    "Red-billed Leiothrix":           ("Leiothrix lutea",       "ソウシチョウ"),
    "Whimbrel":                       ("Numenius phaeopus",     "チュウシャクシギ"),
    "Japanese Grosbeak":              ("Eophona personata",     "イカル"),
    "Eurasian Bullfinch":             ("Pyrrhula pyrrhula",     "ウソ"),
    "Greater White-fronted Goose":    ("Anser albifrons",       "マガン"),
    "Rook":                           ("Corvus frugilegus",     "ミヤマガラス"),
    "Sakhalin Leaf Warbler":          ("Phylloscopus borealoides", "エゾムシクイ"),
    "Eurasian Curlew":                ("Numenius arquata",      "ダイシャクシギ"),
    "Eurasian Wren":                  ("Troglodytes troglodytes", "ミソサザイ"),
    "Eurasian Skylark":               ("Alauda arvensis",       "ヒバリ"),
    "Eurasian Moorhen":               ("Gallinula chloropus",   "バン"),
    "Common Snipe":                   ("Gallinago gallinago",   "タシギ"),
    "Common Cuckoo":                  ("Cuculus canorus",       "カッコウ"),
    "Common Kingfisher":              ("Alcedo atthis",         "カワセミ"),
    "Lesser Cuckoo":                  ("Cuculus poliocephalus", "ホトトギス"),
    "Chestnut-eared Bunting":         ("Emberiza fucata",       "ホオアカ"),
    "Meadow Bunting":                 ("Emberiza cioides",      "ホオジロ"),
    "White's Thrush":                 ("Zoothera aurea",        "トラツグミ"),
    "Blue-and-white Flycatcher":      ("Cyanoptila cyanomelana", "オオルリ"),
    "Japanese Bush Warbler":          ("Horornis diphone",      "ウグイス"),
    "Japanese Tit":                   ("Parus minor",           "シジュウカラ"),
    "Great Cormorant":                ("Phalacrocorax carbo",   "カワウ"),
    "Gray Heron":                     ("Ardea cinerea",         "アオサギ"),
    "Black-crowned Night-Heron":      ("Nycticorax nycticorax", "ゴイサギ"),
    "Ural Owl":                       ("Strix uralensis",       "フクロウ"),
    "Green-winged Teal":              ("Anas crecca",           "コガモ"),
    "Narcissus Flycatcher":           ("Ficedula narcissina",   "キビタキ"),
    "Olive-backed Pipit":             ("Anthus hodgsoni",       "ビンズイ"),
    "Gadwall":                        ("Mareca strepera",       "オカヨシガモ"),
    "Brown-eared Bulbul":             ("Hypsipetes amaurotis",  "ヒヨドリ"),
    "Eurasian Tree Sparrow":          ("Passer montanus",       "スズメ"),
    "Oriental Greenfinch":            ("Chloris sinica",        "カワラヒワ"),
    "Pale Thrush":                    ("Turdus pallidus",       "シロハラ"),
    "Dusky Thrush":                   ("Turdus eunomus",        "ツグミ"),
    "Daurian Redstart":               ("Phoenicurus auroreus",  "ジョウビタキ"),
    "Bull-headed Shrike":             ("Lanius bucephalus",     "モズ"),
    "Japanese White-eye":             ("Zosterops japonicus",   "メジロ"),
    "Warbling White-eye":             ("Zosterops japonicus",   "メジロ"),
    "Great Tit":                      ("Parus major",           "シジュウカラ類"),
    "Great Bittern":                  ("Botaurus stellaris",    "サンカノゴイ"),
    "Red-throated Loon":              ("Gavia stellata",        "アビ"),
    "Tadorna ferruginea":             ("Tadorna ferruginea",    "アカツクシガモ"),
    "Ruddy Shelduck":                 ("Tadorna ferruginea",    "アカツクシガモ"),
    "Ring-necked Pheasant":           ("Phasianus colchicus",   "コウライキジ"),
    "Common Pheasant":                ("Phasianus colchicus",   "キジ"),
    "Whooper Swan":                   ("Cygnus cygnus",         "オオハクチョウ"),
    "Tufted Duck":                    ("Aythya fuligula",       "キンクロハジロ"),
}


def main():
    # 1. species_cache.json を更新
    with open(CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)

    updated = 0
    for common_name, (sci, jp) in CORRECTIONS.items():
        old = cache.get(common_name, {})
        if old.get("scientific_name") != sci or old.get("species_jp") != jp:
            cache[common_name] = {
                "scientific_name": sci,
                "species_jp": jp,
                "taxon_id": old.get("taxon_id"),
                "icon_url": old.get("icon_url"),
            }
            updated += 1

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"[cache] {updated} entries updated in species_cache.json")

    # 2. DB の scientific_name / species_jp を species_cache.json で一括更新
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    db_updated = 0
    for common_name, (sci, jp) in CORRECTIONS.items():
        cur.execute(
            "UPDATE detections SET scientific_name = ?, species_jp = ? WHERE species = ?",
            (sci, jp, common_name),
        )
        db_updated += cur.rowcount

    conn.commit()
    conn.close()
    print(f"[db] {db_updated} rows updated in detections table")


if __name__ == "__main__":
    main()
