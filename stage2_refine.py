"""Stage2 統合: BirdNET(Stage1) 検出を専門分類器(Stage2)へ回す group 汎用ディスパッチャ。

依存隔離のため Stage2(torch/transformers) は bird-fine-classifier の別venv(.venv-cpu)で
`predict.py --json` を subprocess 実行する。BirdProject 本体(birdnetlib/TF)とは混ぜない。

group 汎用: species_taxonomy.yaml で stage2_model が設定済みの群(現状 duck、将来 crow/gull…)だけを
トリガ対象にする。新しい群は分類器を学習して taxonomy に stage2_model を埋めれば自動で有効化される。

設定(settings.json の "stage2" ブロック):
  { "enabled": false, "bfc_dir": "~/bird-fine-classifier",
    "venv_python": ".venv-cpu/bin/python", "species_master": "data/species_master.csv",
    "timeout_sec": 60 }
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROUTE_STATUSES = {"target", "ood_tier1"}  # taxonomy のルーティング規約


def _bfc_paths(cfg: dict) -> tuple[Path, Path]:
    bfc = Path(cfg["bfc_dir"]).expanduser().absolute()
    # .resolve() は venv の bin/python シンボリックリンクを素の interpreter に解決し
    # venv を無効化してしまう（site-packages が見えず import 失敗）→ symlink パスのまま使う。
    venv_py = bfc / cfg.get("venv_python", ".venv-cpu/bin/python")
    return bfc, venv_py


def _active_groups(bfc: Path, venv_py: Path) -> set[str]:
    """species_taxonomy.yaml の中で pipeline.stage2_model が非null の群を、bfc venv 経由で取得。"""
    code = (
        "import yaml,json,sys;"
        "t=yaml.safe_load(open(sys.argv[1]));"
        "print(json.dumps([g for g,v in t.items() "
        "if isinstance(v,dict) and (v.get('pipeline') or {}).get('stage2_model')]))"
    )
    tax = bfc / "species_taxonomy.yaml"
    out = subprocess.run([str(venv_py), "-c", code, str(tax)],
                         capture_output=True, text=True, timeout=60)
    return set(json.loads(out.stdout.strip()))


def load_dispatch_map(cfg: dict) -> dict[str, str]:
    """{学名(sci): group}。stage2_model 設定済の群 × status(target/ood_tier1) のみ。

    キーは学名(sci)。素 6K でもカスタム CNN でも検出の scientific_name は同一なので、
    英名(en_birdnet)の表記揺れ（"Night Heron" vs "Night-Heron" 等）に依存せず堅牢に解決できる。
    失敗時は空 dict（=どの検出も refine しない安全側）。
    """
    try:
        bfc, venv_py = _bfc_paths(cfg)
        active = _active_groups(bfc, venv_py)
        master = Path(cfg["species_master"])
        if not master.is_absolute():
            master = bfc / master
        out: dict[str, str] = {}
        for r in csv.DictReader(open(master, encoding="utf-8")):
            g, st, sci = r.get("group"), r.get("status"), (r.get("sci") or "").strip()
            if sci and g in active and st in _ROUTE_STATUSES:
                out[sci] = g
        return out
    except Exception as e:
        print(f"  [stage2] dispatch_map 構築失敗（refine無効化）: {e}", file=sys.stderr)
        return {}


def refine_detection(audio_path: str, start, end, group: str, cfg: dict) -> dict | None:
    """audio_path の [start,end] 窓を切り出し Stage2(predict.py --json)で再分類。失敗時 None。"""
    import librosa
    import soundfile as sf

    bfc, venv_py = _bfc_paths(cfg)
    try:
        if start is not None and end is not None and end > start:
            y, sr = librosa.load(audio_path, sr=None, mono=True,
                                 offset=float(start), duration=float(end) - float(start))
        else:  # 窓情報が無ければ全体（フォールバック）
            y, sr = librosa.load(audio_path, sr=None, mono=True)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            slice_path = tf.name
        sf.write(slice_path, y, sr)
    except Exception as e:
        print(f"  [stage2] 窓切り出し失敗: {e}", file=sys.stderr)
        return None

    try:
        env = {"PYTHONPATH": str(bfc / "src")}
        import os
        env = {**os.environ, **env}
        proc = subprocess.run(
            [str(venv_py), "-m", "bird_fine.inference.predict",
             "--audio", slice_path, "--group", group, "--json"],
            cwd=str(bfc), env=env, capture_output=True, text=True,
            timeout=int(cfg.get("timeout_sec", 60)),
        )
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not line:
            print(f"  [stage2] 出力なし (rc={proc.returncode}): {proc.stderr[-300:]}", file=sys.stderr)
            return None
        return json.loads(line)
    except Exception as e:
        print(f"  [stage2] subprocess/parse 失敗: {e}", file=sys.stderr)
        return None
    finally:
        Path(slice_path).unlink(missing_ok=True)


def refined_fields(d: dict) -> dict:
    """predict.py --json の出力を db.set_refined のキーワード引数に変換。"""
    if d.get("ood_rejected"):
        return dict(refined_species=None, refined_label=None, refined_sci=None,
                    refined_confidence=None, refined_energy=d.get("energy_score"),
                    refined_status="ood_rejected", stage2_model_version=d.get("model"),
                    refined_group=d.get("group"))
    top = d.get("top") or {}
    return dict(refined_species=top.get("species"), refined_label=top.get("label"),
                refined_sci=top.get("sci"), refined_confidence=top.get("probability"),
                refined_energy=d.get("energy_score"), refined_status="refined",
                stage2_model_version=d.get("model"), refined_group=d.get("group"))
