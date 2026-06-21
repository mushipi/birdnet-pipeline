#!/usr/bin/env python3
"""models_Labels.txt を species_master 駆動で "学名_英名"(Sci_Common) に統一する。

v2学習は英名フォルダ名でラベルを出力するため、配備前にこの後処理が必須。
- 入力ラベルは英名フォルダ名("Amur_Stonechat")でも既統一("Sci_Common")でも可(冪等)。
- en を species_master(en_birdnet→en_inat) で解決し、master の sci/en_birdnet で書き直す。
- **行順・行数は厳守**(tflite 出力ノード順と一致)。
- **fail-loud**: 解決できないラベルが1つでもあれば exit≠0 で列挙(=species_master更新を強制)。
- --write-model-label 指定時、master の model_label 列へ確定ラベル文字列を書き戻す。
"""
import argparse, csv, shutil, sys
from pathlib import Path

def load_master(p):
    rows=list(csv.DictReader(open(p,encoding="utf-8")))
    by_en={}
    for r in rows:
        for c in ("en_birdnet","en_inat"):
            e=(r.get(c) or "").strip()
            if e: by_en.setdefault(e, r)
    return rows, by_en

def resolve(label, by_en):
    cands=[label.replace("_"," ")]
    if "_" in label: cands.append(label.split("_",1)[1])  # sci_en の en 部
    for c in cands:
        if c in by_en: return by_en[c]
    return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--labels", default="models_Labels.txt")
    ap.add_argument("--master", default="/home/mushipi/Scripts/bird-fine-classifier/data/species_master.csv")
    ap.add_argument("--write-model-label", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a=ap.parse_args()
    labels=[l.rstrip("\n") for l in open(a.labels,encoding="utf-8") if l.strip()]
    mrows, by_en = load_master(a.master)
    out=[]; miss=[]; resolved_rows=[]
    for lab in labels:
        r=resolve(lab, by_en)
        if r is None: miss.append(lab); out.append(lab); resolved_rows.append(None); continue
        sci=(r["sci"] or "").strip(); en=(r["en_birdnet"] or r["en_inat"] or "").strip()
        out.append(f"{sci}_{en}"); resolved_rows.append(r)
    if miss:
        print(f"[FAIL] species_master に解決できないラベル {len(miss)}件:", file=sys.stderr)
        for m in miss: print(f"   - {m}", file=sys.stderr)
        print("→ species_master.csv に en_birdnet/sci を追記してから再実行", file=sys.stderr)
        sys.exit(1)
    assert len(out)==len(labels), "行数不一致(バグ)"
    changed=sum(1 for o,l in zip(out,labels) if o!=l)
    print(f"解決 {len(out)}/{len(labels)}  変更 {changed}行")
    if a.dry_run:
        for o,l in zip(out,labels):
            if o!=l: print(f"  {l}  ->  {o}")
        return
    shutil.copy(a.labels, a.labels+".bak-relabel")
    Path(a.labels).write_text("\n".join(out)+"\n", encoding="utf-8")
    print(f"書込: {a.labels} (原本 {a.labels}.bak-relabel)")
    if a.write_model_label:
        for r,ml in zip(resolved_rows,out): r["model_label"]=ml
        cols=list(mrows[0].keys())
        with open(a.master,"w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
            for r in mrows: w.writerow({c:r.get(c,"") for c in cols})
        print(f"master.model_label 書戻し: {a.master}")

if __name__=="__main__": main()
