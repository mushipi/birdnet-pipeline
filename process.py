"""
BirdNET Phase 1 - raw_ingest/ にある WAV ファイルを推論・仕分けして DB に記録する。
1ファイルにつき、CONF_HIGH 以上の種ごとに1レコードを DB に保存する。

Usage:
    uv run python process.py --pi-id pi-01 [--ingest-dir DIR] [--latitude LAT] [--longitude LON]
"""

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from birdnetlib import Recording
from birdnetlib.analyzer import Analyzer

import db
import stage2_refine

BASE_DIR = Path(__file__).parent

def _load_settings() -> dict:
    with open(BASE_DIR / "settings.json", encoding="utf-8") as f:
        return json.load(f)

_settings = _load_settings()
PROCESSED_DIR = Path(_settings.get("processed_dir", BASE_DIR / "processed"))
RAW_INGEST_DIR = Path(_settings.get("raw_ingest_dir", BASE_DIR / "raw_ingest"))
SPECIES_LIST_FILE = _settings.get("species_list_file")  # オプション。eBird 由来のホワイトリスト
# Stage1 モデル選択（未設定なら素の BirdNET_GLOBAL_6K_V2.4、設定すれば mainPC 追加学習のカスタム分類器）
BIRDNET_MODEL_PATH = _settings.get("birdnet_model_path")
BIRDNET_LABELS_PATH = _settings.get("birdnet_labels_path")

# Stage2 統合（既定 disabled = 完全 no-op。明示有効化までは従来挙動を保つ）
_STAGE2_CFG = _settings.get("stage2", {}) or {}
_STAGE2_ENABLED = bool(_STAGE2_CFG.get("enabled"))
_DISPATCH_MAP: dict[str, str] = {}  # {BirdNET種名: group}。process_all 起動時に構築

CONF_HIGH = 0.65  # confirmed として記録する確信度下限
CONF_LOW = 0.25   # birdnetlib の min_conf に渡す値（これ未満は API 側でフィルタ）

_TS_RE = re.compile(r"(\d{8}_\d{6})")
_BLACKLIST_LOG = BASE_DIR / "blacklist_hits.log"


def _load_species_list() -> list[str] | None:
    """eBird ホワイトリストを読み込む（1行 = '学名_英名' 形式、コメント '#' 対応）。
    ファイルなしまたは未設定なら None を返し、BirdNET の地域フィルタのみで動作させる。"""
    if not SPECIES_LIST_FILE:
        return None
    path = Path(SPECIES_LIST_FILE)
    if not path.is_absolute():
        path = BASE_DIR / SPECIES_LIST_FILE
    if not path.exists():
        return None
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines or None


_SPECIES_LIST = _load_species_list()


def build_analyzer() -> Analyzer:
    """Stage1 の Analyzer を生成する。

    settings.json に birdnet_model_path / birdnet_labels_path が両方あれば
    mainPC で追加学習したカスタム分類器を、無ければ素の BirdNET_GLOBAL_6K_V2.4 をロードする。
    （カスタム配備は設定 1 行の差し替えで完結し、未配備時は従来挙動＝完全非破壊）
    """
    if BIRDNET_MODEL_PATH and BIRDNET_LABELS_PATH:
        model = Path(BIRDNET_MODEL_PATH).expanduser()
        labels = Path(BIRDNET_LABELS_PATH).expanduser()
        if not model.exists() or not labels.exists():
            raise FileNotFoundError(
                f"カスタム分類器が見つからないわ: model={model} labels={labels}"
            )
        print(f"  カスタム分類器をロード: {model.name} / {labels.name}")
        return Analyzer(classifier_model_path=str(model), classifier_labels_path=str(labels))
    print("  素の BirdNET_GLOBAL_6K_V2.4 をロード")
    return Analyzer()


def _load_blacklist() -> set[str]:
    path = BASE_DIR / "species_blacklist.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return set(json.load(f).get("blacklist_scientific", []))
    return set()


_BLACKLIST: set[str] = _load_blacklist()


def _log_blacklist_hit(wav_name: str, sci_name: str, confidence: float) -> None:
    from datetime import datetime as _dt
    line = f"{_dt.now().strftime('%Y-%m-%d %H:%M:%S')} | {wav_name} | {sci_name} | {confidence:.3f}\n"
    with open(_BLACKLIST_LOG, "a", encoding="utf-8") as f:
        f.write(line)

def load_species_cache():
    cache_path = BASE_DIR / "species_cache.json"
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}



def parse_timestamp(wav_path: Path) -> datetime:
    """ファイル名の YYYYMMDD_HHMMSS プレフィックスから録音時刻を返す。失敗時は mtime。"""
    m = _TS_RE.search(wav_path.stem)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    return datetime.fromtimestamp(wav_path.stat().st_mtime)


def get_pi_config(pi_id: str | None) -> dict:
    """settings.json の pi_devices から該当端末の設定を取得。なければ legacy 設定にフォールバック。"""
    devices = _settings.get("pi_devices", {})
    if pi_id and pi_id in devices:
        return devices[pi_id]
    # legacy fallback
    return {
        "noise_reduction": _settings.get("noise_reduction", "highpass"),
        "latitude": _settings.get("latitude", 33.57869),
        "longitude": _settings.get("longitude", 130.257151),
        "enabled": True,
    }


def preprocess_audio(wav_path: Path, noise_reduction: str = "highpass") -> Path | None:
    """モノラル変換 + 設定に応じたノイズ除去を行う。
    noise_reduction:
      - 'off'                 : モノラル変換のみ
      - 'highpass'            : 500Hz ハイパスフィルタ（既定）
      - 'spectral'            : noisereduce による定常ノイズ除去
      - 'highpass+spectral'   : 両方適用
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    out_path = Path(tmp.name)

    sox_cmd = ["sox", str(wav_path), "-c", "1", str(out_path)]
    if "highpass" in noise_reduction:
        sox_cmd += ["highpass", "500"]
    try:
        subprocess.run(sox_cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"  [ERROR] sox failed: {e.stderr.decode()}")
        out_path.unlink(missing_ok=True)
        return None

    if "spectral" in noise_reduction:
        try:
            import noisereduce as nr
            import soundfile as sf
            data, rate = sf.read(str(out_path))
            reduced = nr.reduce_noise(y=data, sr=rate, stationary=True)
            sf.write(str(out_path), reduced, rate)
        except Exception as e:
            print(f"  [WARN] spectral denoise failed (continuing without): {e}")

    return out_path


def to_mono(wav_path: Path) -> Path | None:
    """後方互換：preprocess_audio(highpass) のラッパー。"""
    return preprocess_audio(wav_path, "highpass")


def analyze_file(analyzer: Analyzer, wav_path: Path, ts: datetime, lat: float, lon: float, noise_reduction: str = "highpass") -> list:
    """前処理（モノラル+ノイズ除去）後に birdnetlib で推論し、confidence 降順のリストを返す。失敗時は []。"""
    mono_path = preprocess_audio(wav_path, noise_reduction)
    if mono_path is None:
        return []
    try:
        recording = Recording(
            analyzer,
            str(mono_path),
            date=ts,
            lat=lat,
            lon=lon,
            min_conf=CONF_LOW,
            overlap=2.0,
            sensitivity=1.25,
        )
        recording.analyze()
        detections = recording.detections
        return sorted(detections, key=lambda d: d["confidence"], reverse=True)
    except Exception as e:
        print(f"  [ERROR] analyze failed: {e}")
        return []
    finally:
        mono_path.unlink(missing_ok=True)


def filter_blacklist(detections: list, wav_name: str) -> list:
    """ブラックリスト種の検出を除外し、ヒット件数をログに記録する。"""
    result = []
    for d in detections:
        if d.get("scientific_name") in _BLACKLIST:
            _log_blacklist_hit(wav_name, d["scientific_name"], d["confidence"])
        else:
            result.append(d)
    return result


def aggregate_by_species(detections: list, min_conf: float) -> dict[str, dict]:
    """
    確信度 min_conf 以上の検知を種ごとに集約し、最高確信度のエントリと検出回数を返す。
    Returns: {common_name: detection_dict}  ※ detection_dict に det_count を付与
    """
    best: dict[str, dict] = {}
    count: dict[str, int] = {}
    for d in detections:
        if d["confidence"] < min_conf:
            continue
        name = d["common_name"]
        count[name] = count.get(name, 0) + 1
        if name not in best or d["confidence"] > best[name]["confidence"]:
            best[name] = d
    for name, det in best.items():
        det["det_count"] = count[name]
    return best


def process_all(ingest_dir: Path, pi_id: str, lat: float, lon: float) -> None:
    # Pi 別設定を取得（noise_reduction や lat/lon の上書き）
    pi_config = get_pi_config(pi_id)
    if not pi_config.get("enabled", True):
        print(f"[skip] {pi_id} は disabled 設定のためスキップ")
        return
    nr_mode = pi_config.get("noise_reduction", "highpass")
    # CLI 引数 lat/lon と Pi 設定の lat/lon は CLI 優先（明示指定された場合）
    eff_lat = pi_config.get("latitude", lat) if lat == 33.57869 else lat
    eff_lon = pi_config.get("longitude", lon) if lon == 130.257151 else lon

    wav_files = sorted(ingest_dir.glob("*.wav"))
    if not wav_files:
        print(f"raw_ingest に WAV ファイルが見つからないわ: {ingest_dir}")
        return

    print(f"Analyzer をロード中... (pi_id={pi_id}, noise_reduction={nr_mode})")
    analyzer = build_analyzer()
    if _SPECIES_LIST:
        # eBird 由来などのホワイトリストを Analyzer に登録（地域・季節フィルタを補強）
        analyzer.custom_species_list = _SPECIES_LIST
        print(f"  custom_species_list: {len(_SPECIES_LIST)} 種を許可リストに設定")
    db.init_db()
    species_cache = load_species_cache()

    global _DISPATCH_MAP
    if _STAGE2_ENABLED:
        _DISPATCH_MAP = stage2_refine.load_dispatch_map(_STAGE2_CFG)
        groups = sorted(set(_DISPATCH_MAP.values()))
        print(f"  [stage2] 有効: トリガ {len(_DISPATCH_MAP)} 種 / 群 {groups or '(なし)'}")

    for wav_path in wav_files:
        if not wav_path.exists():
            print(f"\n[skip] {wav_path.name} は既に処理済み（別プロセスが先行）")
            continue
        print(f"\n処理中: {wav_path.name}")
        ts = parse_timestamp(wav_path)
        detections = analyze_file(analyzer, wav_path, ts, eff_lat, eff_lon, noise_reduction=nr_mode)
        detections = filter_blacklist(detections, wav_path.name)
        date_str = ts.strftime("%Y-%m-%d")

        confirmed = aggregate_by_species(detections, CONF_HIGH)

        if confirmed:
            # Top1（最高確信度）の科学名ディレクトリへ移動
            top_det = max(confirmed.values(), key=lambda d: d["confidence"])
            sci_name = top_det["scientific_name"].replace(" ", "_")
            dest_dir = PROCESSED_DIR / date_str / "detected" / sci_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / wav_path.name
            shutil.move(str(wav_path), str(dest_path))

            # 種ごとに1レコード INSERT
            for det in confirmed.values():
                sci_name = det["scientific_name"]
                common_name = det["common_name"]
                jp_name = species_cache.get(common_name, {}).get("species_jp")
                
                rid = db.insert_detection(
                    timestamp=ts.isoformat(),
                    file_path=str(dest_path),
                    species=common_name,
                    confidence=det["confidence"],
                    status="confirmed",
                    pi_id=pi_id,
                    latitude=lat,
                    longitude=lon,
                    scientific_name=sci_name,
                    species_jp=jp_name,
                    det_count=det.get("det_count"),
                )

                # Stage2: 専門分類器のある群(duck 等)なら同一窓を再分類し refined_* に追記（非破壊）
                group = _DISPATCH_MAP.get(common_name) if _STAGE2_ENABLED else None
                if group:
                    res = stage2_refine.refine_detection(
                        str(dest_path), det.get("start_time"), det.get("end_time"), group, _STAGE2_CFG)
                    if res:
                        db.set_refined(rid, **stage2_refine.refined_fields(res))
                        rf = res.get("top") or {}
                        tag = rf.get("label") if not res.get("ood_rejected") else "OOD棄却"
                        print(f"    [stage2/{group}] {common_name} → {tag} (energy {res.get('energy_score')})")
                print(f"  [confirmed] {jp_name or common_name} ({det['confidence']:.2f}) x{det.get('det_count', 1)}")


        elif detections:
            # CONF_HIGH 未満だが CONF_LOW 以上の検知あり → review/
            top = detections[0]
            ts_str = ts.strftime("%Y%m%d_%H%M%S")
            conf_int = int(top["confidence"] * 100)
            top_name = top["common_name"].replace(" ", "_")
            new_name = f"{ts_str}_Conf{conf_int:02d}_{top_name}{wav_path.suffix}"
            dest_dir = PROCESSED_DIR / date_str / "review"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / new_name
            shutil.move(str(wav_path), str(dest_path))

            db.insert_detection(
                timestamp=ts.isoformat(),
                file_path=str(dest_path),
                species=top["common_name"],
                confidence=top["confidence"],
                status="pending",
                pi_id=pi_id,
                latitude=lat,
                longitude=lon,
                scientific_name=top["scientific_name"],
                species_jp=species_cache.get(top["common_name"], {}).get("species_jp"),
                det_count=top.get("det_count"),
            )
            print(f"  [pending] {top['common_name']} ({top['confidence']:.2f})")


        else:
            # 検知なし → unknown/
            dest_dir = PROCESSED_DIR / date_str / "unknown"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / wav_path.name
            shutil.move(str(wav_path), str(dest_path))

            db.insert_detection(
                timestamp=ts.isoformat(),
                file_path=str(dest_path),
                species=None,
                confidence=None,
                status="noise",
                pi_id=pi_id,
                latitude=lat,
                longitude=lon,
            )
            print(f"  [noise]")


def main() -> None:
    parser = argparse.ArgumentParser(description="BirdNET Phase 1 処理スクリプト")
    parser.add_argument(
        "--pi-id",
        required=True,
        help="Pi識別子 (例: pi-01)",
    )
    parser.add_argument(
        "--ingest-dir",
        type=Path,
        default=None,
        help="処理対象 WAV ファイルのディレクトリ（デフォルト: raw_ingest/{pi-id}/）",
    )
    parser.add_argument(
        "--latitude",
        type=float,
        default=33.57869,
        help="録音地点の緯度（デフォルト: 33.57869）",
    )
    parser.add_argument(
        "--longitude",
        type=float,
        default=130.257151,
        help="録音地点の経度（デフォルト: 130.257151）",
    )
    args = parser.parse_args()

    ingest_dir = args.ingest_dir or (RAW_INGEST_DIR / args.pi_id)

    if not ingest_dir.exists():
        print(f"[ERROR] ingest-dir が存在しない: {ingest_dir}")
        return

    process_all(ingest_dir, args.pi_id, args.latitude, args.longitude)
    print("\n完了。")


if __name__ == "__main__":
    main()
