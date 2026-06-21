#!/usr/bin/env python3
"""種同一性 契約 lint（retrain/配備前ゲート）。

species_master を正本に、各層が一貫して種(taxon_id)へ解決するか検査する。
1つでもFAILなら exit≠0。今回踏んだ事故（enラベル・genus drift・seasonal欠落112/200・
役割行間の属不整合）を機械検出する。

使い方:
  validate_species_contract.py --labels models_Labels.txt \
      --master .../species_master.csv --seasonal data/seasonal_occurrence.csv \
      [--raw-dir data/raw]
"""
import argparse, csv, sys
from pathlib import Path
from collections import defaultdict

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    ap.add_argument("--master", required=True)
    ap.add_argument("--seasonal")
    ap.add_argument("--raw-dir")
    a=ap.parse_args()

    mrows=list(csv.DictReader(open(a.master, encoding="utf-8")))
    def en_of(r): return [(r.get("en_birdnet") or "").strip(), (r.get("en_inat") or "").strip()]
    def norm_tax(t): return (t or "").strip().replace(".0","")
    en2rows=defaultdict(list); sci2rows=defaultdict(list)
    for r in mrows:
        for e in en_of(r):
            if e: en2rows[e].append(r)
        s=(r["sci"] or "").strip()
        if s: sci2rows[s].append(r)

    labels=[l.strip() for l in open(a.labels, encoding="utf-8") if l.strip()]
    fails=[]
    def check(name, bad, hint=""):
        if bad:
            fails.append(name)
            print(f"[FAIL] {name}: {len(bad)}件")
            for b in bad[:15]: print(f"    - {b}")
            if hint: print(f"    → {hint}")
        else:
            print(f"[PASS] {name}")

    # C1: ラベル → master（en解決＋sci一致＝genus driftなし）
    bad=[]
    for lab in labels:
        if "_" not in lab: bad.append(f"{lab} (en形式=sci未統一)"); continue
        sci, en = lab.split("_",1)
        rows=en2rows.get(en)
        if not rows: bad.append(f"{lab} (en '{en}' がmaster不在)"); continue
        if sci not in {(r['sci'] or '').strip() for r in rows}:
            bad.append(f"{lab} (sci '{sci}' が master の en行と不一致=genus drift)")
    check("C1 ラベル↔master sci一致", bad, "relabel_labels_sci.py 再実行 / master属正規化")

    # C2: seasonal カバレッジ（全ラベルが seasonal に在る＋seasonal sci ⊆ master）
    if a.seasonal and Path(a.seasonal).exists():
        srows=list(csv.DictReader(open(a.seasonal, encoding="utf-8")))
        sseas=set((r.get("sci") or "").strip() for r in srows)
        label_sci=set(l.split("_",1)[0] for l in labels if "_" in l)
        miss_in_seasonal=sorted(label_sci - sseas)
        check("C2 seasonal が全ラベルを網羅", [f"{s} (seasonal欠落)" for s in miss_in_seasonal],
              "build_seasonal_occurrence.py を最新labelsで再生成")
        orphan_seas=sorted(s for s in sseas if s and s not in sci2rows)
        check("C2b seasonal sci ⊆ master", [f"{s} (master不在)" for s in orphan_seas])
    else:
        print("[skip] C2 seasonal（--seasonal未指定）")

    # C3: master 役割行間の genus 一貫性（同一enの全行が同一sci）
    bad=[]
    for en, rows in en2rows.items():
        scis={(r['sci'] or '').strip() for r in rows}
        if len(scis)>1: bad.append(f"{en}: {sorted(scis)}")
    check("C3 master 同一種(en)の属一貫性", bad, "全役割行を現行属へ正規化")

    # C4: taxon_id（全ラベル種が taxon_id を持つ＋同一種の役割行で同一）
    bad=[]; bad_consist=[]
    for lab in labels:
        if "_" not in lab: continue
        en=lab.split("_",1)[1]; rows=en2rows.get(en,[])
        taxes={norm_tax(r.get("taxon_id")) for r in rows if norm_tax(r.get("taxon_id"))}
        if not taxes: bad.append(f"{lab} (taxon_id無し)")
        elif len(taxes)>1: bad_consist.append(f"{lab}: {sorted(taxes)}")
    check("C4 全ラベル種に taxon_id", bad, "Part A taxon_id 補完(iNat)")
    check("C4b 同一種の役割行で taxon_id 一致", bad_consist)

    # C5: data/raw フォルダ ⊆ master en（retrain側のみ）
    if a.raw_dir and Path(a.raw_dir).exists():
        folders=[p.name.replace("_"," ") for p in Path(a.raw_dir).iterdir() if p.is_dir() and p.name!="Background"]
        bad=[f for f in folders if f not in en2rows]
        check("C5 data/raw フォルダ ⊆ master", [f"{f} (master不在)" for f in bad],
              "species_master へ追記 or フォルダ名修正")
    else:
        print("[skip] C5 data/raw（--raw-dir未指定）")

    print()
    if fails:
        print(f"=== LINT FAIL ({len(fails)}チェック): {', '.join(fails)} ===")
        sys.exit(1)
    print("=== LINT PASS（全チェック合格）===")

if __name__=="__main__": main()
