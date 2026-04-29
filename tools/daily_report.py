"""
BirdProject デイリーレポート
前日（JST）の検出サマリを集計して Gmail で送信する。
"""

import json
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "settings.json"
MAIL_CONFIG_FILE = BASE_DIR / "mail_config.json"

JST = timezone(timedelta(hours=9))


def load_db_path() -> str:
    s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return s["db_path"]


def load_mail_config() -> dict:
    return json.loads(MAIL_CONFIG_FILE.read_text(encoding="utf-8"))


def get_summary(db_path: str, start_iso: str, end_iso: str) -> tuple[dict, list, dict, dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT status, COUNT(*) as cnt FROM detections "
        "WHERE timestamp >= ? AND timestamp < ? GROUP BY status",
        (start_iso, end_iso),
    )
    status_counts = {row["status"]: row["cnt"] for row in cur.fetchall()}

    cur.execute(
        "SELECT species, species_jp, scientific_name, COUNT(*) as cnt, "
        "MAX(confidence) as max_conf, MIN(timestamp) as first_seen "
        "FROM detections "
        "WHERE timestamp >= ? AND timestamp < ? AND status = 'confirmed' "
        "GROUP BY species ORDER BY cnt DESC",
        (start_iso, end_iso),
    )
    species_list = [dict(row) for row in cur.fetchall()]

    cur.execute(
        "SELECT substr(timestamp, 12, 2) as hour, COUNT(*) as cnt "
        "FROM detections "
        "WHERE timestamp >= ? AND timestamp < ? AND status IN ('confirmed', 'pending') "
        "GROUP BY hour ORDER BY hour",
        (start_iso, end_iso),
    )
    hourly = {row["hour"]: row["cnt"] for row in cur.fetchall()}

    cur.execute(
        "SELECT pi_id, COUNT(*) as cnt FROM detections "
        "WHERE timestamp >= ? AND timestamp < ? GROUP BY pi_id",
        (start_iso, end_iso),
    )
    pi_counts = {row["pi_id"]: row["cnt"] for row in cur.fetchall()}

    conn.close()
    return status_counts, species_list, hourly, pi_counts


def render_html(date_str: str, status_counts: dict, species_list: list,
                hourly: dict, pi_counts: dict) -> str:
    confirmed = status_counts.get("confirmed", 0)
    pending = status_counts.get("pending", 0)
    noise = status_counts.get("noise", 0)
    total = confirmed + pending + noise

    if total == 0:
        return f"""<html><body>
<h2>BirdProject デイリーレポート — {date_str}</h2>
<p>この日の検出記録はありませんでした。</p>
</body></html>"""

    species_rows = "\n".join(
        f"<tr><td>{s['species'] or '-'}</td>"
        f"<td>{s['species_jp'] or '-'}</td>"
        f"<td style='text-align:right'>{s['cnt']}</td>"
        f"<td style='text-align:right'>{s['max_conf']:.2f}</td>"
        f"<td>{s['first_seen'][11:16] if s['first_seen'] else '-'}</td></tr>"
        for s in species_list
    ) or "<tr><td colspan='5'>confirmed なし</td></tr>"

    max_hourly = max(hourly.values()) if hourly else 1
    hourly_rows = "\n".join(
        f"<tr><td>{h}:00</td>"
        f"<td style='text-align:right'>{hourly.get(h, 0)}</td>"
        f"<td><div style='background:#4a90e2;height:14px;"
        f"width:{int(hourly.get(h, 0) / max_hourly * 200)}px'></div></td></tr>"
        for h in [f"{i:02d}" for i in range(24)]
        if hourly.get(h, 0) > 0
    )

    pi_rows = "\n".join(
        f"<tr><td>{pid}</td><td style='text-align:right'>{cnt}</td></tr>"
        for pid, cnt in pi_counts.items()
    )

    return f"""<html><body style="font-family: sans-serif; max-width: 720px;">
<h2>BirdProject デイリーレポート — {date_str}</h2>

<h3>サマリ</h3>
<table border="1" cellpadding="6" style="border-collapse:collapse">
<tr><th>confirmed</th><th>pending</th><th>noise</th><th>合計</th></tr>
<tr>
  <td style="text-align:right">{confirmed}</td>
  <td style="text-align:right">{pending}</td>
  <td style="text-align:right">{noise}</td>
  <td style="text-align:right"><b>{total}</b></td>
</tr>
</table>

<h3>確認された種（confirmed）</h3>
<table border="1" cellpadding="6" style="border-collapse:collapse">
<tr><th>英名</th><th>和名</th><th>件数</th><th>最高信頼度</th><th>初観察時刻</th></tr>
{species_rows}
</table>

<h3>時間帯別 検出活動量（confirmed + pending）</h3>
<table cellpadding="4" style="border-collapse:collapse">
<tr><th>時刻</th><th>件数</th><th></th></tr>
{hourly_rows}
</table>

<h3>Pi 別件数</h3>
<table border="1" cellpadding="6" style="border-collapse:collapse">
<tr><th>Pi ID</th><th>件数</th></tr>
{pi_rows}
</table>

<p style="color:#888;font-size:12px;margin-top:24px">
Web UI: <a href="http://100.102.9.77:8765">http://100.102.9.77:8765</a>
</p>
</body></html>"""


def send_mail(mail_config: dict, html_body: str, date_str: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["From"] = mail_config["from_addr"]
    msg["To"] = mail_config["to_addr"]
    msg["Subject"] = f"[BirdProject] デイリーレポート {date_str}"
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(mail_config["smtp_user"], mail_config["smtp_password"].replace(" ", ""))
        server.send_message(msg)


def main():
    yesterday = (datetime.now(JST) - timedelta(days=1)).date()
    start = datetime.combine(yesterday, datetime.min.time(), JST)
    end = start + timedelta(days=1)
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%S")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%S")

    db_path = load_db_path()
    mail_config = load_mail_config()

    status_counts, species_list, hourly, pi_counts = get_summary(db_path, start_iso, end_iso)
    html = render_html(yesterday.strftime("%Y-%m-%d"), status_counts, species_list, hourly, pi_counts)
    send_mail(mail_config, html, yesterday.strftime("%Y-%m-%d"))
    print(f"[OK] Daily report sent for {yesterday}")


if __name__ == "__main__":
    main()
