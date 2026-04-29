# BirdProject 開発記録

---

## 2026-03-05 — Phase 1 完了: 推論基盤

### 実装内容

- `process.py`: BirdNET 推論・仕分け・DB 記録の CLI スクリプト
- `db.py`: SQLite アクセス層（WAL モード、`row_factory=sqlite3.Row`）
- `raw_ingest/` に手動配置した WAV を処理する最小構成で動作確認

### DB スキーマ（当時）

```sql
CREATE TABLE detections (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT NOT NULL,
    file_path        TEXT NOT NULL,
    top_1_species    TEXT,
    top_1_confidence REAL,
    top_2_species    TEXT,
    top_2_confidence REAL,
    status           TEXT NOT NULL CHECK(status IN ('confirmed', 'pending', 'noise'))
);
-- マイグレーションで追加: pi_id, latitude, longitude
```

### 仕分けロジック（当時）

- `top_1_confidence ≥ 0.7` → `detected/{Scientific_name}/` / `confirmed`
- `0.1 ≤ top_1_confidence < 0.7` → `review/` / `pending`
- `< 0.1` → `unknown/` / `noise`
- 1ファイル = 1レコード（Top1・Top2 のみ記録）

### 技術的決定事項

- **numpy<2 固定**: `tflite-runtime` が NumPy 1.x ビルドのため。
  外すと `SystemError` で BirdNET がクラッシュすることを確認済み。
- **uv を使用**: pip/venv の代わりに `uv` でパッケージ管理を統一

---

## 2026-03-17 — Phase 2 完了: Pi 自動転送・Web UI・設定管理

### 実装内容（N97 側）

#### Web UI 構築（FastAPI + Jinja2）

- `web/app.py`: FastAPI アプリ（`:8765`）
- `web/templates/index.html`: 検出結果一覧（タブ・フィルタ・音声プレーヤー・ステータス変更）
- `web/templates/stats.html`: ステータス別件数・上位種ランキング・時間帯別棒グラフ
- `web/templates/settings.html`: 設定フォーム（保存時に crontab + Pi 設定を連動更新）
- `web/templates/help.html`: システム仕様・使い方の包括的ドキュメント

#### ingest_and_process.sh の簡略化

当初は N97 から Pi への rsync pull + SSH 削除も行っていたが、
**Pi push 一本化方針**に変更し、以下を削除:

- `rsync -az "$PI_HOST:$PI_RECORDINGS" "$INGEST_DIR/"` （pull）
- SSH による Pi 側ファイル削除ブロック

**理由**: Pi 側の `sync_to_n97.sh` が `--remove-source-files` で push + 自動削除するため、
N97 側での pull と削除が不要になった。二重削除による競合エラーが発生していたことも要因。

#### settings.json の整備

```json
{
  "ingest_interval_min": 10,
  "pi_host": "mushipi@100.125.31.76",
  "pi_recordings": "/home/mushipi/recordings/",
  "record_all_day": false,
  "record_start_hour": 4,
  "record_stop_hour": 22,
  "segment_sec": 60
}
```

設定保存時に crontab と Pi の `record_config.sh` を自動更新する `push_record_config_to_pi()` を実装。

#### DB スキーマ変更（破壊的変更、本番データなしのため実施）

旧: `top_1_species`, `top_1_confidence`, `top_2_species`, `top_2_confidence`
新: `species`, `confidence`

**理由**: 1ファイル1レコードは「録音の都合に引きずられた設計」であり、
観察記録として「この時間・場所でこの種を確認した」を単位とするため、
1ファイル × 1種 = 1レコードに変更。

#### 複数種検知ロジックの実装

```python
# 確信度 0.7 以上の種を集約（同種は最高確信度のみ）
confirmed = aggregate_by_species(detections, CONF_HIGH)

# 種ごとに INSERT
for det in confirmed.values():
    db.insert_detection(species=det["common_name"], confidence=det["confidence"], ...)
```

#### 推論パラメータの最適化

- `overlap=1.5` を `Recording()` に追加。
  60 秒録音で分析窓が約 20 → 約 39 に増加し、検知機会が約 2 倍に。

### 実装内容（Pi 側）

#### Pi セットアップ

N97 上に `pi-scripts/` ディレクトリを作成し、Pi への転送・インストールを一括化。

```
pi-scripts/
├── record_config.sh   # 録音設定テンプレート
├── record.sh          # 録音ループ（timeout + 時間帯フィルタ）
├── sync_to_n97.sh     # rsync push スクリプト
├── bird-record.service # systemd ユニット
└── install_pi.sh      # セットアップ一括スクリプト
```

#### bird-record.service の改善

```ini
RestartSec=5 → 10   # USB マイク再認識待機時間を延長
StartLimitIntervalSec=0  # 追加: 連続クラッシュでも諦めない
```

**理由**: USB マイクの認識に 5 秒以上かかる場合があり、
再起動直後に `arecord` が `hw:0,0` を掴めずクラッシュするループが発生していた。
`StartLimitIntervalSec=0` は systemd のデフォルト（10回クラッシュで停止）を無効化する。

#### 録音セグメント長の変更

`SEGMENT_SEC=300`（5分）→ `SEGMENT_SEC=60`（1分）

**理由**:
- 5分では1ファイルに複数種が混在しタイムスタンプ精度が低い（最大5分の誤差）
- 1分にすることで観察記録の時刻精度が向上
- BirdNET の検知精度はファイル長に依存しないため品質への影響なし
- 1日あたりのファイル数: 216 → 1,080（18時間録音時）

#### 録音時間帯フィルタの実装

```bash
if [ "${RECORD_ALL_DAY:-0}" = "1" ] || \
   ([ "$hour" -ge "$RECORD_START" ] && [ "$hour" -lt "$RECORD_STOP" ]); then
    # 録音
else
    sleep 60
fi
```

N97 の設定画面から `record_start_hour` / `record_stop_hour` / `record_all_day` を変更でき、
保存時に SSH で Pi の `record_config.sh` を上書きしてサービスを再起動する。

#### sudoers 設定

```
mushipi ALL=(ALL) NOPASSWD: /bin/systemctl restart bird-record.service
```

N97 から SSH で `sudo systemctl restart` をパスワードなしで実行するために必要。

#### push 先パスの修正

旧: `raw_ingest/pi-01/`
新: `raw_ingest/mushipi-bird01/`

**理由**: `process.py` のデフォルト `--pi-id` と `ingest_and_process.sh` が
`mushipi-bird01` を期待していたが、`sync_to_n97.sh` が `pi-01/` に push していた不一致を解消。

### トラブルシューティング記録

#### CRLF 改行コードによる bash エラー

Pi に scp で転送したスクリプトが `\r: command not found` エラーで実行できなかった。
N97 上で作成したファイルに Windows 改行コード（CRLF）が混入していたことが原因。

```bash
sed -i "s/\r//" ~/pi-setup/install_pi.sh
```

で解消。Pi 側の `record.sh` も同様に変換が必要だった。

#### bird-record.service が status=203/EXEC で起動失敗

`install_pi.sh` で `chmod +x` を行ったが、スクリプト内部に CRLF が残っており
`/bin/bash\r` が実行できなかった。CRLF 変換 + 再 `chmod +x` で解消。

#### Web UI が 500 エラー

`db.init_db()` が `process.py` からしか呼ばれておらず、
Web アプリ起動直後に `get_detections()` を呼ぶと DB テーブルが存在せずエラーになった。

`app.py` のモジュールトップに `db.init_db()` を追加して解消。

---

## 既知の制限・TODO

- [ ] N97 の Web サーバー（uvicorn）が `birdnet-web.service` として systemd 管理されているが、自動起動設定の確認が必要
- [ ] Pi が複数台になった場合の設定画面対応（現在は1台固定）
- [ ] `push_record_config_to_pi()` のエラーハンドリングなし（SSH 失敗が設定保存後に表示されない）
- [ ] 録音地点（latitude/longitude）が `process.py` のハードコードのみ。設定画面からの変更不可
- [ ] Pi のディスク残量監視なし（Tailscale 障害時に録音ファイルが Pi に溜まる可能性）

---

## 2026-03-20 — パラボリックマイク設計資料の統合

### 実装内容

- `handover_parabolic_mic.md`: 自作パラボリックマイクの詳細設計（光学設計、BackPlate、分割方針）をドキュメント化
- `web/app.py`: `/docs/parabolic` ルートを追加し、Web UI から設計資料を閲覧可能に修正
- `DESIGN.md`: ハードウェア構成表を更新し、パラボリックマイクの章を追加
- `web/templates/help.html`: パラボリックマイク設計書へのリンクを追加

---

## 2026-04-29 — Phase 3: 推論精度向上・Pi 移行・インフラ復旧

### 推論パラメータ改善（`process.py`）

すべての改善提案（inference_review.md）を実装済みの状態から確認。唯一未適用だった CONF_HIGH を調整：

| パラメータ | 旧値 | 新値 | 理由 |
|-----------|------|------|------|
| `CONF_HIGH` | 0.7 | **0.65** | 0.65〜0.70 帯が Japanese Bush Warbler・Common Kingfisher 等の信頼できる種で占められていたため引き下げ |

（他のパラメータ `CONF_LOW=0.25`, `overlap=2.0`, `sensitivity=1.25`, ハイパスフィルタ, det_count, ブラックリストは既に実装済み）

### Pi 移行（mushipi-bird01 の Tailscale ノード更新）

旧ノード `mushipi-bird01` (100.125.31.76) が16日間 offline に。新ノード (100.78.71.38) が active かつ録音継続中と判明。

- `settings.json`: `pi_host` を `mushipi@100.78.71.38` に更新、全パスを `/mnt/hamcam/BirdProject/` → `/home/mushipi/BirdProject/` に修正
- `web/app.py`: `DEFAULT_SETTINGS` の `pi_host` を更新
- Pi 側 `sync_to_n97.sh`: 転送先パスを修正（`/mnt/hamcam/` → `/home/mushipi/`）
- N97 `~/.ssh/authorized_keys`: Pi の新規鍵（`id_ed25519_n97` 2026-04-26 作成）に更新
- N97 `raw_ingest/mushipi-bird01/` および `processed/` ディレクトリを新規作成

**原因まとめ**: `/mnt/hamcam/` は外部ストレージのマウントポイントだったが、デバイスが切断された状態のままパスが残っていた。Pi 側でも SSH 鍵が 4/26 に再生成されていたが N97 に反映されていなかった。

### インフラ改善

- `run_ingest.sh`: 処理後に `processed/*/unknown/*.wav` を自動削除してストレージを節約
  （noise は DB に記録済みのため WAV の保持は不要）
- systemd timer (`birdnet-ingest.timer`) で 10 分ごとの自動処理を確認

### ドキュメント整理

- `handover_parabolic_mic.md` → `docs/hardware/parabolic_mic.md` に移動
- `vm_q1_report.md` → `archive/` に移動
- `inference_review.md` → `archive/inference_review_2026-03-20.md` に移動（提案内容は実装完了）
- `DESIGN.md`: Pi IP・推論パラメータ・DB スキーマ・ファイル構成を現状に合わせて更新
- `README.md` を新規作成

### hamcam（外部 HDD）の再マウントと保存先切り替え

外部 HDD（`/dev/sdc2`、UUID=B4BE3166BE3121F2、932GB）が `/mnt/hamcam` にマウントされていなかった。
devmon が `/media/devmon/ボリューム` に自動マウントしていたため、BirdProject の設定と実体がずれていた。

**対応内容:**

- `/etc/fstab` に `/mnt/hamcam` エントリを追加（UUID=B4BE3166BE3121F2、ntfs-3g）
  ```
  UUID=B4BE3166BE3121F2  /mnt/hamcam  ntfs-3g  defaults,nofail,uid=1000,gid=1000,umask=000  0  0
  ```
- devmon からアンマウントし `/mnt/hamcam` に再マウント
- `sudo systemctl daemon-reload` と `systemctl --user daemon-reload` を実行

**DB マージ（旧 hamcam DB + 新 DB）:**

| DB | 件数 | 期間 |
|----|------|------|
| 旧 hamcam DB | 3,592件 | 〜2026-03-21 |
| 新 DB（home） | 2,480件 | 2026-04-26〜 |
| **マージ後** | **6,072件** | 重複なし |

- SQLite ATTACH を使って新 DB のデータを旧 DB に追記
- `processed/` ファイル（6.9GB）を rsync で hamcam に移動

**settings.json の最終パス構成:**

```json
{
  "processed_dir": "/mnt/hamcam/BirdProject/processed",
  "raw_ingest_dir": "/home/mushipi/BirdProject/raw_ingest",
  "db_path": "/mnt/hamcam/BirdProject/db/bird_calls.db"
}
```

- `raw_ingest_dir` のみ N97 本体に保持（処理後すぐ hamcam に移動されるため、一時置き場は本体で十分）
- `run_ingest.sh`: noise 削除パスを `$SCRIPT_DIR/processed` から `settings.json` の `processed_dir` を参照するよう修正

---

## 2026-04-29 — デイリーレポート機能（Gmail）

### 実装内容

- `tools/daily_report.py`: 前日（JST）の検出サマリを HTML メールで送信
- `mail_config.json`（パーミッション 600）: SMTP 認証情報。`bugsy.agent@gmail.com` のアプリパスワード使用
- `birdnet-daily-report.service` / `.timer`: systemd user timer（`OnCalendar=*-*-* 07:00:00 Asia/Tokyo`）
- `.gitignore` を新規作成（`mail_config.json` ほか機密・大容量を除外）

### レポート内容

- ステータス別件数（confirmed / pending / noise）
- confirmed 種一覧（英名・和名・件数・最高信頼度・初観察時刻）
- 時間帯別の活動量（バーチャート）
- Pi 別件数

---

## 2026-04-29 — Phase 4: 誤検知削減（即時対応）

### 4-1. ブラックリスト拡充

`species_blacklist.json` に追加:
- `Botaurus stellaris`（サンカノゴイ・九州ではほぼ記録なし）
- `Gavia stellata`（アビ・35件は誤検知濃厚）
- `Cathartes aura`（ヒメコンドル・日本に生息せず、Carrion Crow の誤マッピング）

### 4-2. 過去 confirmed の遡及降格

ブラックリスト全種の confirmed/pending を一括 noise へ降格（**約 350件** 排除）。
特に `Gavia stellata` の pending 164件、`Botaurus stellaris` の pending 33件など。

### 4-3. 和名マスタ修正

`update_metadata.py` が iNaturalist API の不正確な検索結果で DB の `scientific_name` を上書きしていた問題を解消：
- `tools/fix_species_metadata.py` を新規作成
- `species_cache.json` 21件 + DB の `scientific_name` / `species_jp` を **1940件**一括修正
- 主な修正: Carrion Crow → Corvus corone（ハシボソガラス）、Rook → Corvus frugilegus（ミヤマガラス）、Whimbrel → Numenius phaeopus（チュウシャクシギ）等

### 4-A. UI からブラックリスト管理

- `web/templates/blacklist.html` 新規作成（一覧表示・追加・削除・遡及降格）
- `web/templates/index.html` の各検出カードに「除外登録」ボタン
- `web/app.py` に API 追加：
  - `GET /blacklist` 管理画面
  - `POST /api/blacklist/add` （retroactive オプションで過去 confirmed/pending を一括 noise）
  - `POST /api/blacklist/remove`

---

## 2026-04-29 — Phase 5: モデル性能向上（基盤実装）

### 5-A. BirdNET Species List 対応

- `process.py` に `species_list_file` 設定読み込みを追加
- `analyzer.custom_species_list` にホワイトリストをセット（birdnetlib API）
- ファイル未指定時は従来通り（lat/lon/date による地域・季節フィルタのみ）

### 5-B. eBird ホワイトリスト自動生成

- `tools/fetch_ebird_species.py` を新規作成
- eBird API（`/v2/product/spplist/{regionCode}` + `/v2/ref/taxonomy/ebird`）から
  指定地域の観察種リストを取得し、`<学名>_<英名>` 形式でファイル出力
- 運用手順:
  1. https://ebird.org/api/keygen で API key 取得
  2. `settings.json` に `ebird_api_key` / `ebird_region_code` (例: `JP-40`) / `species_list_file` を追加
  3. スクリプト実行 → `species_list_file` が生成され、Phase 5-A の機構で自動適用

### 5-C. 人手検証ラベルの蓄積基盤

- DB マイグレーション: `human_label` (`'correct'`/`'wrong'`/NULL) と `human_labeled_at` カラム追加
- `db.set_human_label()` 関数追加
- `web/app.py` に `POST /api/label/{id}` エンドポイント追加
- `index.html` の各検出カードに「✓正解 / ✗誤検知 / 解除」ボタン追加
- `tools/export_labeled_dataset.py`: ラベル付き WAV を ZIP 出力（カスタム分類器学習用）

### 5-D / 5-E（将来計画）

`docs/plans/future_model_improvements.md` に詳細を文書化:
- 5-D: BirdNET embedding + 軽量分類器（scikit-learn LogisticRegression 等）/ BirdNET 転移学習
- 5-E: Perch・YAMNet 等との複数モデルアンサンブル（多数決・加重平均・カスケード）

着手目安: Phase 5-C のラベルデータが各クラス 500件以上貯まってから（おおよそ 2026-11 以降）。

---

## 2026-04-29 — UI 機能拡充とノイズ除去・端末別設定

### Web UI 新ページ

| URL | 内容 |
|---|---|
| `/whitelist` | eBird ホワイトリスト一覧（294種）、フィルタ検索、再取得ボタン |
| `/labeled` | 人手検証ラベルの統計、Phase 5-D 着手目安（500件）の進捗バー、ZIP DL |
| `/reports` | 過去14日分のデイリーレポートを iframe 表示、即時メール送信 |
| `/devices` | 登録 Pi 一覧、ON/OFF トグル |
| `/devices/{pi_id}` | 端末別設定（ノイズ除去モード・lat/lon・録音時間帯） |
| `/docs` | セクション分けで6文書追加（README、計画、archive 含む） |

### ノイズ除去機能

`process.py` の前処理を 4 モード切替に拡張:

| モード | 処理内容 | 1ファイル処理時間（60s WAV、N97） |
|---|---|---|
| `off` | モノラル変換のみ | 0.09s |
| `highpass`（既定） | 500Hz ハイパスフィルタ | 0.08s |
| `spectral` | `noisereduce` で定常ノイズ除去 | 4.02s |
| `highpass+spectral` | 両方適用（最強） | 1.77s |

- 依存追加: `noisereduce==3.0.3`、`soundfile`
- `to_mono()` を後方互換として残し、新規 `preprocess_audio(wav, mode)` を実装

### 端末別設定（`pi_devices`）

`settings.json` に `pi_devices` セクションを追加。Pi 単位で：
- `host` / `latitude` / `longitude`
- `noise_reduction` モード
- `record_all_day` / `record_start_hour` / `record_stop_hour` / `segment_sec`
- `enabled`（無効化時 `process_all` がスキップ）

`process.py` の `get_pi_config(pi_id)` で設定取得。`pi_devices` がない場合は legacy 設定にフォールバック（後方互換）。

複数 Pi 対応の基盤ができたが、現状は `mushipi-bird01` の1台のみ。新規 Pi 追加は `settings.json` の `pi_devices` に追記 → `/devices` ページに自動表示される。

### `update_settings` (`/settings`) は当面残置

グローバル設定（`ingest_interval_min` 等）はこちら、端末別は `/devices` で管理という棲み分け。
将来的に `/settings` から Pi 個別項目を削除する整理は Phase 7（複数 Pi 運用が本格化したタイミング）。
