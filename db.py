import json
import sqlite3
from pathlib import Path

_BASE_DIR = Path(__file__).parent
_DEFAULT_DB_PATH = _BASE_DIR / "db" / "bird_calls.db"


def _resolve_db_path() -> Path:
    settings_file = _BASE_DIR / "settings.json"
    if settings_file.exists():
        try:
            s = json.loads(settings_file.read_text(encoding="utf-8"))
            if "db_path" in s:
                return Path(s["db_path"])
        except (json.JSONDecodeError, KeyError):
            pass
    return _DEFAULT_DB_PATH


DB_PATH = _resolve_db_path()

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS detections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    species     TEXT,
    confidence  REAL,
    status      TEXT NOT NULL CHECK(status IN ('confirmed', 'pending', 'noise')),
    pi_id       TEXT NOT NULL DEFAULT 'unknown',
    latitude    REAL,
    longitude   REAL,
    scientific_name TEXT,
    species_jp  TEXT,
    det_count   INTEGER DEFAULT 1
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(_CREATE_TABLE)
    _migrate_db()


def _migrate_db() -> None:
    """既存テーブルに不足カラムを追加する（冪等）。"""
    with _connect() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(detections)").fetchall()}
        if "det_count" not in cols:
            conn.execute("ALTER TABLE detections ADD COLUMN det_count INTEGER DEFAULT 1")
        # Phase 5-C: 人手検証ラベル（カスタム分類器の学習データ用）
        if "human_label" not in cols:
            conn.execute(
                "ALTER TABLE detections ADD COLUMN human_label TEXT "
                "CHECK(human_label IN ('correct','wrong') OR human_label IS NULL)"
            )
        if "human_labeled_at" not in cols:
            conn.execute("ALTER TABLE detections ADD COLUMN human_labeled_at TEXT")
        # Stage2 統合: 専門分類器(カモ10種等)による再分類結果を非破壊で追記。
        #   Stage1 の species/scientific_name/confidence は不変。refined_* は別系統。
        for col, ddl in [
            ("refined_species", "TEXT"),        # Stage2 内部クラス(en, 例 Mallard)。OOD棄却時 NULL
            ("refined_label", "TEXT"),          # 表示ラベル(複合含む, 例 マガモ/カルガモ)
            ("refined_sci", "TEXT"),            # 学名(複合は slash, 例 Anas platyrhynchos/zonorhyncha)
            ("refined_confidence", "REAL"),     # Stage2 top1 確率
            ("refined_energy", "REAL"),         # OOD energy スコア(録音平均)
            ("refined_status", "TEXT"),         # 'refined' / 'ood_rejected'
            ("stage2_model_version", "TEXT"),   # 例 ast-duck-D-base-soup(モデル更新追跡)
            ("refined_group", "TEXT"),          # ディスパッチ群(duck/crow/gull…)
            ("refined_at", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE detections ADD COLUMN {col} {ddl}")


def set_refined(
    detection_id: int,
    *,
    refined_species: str | None,
    refined_label: str | None,
    refined_sci: str | None,
    refined_confidence: float | None,
    refined_energy: float | None,
    refined_status: str,
    stage2_model_version: str | None,
    refined_group: str | None,
) -> None:
    """Stage2 再分類結果を該当 detection 行に追記（Stage1 列は触らない）。"""
    from datetime import datetime, timezone, timedelta
    JST = timezone(timedelta(hours=9))
    ts = datetime.now(JST).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE detections SET refined_species=?, refined_label=?, refined_sci=?, "
            "refined_confidence=?, refined_energy=?, refined_status=?, "
            "stage2_model_version=?, refined_group=?, refined_at=? WHERE id=?",
            (refined_species, refined_label, refined_sci, refined_confidence, refined_energy,
             refined_status, stage2_model_version, refined_group, ts, detection_id),
        )


def set_human_label(detection_id: int, label: str | None) -> None:
    """人手検証ラベル（'correct' / 'wrong' / None）を付与する。"""
    from datetime import datetime, timezone, timedelta
    JST = timezone(timedelta(hours=9))
    ts = datetime.now(JST).isoformat() if label else None
    with _connect() as conn:
        conn.execute(
            "UPDATE detections SET human_label = ?, human_labeled_at = ? WHERE id = ?",
            (label, ts, detection_id),
        )


def get_detections(
    status: str | None = None,
    pi_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_conf: float | None = None,
    species: str | None = None,
    refined_status: str | None = None,
) -> list[sqlite3.Row]:
    """status / pi_id / date_from / date_to / min_conf / species / refined_status でフィルタした detections を新しい順に返す。"""
    sql = "SELECT * FROM detections"
    params: list = []
    conditions = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if pi_id:
        conditions.append("pi_id = ?")
        params.append(pi_id)
    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        from datetime import date, timedelta
        try:
            dt = date.fromisoformat(date_to) + timedelta(days=1)
            conditions.append("timestamp < ?")
            params.append(dt.isoformat())
        except ValueError:
            pass
    if min_conf is not None:
        conditions.append("confidence >= ?")
        params.append(min_conf)
    if species:
        conditions.append("species LIKE ?")
        params.append(f"%{species}%")
    if refined_status:
        conditions.append("refined_status = ?")
        params.append(refined_status)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY timestamp DESC"
    with _connect() as conn:
        return conn.execute(sql, params).fetchall()


def get_stats(
    date_from: str | None = None,
    date_to: str | None = None,
    min_conf: float = 0.0
) -> dict:
    """統計情報を返す。date_from/date_to/min_conf で期間を絞り込める。"""
    from datetime import date, timedelta
    
    # デフォルト: 直近10日
    today = date.today()
    if date_from is None:
        date_from = (today - timedelta(days=9)).isoformat()
    if date_to is None:
        date_to = today.isoformat()

    # timestamp + confidence の範囲条件
    cond   = "timestamp >= ? AND timestamp < ? AND confidence >= ?"
    params = (date_from, (date.fromisoformat(date_to) + timedelta(days=1)).isoformat(), min_conf)

    with _connect() as conn:
        # ステータス別件数（期間内）
        rows = conn.execute(
            f"SELECT status, COUNT(*) as cnt FROM detections WHERE {cond} GROUP BY status",
            params
        ).fetchall()
        status_counts = {r["status"]: r["cnt"] for r in rows}

        # Stage2 refined 別件数（期間内）: refined / ood_rejected
        rows = conn.execute(
            f"SELECT refined_status, COUNT(*) as cnt FROM detections "
            f"WHERE refined_status IS NOT NULL AND {cond} GROUP BY refined_status",
            params
        ).fetchall()
        refined_counts = {r["refined_status"]: r["cnt"] for r in rows}

        # 種別 confirmed 件数（上位20種）
        rows = conn.execute(
            f"""
            SELECT species, MAX(species_jp) as species_jp, COUNT(*) as count
            FROM detections
            WHERE status = 'confirmed' AND species IS NOT NULL AND {cond}
            GROUP BY species
            ORDER BY count DESC
            LIMIT 20
            """,
            params
        ).fetchall()
        top_species = [{"species": r["species"], "species_jp": r["species_jp"], "count": r["count"]} for r in rows]

        # 時間帯別件数（confirmed のみ）
        rows = conn.execute(
            f"""
            SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour, COUNT(*) as count
            FROM detections
            WHERE status = 'confirmed' AND {cond}
            GROUP BY hour
            ORDER BY hour
            """,
            params
        ).fetchall()
        hourly_map = {r["hour"]: r["count"] for r in rows}
        hourly = [{"hour": h, "count": hourly_map.get(h, 0)} for h in range(24)]

        # 日別検出件数（confirmed、期間内）
        rows = conn.execute(
            f"""
            SELECT date(timestamp) as day, COUNT(*) as count
            FROM detections
            WHERE status = 'confirmed' AND {cond}
            GROUP BY day
            ORDER BY day
            """,
            params
        ).fetchall()
        daily_counts = {r["day"]: r["count"] for r in rows}

        # 期間内の日別トレンド（全ステータス）
        start_date = date.fromisoformat(date_from)
        end_date   = date.fromisoformat(date_to)
        trend = []
        d = start_date
        while d <= end_date:
            ds = d.isoformat()
            rows_d = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM detections WHERE date(timestamp)=? AND confidence >= ? GROUP BY status",
                (ds, min_conf),
            ).fetchall()
            counts = {r["status"]: r["cnt"] for r in rows_d}
            trend.append({
                "date": ds,
                "confirmed": counts.get("confirmed", 0),
                "pending":   counts.get("pending", 0),
                "noise":     counts.get("noise", 0),
            })
            d += timedelta(days=1)

    return {
        "status_counts": status_counts,
        "refined_counts": refined_counts,
        "top_species":   top_species,
        "hourly":        hourly,
        "daily_counts":  daily_counts,
        "recent_7":      trend,
        "date_from":     date_from,
        "date_to":       date_to,
        "min_conf":      min_conf,
    }



def get_distinct_pi_ids() -> list[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT pi_id FROM detections ORDER BY pi_id").fetchall()
        return [row[0] for row in rows]


def update_status(detection_id: int, new_status: str) -> None:
    """レコードのステータスを更新する。"""
    with _connect() as conn:
        conn.execute(
            "UPDATE detections SET status = ? WHERE id = ?",
            (new_status, detection_id),
        )


def insert_detection(
    *,
    timestamp: str,
    file_path: str,
    species: str | None,
    confidence: float | None,
    status: str,
    pi_id: str,
    latitude: float | None,
    longitude: float | None,
    scientific_name: str | None = None,
    species_jp: str | None = None,
    det_count: int | None = None,
) -> int:
    sql = """
    INSERT INTO detections
        (timestamp, file_path, species, confidence, status, pi_id, latitude, longitude, scientific_name, species_jp, det_count)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with _connect() as conn:
        cur = conn.execute(
            sql,
            (timestamp, file_path, species, confidence, status, pi_id, latitude, longitude, scientific_name, species_jp, det_count),
        )
        return cur.lastrowid
