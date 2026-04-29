import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# BirdProject ルートを import パスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import db

app = FastAPI(docs_url="/api/docs", redoc_url="/api/redoc")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

db.init_db()

PROJECT_DIR = Path(__file__).parent.parent
SETTINGS_FILE = PROJECT_DIR / "settings.json"
LOCK_FILE = Path("/tmp/birdnet_ingest.lock")
LOG_FILE = PROJECT_DIR / "ingest.log"
INGEST_SCRIPT = PROJECT_DIR / "run_ingest.sh"

def _processed_dir() -> Path:
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            s = json.load(f)
        if "processed_dir" in s:
            return Path(s["processed_dir"])
    return PROJECT_DIR / "processed"

PROCESSED_DIR = _processed_dir()

DEFAULT_SETTINGS = {
    "ingest_interval_min": 10,
    "pi_host": "mushipi@100.78.71.38",
    "pi_recordings": "/home/mushipi/recordings/",
    "record_all_day": False,
    "record_start_hour": 4,
    "record_stop_hour": 22,
    "segment_sec": 60,
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return DEFAULT_SETTINGS.copy()


def save_settings(data: dict) -> None:
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def push_record_config_to_pi(pi_host: str, start: int, stop: int, segment_sec: int, all_day: bool) -> None:
    config = f"RECORD_ALL_DAY={'1' if all_day else '0'}\nRECORD_START={start}\nRECORD_STOP={stop}\nSEGMENT_SEC={segment_sec}\n"
    subprocess.run(
        ["ssh", pi_host, "cat > /home/mushipi/BirdProject/record_config.sh"],
        input=config, text=True
    )
    subprocess.run(
        ["ssh", pi_host, "sudo systemctl restart bird-record.service"]
    )


def update_ingest_timer(interval: int) -> None:
    """birdnet-ingest.timer の OnUnitActiveSec を interval 分に更新して再起動する"""
    timer_path = Path.home() / ".config/systemd/user/birdnet-ingest.timer"
    content = timer_path.read_text()
    import re
    content = re.sub(r"OnUnitActiveSec=\S+", f"OnUnitActiveSec={interval}min", content)
    timer_path.write_text(content)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "restart", "birdnet-ingest.timer"])


@app.get("/")
async def index(
    request: Request,
    status: str | None = None,
    pi_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_conf: str | None = None,
    species: str | None = None,
):
    # 手動で float 変換（空文字列などのパースエラー回避）
    try:
        min_conf_val = float(min_conf) if min_conf else None
    except (ValueError, TypeError):
        min_conf_val = None

    # フォームから % 値で来るので 0〜1 に変換
    min_conf_frac = min_conf_val / 100.0 if min_conf_val is not None else None
    rows = db.get_detections(
        status=status,
        pi_id=pi_id,
        date_from=date_from,
        date_to=date_to,
        min_conf=min_conf_frac,
        species=species,
    )
    pi_list = db.get_distinct_pi_ids()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "rows": [dict(r) for r in rows],
            "current_status": status or "all",
            "current_pi_id": pi_id or "",
            "pi_list": pi_list,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "min_conf": int(min_conf_val) if min_conf_val is not None else "",
            "species": species or "",
        },
    )


@app.get("/help")
async def help_page(request: Request):
    return templates.TemplateResponse("help.html", {"request": request})


@app.get("/docs")
async def docs_list(request: Request):
    docs = [
        {"id": "design", "title": "設計書", "file": "DESIGN.md", "desc": "システムの全体構造、ハードウェア、ネットワーク構成など"},
        {"id": "devlog", "title": "開発記録", "file": "DEVLOG.md", "desc": "実装の経緯、修正履歴、技術的決定事項"},
        {"id": "parabolic", "title": "パラボリックマイク設計", "file": "docs/hardware/parabolic_mic.md", "desc": "3Dプリント製パラボリックマイクの詳細設計資料"},
    ]
    return templates.TemplateResponse("docs_list.html", {"request": request, "docs": docs})


@app.get("/docs/{doc_name}")
async def docs_page(request: Request, doc_name: str):
    from fastapi import HTTPException
    files = {
        "design": PROJECT_DIR / "DESIGN.md",
        "devlog": PROJECT_DIR / "DEVLOG.md",
        "parabolic": PROJECT_DIR / "docs" / "hardware" / "parabolic_mic.md",
    }
    if doc_name not in files:
        raise HTTPException(status_code=404)
    content = files[doc_name].read_text(encoding="utf-8")
    titles = {
        "design": "設計書",
        "devlog": "開発記録",
        "parabolic": "パラボリックマイク設計",
    }
    return templates.TemplateResponse(
        "docs.html",
        {"request": request, "content": content, "title": titles[doc_name], "doc_name": doc_name},
    )


@app.get("/stats")
async def stats(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    min_conf: str | None = None,
):
    try:
        min_conf_val = float(min_conf) if min_conf else 0.0
    except (ValueError, TypeError):
        min_conf_val = 0.0
    data = db.get_stats(date_from=date_from, date_to=date_to, min_conf=min_conf_val)
    return templates.TemplateResponse("stats.html", {"request": request, **data})


@app.post("/api/pi/health")
async def pi_health():
    settings = load_settings()
    pi_host = settings.get("pi_host")
    script_path = "/home/mushipi/BirdProject/health_check_pi.sh"
    try:
        res = subprocess.run(
            ["ssh", pi_host, f"bash {script_path}"],
            capture_output=True, text=True, timeout=30
        )
        if res.returncode == 0:
            return json.loads(res.stdout)
        else:
            return {"error": res.stderr or "Failed to run health check"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/pi/test_record")
async def pi_test_record():
    settings = load_settings()
    pi_host = settings.get("pi_host")
    remote_wav = "/tmp/test_mic_playback.wav"
    local_static = Path(__file__).parent / "static"
    local_wav = local_static / "test_mic.wav"

    local_static.mkdir(exist_ok=True)

    try:
        # 1. 録音 (5秒)
        subprocess.run(
            ["ssh", pi_host, f"arecord -D default -d 5 -f S16_LE -r 44100 {remote_wav}"],
            timeout=30, check=True
        )
        # 2. 転送 (cat で取得して保存)
        res = subprocess.run(
            ["ssh", pi_host, f"cat {remote_wav}"],
            capture_output=True, timeout=30, check=True
        )
        with open(local_wav, "wb") as f:
            f.write(res.stdout)

        return {"url": "/static/test_mic.wav?t=" + str(datetime.now().timestamp())}
    except Exception as e:
        return {"error": str(e)}


@app.get("/settings")
async def settings_page(request: Request):
    data = load_settings()
    return templates.TemplateResponse(
        "settings.html", {"request": request, "settings": data}
    )


@app.post("/settings")
async def update_settings(
    request: Request,
    ingest_interval_min: int = Form(...),
    pi_host: str = Form(...),
    pi_recordings: str = Form(...),
    record_all_day: str = Form(""),
    record_start_hour: int = Form(4),
    record_stop_hour: int = Form(22),
    segment_sec: int = Form(...),
):
    all_day = record_all_day == "1"
    # 既存設定を読み込んでパス系フィールドを保持したまま上書き
    existing = load_settings()
    settings = {
        **{k: v for k, v in existing.items() if k not in (
            "ingest_interval_min", "pi_host", "pi_recordings",
            "record_all_day", "record_start_hour", "record_stop_hour", "segment_sec"
        )},
        "ingest_interval_min": ingest_interval_min,
        "pi_host": pi_host,
        "pi_recordings": pi_recordings,
        "record_all_day": all_day,
        "record_start_hour": record_start_hour,
        "record_stop_hour": record_stop_hour,
        "segment_sec": segment_sec,
    }
    save_settings(settings)
    update_ingest_timer(ingest_interval_min)
    push_record_config_to_pi(pi_host, record_start_hour, record_stop_hour, segment_sec, all_day)
    return templates.TemplateResponse(
        "settings.html", {"request": request, "settings": settings, "saved": True}
    )


@app.post("/ingest")
async def trigger_ingest():
    if LOCK_FILE.exists():
        return JSONResponse(
            status_code=409,
            content={"error": "実行中です。しばらくお待ちください。"},
        )
    subprocess.Popen(["/bin/bash", str(INGEST_SCRIPT)])
    return JSONResponse({"status": "started"})


@app.get("/api/pi/status")
async def pi_status():
    settings = load_settings()
    pi_host = settings.get("pi_host", "")
    if not pi_host:
        return {"online": False, "reason": "pi_host not configured"}
    result = subprocess.run(
        ["ssh", "-q", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", pi_host, "exit"],
        capture_output=True,
    )
    return {"online": result.returncode == 0}


@app.get("/ingest/status")
async def ingest_status():
    running = LOCK_FILE.exists()
    last_run = None
    log_tail = []
    if LOG_FILE.exists():
        stat = LOG_FILE.stat()
        JST = timezone(timedelta(hours=9))
        last_run = datetime.fromtimestamp(stat.st_mtime, tz=JST).strftime("%Y-%m-%d %H:%M:%S")
        lines = LOG_FILE.read_text(errors="replace").splitlines()
        log_tail = lines[-20:]
    return {"running": running, "last_run": last_run, "log_tail": log_tail}


@app.get("/export/dataset")
async def export_dataset():
    import io
    import zipfile

    rows = db.get_detections(status="confirmed")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for row in rows:
            path = Path(row["file_path"])
            if path.exists():
                species = (row["species"] or "unknown").replace(" ", "_")
                zf.write(path, f"{species}/{path.name}")
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=bird_dataset.zip"},
    )


@app.post("/update/{detection_id}")
async def update(
    detection_id: int,
    new_status: str = Form(...),
    redirect_status: str = Form(""),
    redirect_pi_id: str = Form(""),
):
    db.update_status(detection_id, new_status)
    params = {}
    if redirect_status:
        params["status"] = redirect_status
    if redirect_pi_id:
        params["pi_id"] = redirect_pi_id
    if params:
        from urllib.parse import urlencode
        return RedirectResponse(f"/?{urlencode(params)}", status_code=303)
    return RedirectResponse("/", status_code=303)


# ─────────────────── ブラックリスト管理 ───────────────────

BLACKLIST_FILE = PROJECT_DIR / "species_blacklist.json"


def load_blacklist() -> dict:
    if BLACKLIST_FILE.exists():
        return json.loads(BLACKLIST_FILE.read_text(encoding="utf-8"))
    return {"blacklist_scientific": [], "reason": ""}


def save_blacklist(data: dict) -> None:
    BLACKLIST_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@app.get("/blacklist")
async def blacklist_page(request: Request):
    bl = load_blacklist()
    sci_names = bl.get("blacklist_scientific", [])
    # 学名 → 英名・和名のマップを species_cache から作成
    cache_path = PROJECT_DIR / "species_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    sci_to_meta = {}
    for common, meta in cache.items():
        sci = meta.get("scientific_name")
        if sci and sci not in sci_to_meta:
            sci_to_meta[sci] = {"common": common, "jp": meta.get("species_jp", "")}

    entries = [
        {
            "scientific_name": sci,
            "common_name": sci_to_meta.get(sci, {}).get("common", ""),
            "species_jp": sci_to_meta.get(sci, {}).get("jp", ""),
        }
        for sci in sci_names
    ]
    return templates.TemplateResponse(
        "blacklist.html",
        {"request": request, "entries": entries, "reason": bl.get("reason", "")},
    )


@app.post("/api/blacklist/add")
async def blacklist_add(
    scientific_name: str = Form(...),
    retroactive: str = Form("1"),
):
    bl = load_blacklist()
    sci_list = bl.get("blacklist_scientific", [])
    if scientific_name not in sci_list:
        sci_list.append(scientific_name)
        bl["blacklist_scientific"] = sci_list
        save_blacklist(bl)

    demoted = 0
    if retroactive == "1":
        import sqlite3
        conn = sqlite3.connect(db.DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "UPDATE detections SET status='noise' "
            "WHERE scientific_name = ? AND status IN ('confirmed','pending')",
            (scientific_name,),
        )
        demoted = cur.rowcount
        conn.commit()
        conn.close()
    return {"ok": True, "added": scientific_name, "demoted": demoted}


@app.post("/api/label/{detection_id}")
async def set_label(detection_id: int, label: str = Form(...)):
    """Phase 5-C: 人手検証ラベル（'correct'/'wrong'/'clear' で解除）。"""
    new_label = None if label == "clear" else label
    if new_label not in (None, "correct", "wrong"):
        return JSONResponse(status_code=400, content={"error": "invalid label"})
    db.set_human_label(detection_id, new_label)
    return {"ok": True, "id": detection_id, "label": new_label}


@app.post("/api/blacklist/remove")
async def blacklist_remove(scientific_name: str = Form(...)):
    bl = load_blacklist()
    sci_list = bl.get("blacklist_scientific", [])
    if scientific_name in sci_list:
        sci_list.remove(scientific_name)
        bl["blacklist_scientific"] = sci_list
        save_blacklist(bl)
    return {"ok": True, "removed": scientific_name}


@app.get("/audio/{file_path:path}")
async def audio(file_path: str):
    from fastapi import HTTPException
    # テンプレートで lstrip('/') されているので / を戻して絶対パスを復元
    candidate = Path("/" + file_path)
    if not candidate.exists():
        # フォールバック: PROCESSED_DIR 相対パス
        candidate = PROCESSED_DIR / file_path
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(candidate, media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
