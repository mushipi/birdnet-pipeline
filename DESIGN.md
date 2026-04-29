# BirdProject 設計書

最終更新: 2026-03-17

---

## 目次

1. [システム概要](#1-システム概要)
2. [ハードウェア構成](#2-ハードウェア構成)
3. [ネットワーク構成](#3-ネットワーク構成)
4. [ソフトウェアアーキテクチャ](#4-ソフトウェアアーキテクチャ)
5. [Pi 側コンポーネント](#5-pi-側コンポーネント)
6. [N97 側コンポーネント](#6-n97-側コンポーネント)
7. [データフロー](#7-データフロー)
8. [DB スキーマ](#8-db-スキーマ)
9. [API エンドポイント](#9-api-エンドポイント)
10. [ファイル保存構造](#10-ファイル保存構造)
11. [設定仕様](#11-設定仕様)
12. [推論パラメータ](#12-推論パラメータ)
13. [systemd / cron 構成](#13-systemd--cron-構成)
14. [パラボリックマイク（自作）](#14-パラボリックマイク自作)

---

## 1. システム概要

野鳥の鳴き声を Raspberry Pi で継続録音し、N97 サーバー上で BirdNET による自動種判定を行い、
Web UI で観察記録として管理するシステム。

### 設計方針

- **観察記録単位**: 1種 × 1録音ファイル = 1 DB レコード（時間・場所・種が揃った観察記録）
- **自動判定 + 人間レビュー**: 確信度 70% 以上は自動確定、未満は人間がレビュー
- **Pi は薄く**: 録音と転送のみ。推論・DB・Web はすべて N97 側
- **Tailscale 経由**: Pi と N97 の通信はすべて Tailscale VPN 上で行う

---

## 2. ハードウェア構成

| 役割 | 機器 | 備考 |
|------|------|------|
| 録音端末 | Raspberry Pi (mushipi-bird01) | USB マイク接続、屋外設置想定 |
| 推論・Web サーバー | ACEMAGIC S1 (mushipiubuntuserver) | Intel N97 / 4コア / 7.5GB RAM / Ubuntu 24.04 |
| マイク | 自作パラボリックマイク | [詳細設計](#14-パラボリックマイク自作) 参照 |
| マイク素子 | Cubilux USB-A ラベリアマイク | φ12mm 単一指向性 ECM |

**録音仕様:**

| 項目 | 値 |
|------|-----|
| サンプリングレート | 48,000 Hz |
| ビット深度 | 16bit PCM (S16_LE) |
| チャンネル | モノラル (1ch) |
| セグメント長 | 60 秒（設定画面から変更可、最小 30 秒推奨） |
| ファイル名 | `YYYYMMDD_HHMMSS.wav` |
| ファイルサイズ | 約 5.5MB / ファイル（60秒時） |

---

## 3. ネットワーク構成

| ホスト | LAN IP | Tailscale IP | ホスト名 |
|--------|--------|--------------|----------|
| N97 サーバー | 192.168.31.234 | 100.102.9.77 | mushipiubuntuserver |
| Raspberry Pi | — | 100.78.71.38 | mushipi-bird01 |

- **Pi → N97**: rsync による WAV 転送（Tailscale IP、SSH 鍵認証）
- **N97 → Pi**: SSH による設定反映（`record_config.sh` 上書き・サービス再起動）
- **ブラウザ → N97**: Web UI アクセス（Tailscale 経由 `http://100.102.9.77:8765`）
- SSH 鍵: Pi の `~/.ssh/id_ed25519_n97` → N97 の `~/.ssh/authorized_keys` に登録済み

---

## 4. ソフトウェアアーキテクチャ

```
┌──────────────────────────────────────┐
│           Raspberry Pi               │
│                                      │
│  record_config.sh  ←── SSH (N97)    │
│  record.sh (systemd常駐)             │
│    └─ arecord → recordings/*.wav     │
│  sync_to_n97.sh (cron 10分)          │
│    └─ rsync → N97 raw_ingest/  ──────┼────┐
└──────────────────────────────────────┘    │
                                            ▼
┌───────────────────────────────────────────────────────┐
│                    N97 Server                         │
│                                                       │
│  raw_ingest/mushipi-bird01/  ← rsync で受信           │
│                                                       │
│  ingest_and_process.sh  (cron 10分)                   │
│    └─ process.py                                      │
│         ├─ sox: モノラル変換                           │
│         ├─ birdnetlib: BirdNET 推論                   │
│         ├─ 仕分け: detected/ / review/ / unknown/     │
│         └─ db.py: SQLite INSERT                       │
│                                                       │
│  web/app.py  (FastAPI, :8765)                         │
│    ├─ GET /          検出結果一覧                      │
│    ├─ GET /stats     統計                             │
│    ├─ GET /settings  設定 (保存時 Pi へ SSH 反映)      │
│    ├─ GET /help      使い方・仕様                      │
│    ├─ POST /ingest   手動取り込みトリガー              │
│    └─ GET /export/dataset  ZIP ダウンロード           │
└───────────────────────────────────────────────────────┘
```

---

## 5. Pi 側コンポーネント

### ファイル構成

```
/home/mushipi/
├── BirdProject/
│   ├── record_config.sh     # 録音設定（N97 設定画面から SSH で上書きされる）
│   ├── record.sh            # 録音ループスクリプト（systemd から起動）
│   └── sync_to_n97.sh       # N97 への rsync 転送（cron から起動）
├── recordings/              # 録音 WAV 一時置き場（転送後に自動削除）
└── sync.log                 # sync_to_n97.sh のログ

/etc/systemd/system/
└── bird-record.service

/etc/sudoers.d/
└── mushipi-bird             # systemctl restart を NOPASSWD で許可
```

### record_config.sh

```bash
RECORD_ALL_DAY=0    # 1: 24時間録音（時間帯フィルタ無効）、0: 時間帯フィルタ有効
RECORD_START=4      # 録音開始時刻（時、0〜23）
RECORD_STOP=22      # 録音停止時刻（時、0〜23）
SEGMENT_SEC=60      # 1ファイルあたりの録音長（秒）
```

N97 の設定画面で「保存」するたびに SSH 経由で上書きされ、`bird-record.service` が自動再起動される。

### record.sh

```bash
#!/bin/bash
source /home/mushipi/BirdProject/record_config.sh
mkdir -p /home/mushipi/recordings

while true; do
    hour=$(date +%-H)
    if [ "${RECORD_ALL_DAY:-0}" = "1" ] || \
       ([ "$hour" -ge "$RECORD_START" ] && [ "$hour" -lt "$RECORD_STOP" ]); then
        fname="/home/mushipi/recordings/$(date '+%Y%m%d_%H%M%S').wav"
        timeout "$SEGMENT_SEC" arecord -D hw:0,0 -f S16_LE -r 48000 -c 1 "$fname"
    else
        sleep 60
    fi
done
```

- `RECORD_ALL_DAY=1` の場合は時間チェックをスキップして常時録音
- 録音時間帯外は 60 秒スリープしてループ継続
- `timeout` によりセグメント長の強制終了（録音完了と同義）

### sync_to_n97.sh

```bash
#!/bin/bash
rsync -az --remove-source-files \
  -e "ssh -i /home/mushipi/.ssh/id_ed25519_n97" \
  /home/mushipi/recordings/ \
  mushipi@100.102.9.77:/home/mushipi/BirdProject/raw_ingest/mushipi-bird01/
```

- `--remove-source-files`: 転送成功したファイルを Pi 側から自動削除
- Pi ストレージの枯渇を防ぐ。Tailscale 障害時は削除されず残り続ける（次回転送時に送られる）

### bird-record.service

```ini
[Unit]
Description=BirdProject Recording
After=sound.target

[Service]
ExecStart=/home/mushipi/BirdProject/record.sh
Restart=always
RestartSec=10
StartLimitIntervalSec=0
User=mushipi

[Install]
WantedBy=multi-user.target
```

| 設定 | 値 | 意図 |
|------|-----|------|
| `Restart=always` | — | クラッシュ・正常終了を問わず再起動 |
| `RestartSec=10` | 10秒 | USB マイク再認識の待機時間（5秒から延長） |
| `StartLimitIntervalSec=0` | 無制限 | 連続クラッシュでも systemd が諦めない |

### Pi の crontab

```
*/10 * * * * /bin/bash /home/mushipi/BirdProject/sync_to_n97.sh >> /home/mushipi/sync.log 2>&1
```

### sudoers 設定

```
# /etc/sudoers.d/mushipi-bird
mushipi ALL=(ALL) NOPASSWD: /bin/systemctl restart bird-record.service
```

---

## 6. N97 側コンポーネント

### ファイル構成

```
/home/mushipi/BirdProject/
├── process.py               # BirdNET 推論・仕分け・DB 記録
├── db.py                    # SQLite 操作層
├── run_ingest.sh            # systemd timer から呼ばれる取り込みスクリプト
├── settings.json            # 動作設定（Web UI から更新）
├── species_blacklist.json   # 誤検知しやすい種のブラックリスト
├── README.md                # プロジェクト概要・セットアップ
├── DESIGN.md                # 本設計書
├── DEVLOG.md                # 開発記録
├── raw_ingest/
│   └── mushipi-bird01/      # Pi から rsync で届く WAV 受け皿
├── processed/               # 推論済みファイル（後述）
│   └── {YYYY-MM-DD}/
│       ├── detected/        # confirmed（noise WAV は自動削除）
│       ├── review/          # pending
│       └── unknown/         # noise（run_ingest.sh 実行後に WAV 自動削除）
├── db/
│   └── bird_calls.db        # SQLite データベース
├── ingest.log               # run_ingest.sh のログ
├── docs/
│   └── hardware/
│       └── parabolic_mic.md # 自作パラボリックマイク設計書
├── archive/                 # 過去の調査・検討資料
├── web/
│   ├── app.py               # FastAPI アプリケーション
│   └── templates/
│       ├── index.html       # 検出結果一覧
│       ├── stats.html       # 統計
│       ├── settings.html    # 設定
│       └── help.html        # 使い方・仕様
├── pi-scripts/              # Pi（通常モデル）用スクリプトのマスターコピー
├── pi-scripts-zerow/        # Pi Zero W 最適化版スクリプト
└── .venv/                   # uv が管理する仮想環境
```

### process.py の処理フロー

```
WAV ファイル（raw_ingest/mushipi-bird01/）
  │
  ├─ parse_timestamp(): ファイル名の YYYYMMDD_HHMMSS からタイムスタンプ取得
  │                     失敗時は mtime を使用
  │
  ├─ to_mono(): sox でモノラル変換 → 一時ファイル
  │
  ├─ analyze_file(): birdnetlib Recording.analyze()
  │     内部: 3秒チャンク × overlap=1.5秒 でスライド推論
  │     戻り値: [{common_name, scientific_name, confidence, start_time, end_time}, ...]
  │             confidence 降順ソート済み
  │
  ├─ aggregate_by_species(): CONF_HIGH(0.7) 以上の種を集約
  │     同種は最高 confidence のエントリのみ残す
  │
  ├─ [confirmed あり]
  │     ファイルを detected/{scientific_name}/ へ move
  │     種ごとに confirmed レコードを INSERT（1ファイルから複数レコード可）
  │
  ├─ [confirmed なし、CONF_LOW(0.1) 以上あり]
  │     ファイル名に確信度・Top1種名を付与して review/ へ move
  │     pending レコードを 1件 INSERT
  │
  └─ [全部 CONF_LOW 未満]
        unknown/ へ move
        noise レコードを 1件 INSERT（species=NULL, confidence=NULL）
```

### 依存パッケージ

| パッケージ | バージョン制約 | 用途 |
|-----------|--------------|------|
| birdnetlib | — | BirdNET 推論ラッパー |
| fastapi | — | Web フレームワーク |
| uvicorn | — | ASGI サーバー |
| jinja2 | — | HTML テンプレート |
| numpy | **<2 固定必須** | tflite-runtime が NumPy 1.x ビルドのため |

> **numpy<2 を外すと `SystemError` で tflite-runtime がクラッシュする。絶対に外さないこと。**

### ingest_and_process.sh

```bash
#!/bin/bash
# ロックファイルで二重起動防止
# raw_ingest/mushipi-bird01/ に WAV がある場合のみ process.py を実行

WAV_COUNT=$(find "$PROJECT_DIR/raw_ingest/mushipi-bird01" -maxdepth 1 -name '*.wav' 2>/dev/null | wc -l)
if [ "$WAV_COUNT" -gt 0 ]; then
    uv run python process.py --pi-id mushipi-bird01
fi
```

---

## 7. データフロー

### 録音から DB 記録まで

```
[Pi] arecord (SEGMENT_SEC 秒)
  → /home/mushipi/recordings/YYYYMMDD_HHMMSS.wav

[Pi cron 10分] sync_to_n97.sh
  → rsync → N97: raw_ingest/mushipi-bird01/YYYYMMDD_HHMMSS.wav
  → 転送成功後、Pi 側ファイルを削除（--remove-source-files）

[N97 cron 10分] ingest_and_process.sh
  → process.py --pi-id mushipi-bird01
    → birdnetlib 推論 (3秒チャンク × overlap=1.5秒)
    → 仕分け + db.insert_detection()
    → WAV を processed/ 以下へ move
```

### 設定変更フロー

```
[ブラウザ] POST /settings (フォーム送信)
  → settings.json 更新
  → crontab 更新 (*/N * * * * ingest_and_process.sh)
  → SSH: echo "RECORD_ALL_DAY=...\n..." | cat > Pi:~/BirdProject/record_config.sh
  → SSH: sudo systemctl restart bird-record.service
```

---

## 8. DB スキーマ

```sql
CREATE TABLE IF NOT EXISTS detections (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT NOT NULL,   -- 録音開始時刻 (ISO 8601)。ファイル名から抽出、失敗時は mtime
    file_path      TEXT NOT NULL,   -- 推論後のファイルパス（複数レコードが同一パスを共有する場合あり）
    species        TEXT,            -- 種名 (English common name)。noise の場合 NULL
    confidence     REAL,            -- BirdNET 確信度 (0.0〜1.0)。noise の場合 NULL
    status         TEXT NOT NULL CHECK(status IN ('confirmed', 'pending', 'noise')),
    pi_id          TEXT NOT NULL DEFAULT 'unknown',  -- 録音元 Pi 識別子
    latitude       REAL,            -- 録音地点の緯度
    longitude      REAL,            -- 録音地点の経度
    scientific_name TEXT,           -- 学名
    species_jp     TEXT,            -- 和名
    det_count      INTEGER DEFAULT 1 -- 同一ファイル内での検出回数
);
```

**PRAGMA:** `journal_mode=WAL`（並行アクセス対応）

### ステータス定義

| status | 自動付与条件 | 手動変更 |
|--------|------------|---------|
| confirmed | confidence ≥ 0.65 | pending → confirmed ボタン |
| pending | 0.25 ≤ confidence < 0.65 | — |
| noise | confidence < 0.25 | pending → noise ボタン |

### 1ファイル複数レコードの例

同一 WAV に複数種が confirmed された場合、同じ `file_path` を持つ複数レコードが作られる。

```
id=1  timestamp=2026-03-18T04:05:00  species="Japanese Bush Warbler"  confidence=0.85
      status=confirmed  file_path=processed/2026-03-18/detected/Horornis_diphone/20260318_040500.wav

id=2  timestamp=2026-03-18T04:05:00  species="Brown-eared Bulbul"     confidence=0.72
      status=confirmed  file_path=processed/2026-03-18/detected/Horornis_diphone/20260318_040500.wav
```

---

## 9. API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/` | 検出結果一覧 |
| GET | `/stats` | 統計（ステータス別件数・上位種・時間帯別） |
| GET | `/settings` | 設定画面 |
| POST | `/settings` | 設定保存 + crontab 更新 + Pi 設定反映 |
| GET | `/help` | 使い方・仕様ページ |
| POST | `/ingest` | 手動取り込みトリガー（非同期） |
| GET | `/ingest/status` | 取り込み実行状態 JSON（running, last_run, log_tail） |
| POST | `/update/{id}` | レコードのステータス変更 |
| GET | `/audio/{file_path}` | WAV ファイル配信 |
| GET | `/export/dataset` | confirmed レコードの WAV を ZIP ダウンロード |

### GET / クエリパラメータ

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| status | str | `confirmed` / `pending` / `noise` |
| pi_id | str | Pi 識別子で絞り込み |
| date_from | str | 日付下限 (YYYY-MM-DD) |
| date_to | str | 日付上限 (YYYY-MM-DD、当日含む) |
| min_conf | float | 確信度下限（0〜100 の % 値） |
| species | str | 種名の部分一致検索（LIKE %...%） |

---

## 10. ファイル保存構造

```
BirdProject/processed/
└── {YYYY-MM-DD}/
    ├── detected/                          # confirmed（confidence ≥ 0.7）
    │   └── {Scientific_Name}/             # 科学名（スペース→アンダースコア）
    │       └── YYYYMMDD_HHMMSS.wav        # 元ファイル名を保持
    ├── review/                            # pending（0.1 ≤ confidence < 0.7）
    │   └── YYYYMMDD_HHMMSS_ConfXX_TopSpecies.wav
    │       # XX: 確信度 0〜99の整数, TopSpecies: スペース→アンダースコア
    └── unknown/                           # noise（confidence < 0.1）
        └── YYYYMMDD_HHMMSS.wav
```

- 物理ファイルは 1 WAV につき 1 つのみ
- 複数種が confirmed された場合、ファイルは Top1（最高確信度）の科学名ディレクトリに保存
- 複数 DB レコードが同一 `file_path` を参照する

---

## 11. 設定仕様

### settings.json

```json
{
  "ingest_interval_min": 10,
  "pi_host": "mushipi@100.78.71.38",
  "pi_recordings": "/home/mushipi/recordings/",
  "record_all_day": true,
  "record_start_hour": 4,
  "record_stop_hour": 22,
  "segment_sec": 60,
  "processed_dir": "/mnt/hamcam/BirdProject/processed",
  "raw_ingest_dir": "/home/mushipi/BirdProject/raw_ingest",
  "db_path": "/mnt/hamcam/BirdProject/db/bird_calls.db"
}
```

| キー | 型 | 説明 |
|------|-----|------|
| ingest_interval_min | int | systemd timer の実行間隔（分） |
| pi_host | str | Pi の SSH/rsync 接続先 |
| pi_recordings | str | Pi の録音ディレクトリ（末尾 `/` 必須） |
| record_all_day | bool | true: 24時間録音、false: 時間帯フィルタ有効 |
| record_start_hour | int | 録音開始時刻（時、0〜23）。record_all_day=false 時のみ有効 |
| record_stop_hour | int | 録音停止時刻（時、0〜23）。record_all_day=false 時のみ有効 |
| segment_sec | int | 録音セグメント長（秒、最小 30 推奨） |
| processed_dir | str | 推論済み WAV の保存先（外部 HDD `/mnt/hamcam` 推奨） |
| raw_ingest_dir | str | Pi から rsync される WAV の受け皿 |
| db_path | str | SQLite DB のパス（外部 HDD `/mnt/hamcam` 推奨） |

---

## 12. 推論パラメータ

| パラメータ | 値 | 変更方法 |
|-----------|-----|---------|
| モデル | BirdNET_GLOBAL_6K_V2.4 | birdnetlib のデフォルト（TFLite FP32） |
| チャンク長 | 3 秒 | BirdNET 固定値（変更不可） |
| overlap | 2.0 秒 | `process.py` の `Recording()` の `overlap=` 引数 |
| sensitivity | 1.25 | `process.py` の `Recording()` の `sensitivity=` 引数 |
| min_conf | 0.25 | `process.py` の `CONF_LOW`（これ未満は API 側でフィルタ） |
| confirmed 閾値 | 0.65 | `process.py` の `CONF_HIGH` |
| latitude | 33.57869 | `process.py --latitude` または `process.py` の argparse デフォルト |
| longitude | 130.257151 | `process.py --longitude` または `process.py` の argparse デフォルト |

### overlap の効果

overlap=2.0 秒では、3秒窓を 1.0 秒ずつずらして分析するため、
60 秒の録音で約 58 窓を分析できる（overlap なしの約 3 倍）。
短いセグメントでの検知漏れを減らす効果がある。

### sensitivity の効果

sensitivity=1.25（デフォルト 1.0）により、遠距離や小音量の鳴き声も検知しやすくなる。
上げすぎると false positive が増えるため、CONF_HIGH で confirmed 閾値を管理して品質を維持する。

---

## 13. systemd / cron 構成

### Pi 側

```
systemd: bird-record.service  (enabled, 常駐)
  └─ /home/mushipi/BirdProject/record.sh

crontab (mushipi):
  */10 * * * * /bin/bash /home/mushipi/BirdProject/sync_to_n97.sh >> /home/mushipi/sync.log 2>&1
```

### N97 側

```
crontab (mushipi):
  */10 * * * * /bin/bash /home/mushipi/BirdProject/ingest_and_process.sh

uvicorn (手動起動):
  cd /home/mushipi/BirdProject/web
  uv run uvicorn app:app --host 0.0.0.0 --port 8765
  → http://100.102.9.77:8765

ロックファイル: /tmp/birdproject_ingest.lock
ログ: /home/mushipi/BirdProject/ingest.log
```

---

## 14. パラボリックマイク（自作）

長距離の録音精度を向上させるため、3D プリンタで製作する専用のパラボリックマイクを併用する。

- **設計思想**: 4分割出力可能な大口径パラボラ（φ300mm）
- **焦点距離**: 74mm
- **詳細仕様**: [パラボリックマイク設計書](/docs/parabolic) を参照
