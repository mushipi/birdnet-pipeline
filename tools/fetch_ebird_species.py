"""
eBird API で指定地域の観察種リストを取得して、BirdNET の custom_species_list 形式
（"<学名>_<英名>" の1行1種）でファイルに書き出す。

使い方:
    uv run python tools/fetch_ebird_species.py

事前準備:
    1. https://ebird.org/api/keygen で API key を取得
    2. settings.json に以下を追加：
       "ebird_api_key": "<取得したキー>",
       "ebird_region_code": "JP-40",   # 福岡県（他県は JP-XX、コードは eBird の region 一覧参照）
       "species_list_file": "ebird_species_list.txt"
"""

import json
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "settings.json"

EBIRD_BASE = "https://api.ebird.org/v2"


def main():
    settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    api_key = settings.get("ebird_api_key")
    region = settings.get("ebird_region_code", "JP-40")
    out_file = settings.get("species_list_file", "ebird_species_list.txt")

    if not api_key:
        print("[ERROR] settings.json に ebird_api_key が未設定です。")
        print("        https://ebird.org/api/keygen でキーを取得してください。")
        return 1

    headers = {"X-eBirdApiToken": api_key}

    # 1. 地域の観察種コードリストを取得
    print(f"[1/2] eBird から地域 {region} の観察種コードを取得中...")
    r = requests.get(f"{EBIRD_BASE}/product/spplist/{region}", headers=headers, timeout=30)
    r.raise_for_status()
    species_codes = r.json()
    print(f"      {len(species_codes)} 種コード取得")

    # 2. taxonomy 全件取得（speciesCode → sciName, comName のマップ）
    print("[2/2] eBird taxonomy を取得中...")
    r = requests.get(
        f"{EBIRD_BASE}/ref/taxonomy/ebird",
        params={"cat": "species", "fmt": "json"},
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()
    taxonomy = r.json()
    tax_map = {t["speciesCode"]: (t.get("sciName"), t.get("comName")) for t in taxonomy}

    # 3. 出力ファイルに書き出し
    out_path = BASE_DIR / out_file if not Path(out_file).is_absolute() else Path(out_file)
    lines = []
    skipped = 0
    for code in species_codes:
        if code not in tax_map:
            skipped += 1
            continue
        sci, com = tax_map[code]
        if not sci or not com:
            skipped += 1
            continue
        lines.append(f"{sci}_{com}")

    lines.sort()
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[OK] {len(lines)} 種を {out_path} に書き出し（taxonomy 不一致でスキップ: {skipped}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
