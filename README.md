# BirdProject

Raspberry Pi で野鳥の鳴き声を継続録音し、推論機（現 N97 / 移行先 GT105）で **2段パイプライン**
（BirdNET 汎用検出 → 群専用 Stage2 細分類）により自動種判定を行い、Web UI で観察記録として管理するシステム。

## システム概要

```
[Raspberry Pi]  録音（arecord, 60秒セグメント）
      ↓ rsync（Tailscale VPN, 10分ごと）
[推論機: 現N97 → 移行先GT105]  process.py（cron 10分）
   ├ Stage1: BirdNET_GLOBAL_6K_V2.4 で汎用検出
   ├ Dispatcher: 検出種の group を判定（species_master）
   │     └ group∈{duck, crow, …}（taxonomy 登録済）なら Stage2 へ
   ├ Stage2: 群専用 AST（多seed soup）で同一3秒窓を再分類 → 種/複合に上書き＋OOD棄却
   └ SQLite DB（Stage1列は不変・refined_* 追加・原本WAV保持）
      ↓
[Web UI]        http://<推論機_TAILSCALE_IP>:8765
```

- **2段の全体設計** → [`docs/plans/two_stage_pipeline_design.md`](docs/plans/two_stage_pipeline_design.md)。
  Stage2 ルーティングフックは実装済（`stage2_refine.py`, 群汎用 dispatcher, 既定 `settings.stage2.enabled=false`）。
  Stage2 細分類器の開発は別repo [`bird-fine-classifier`](../bird-fine-classifier)（duck 運用中・crow 構築中）。

## ネットワーク構成

| ホスト | Tailscale IP | 役割 |
|--------|-------------|------|
| mushipiubuntuserver (N97) | <N97_TAILSCALE_IP> | 推論・DB・Web（現運用） |
| mushipi-GT105 | <GT105_TAILSCALE_IP> | 推論・DB・Web（移行先, CPU運用ハブ。Stage2 CPU推論検証済） |
| mushipi-bird01 (Pi) | <PI_TAILSCALE_IP> | 録音・転送 |

## セットアップ

### N97 側

```bash
# 依存パッケージのインストール
cd /home/mushipi/BirdProject
uv sync

# systemd user services の有効化
systemctl --user enable --now birdnet-ingest.timer
systemctl --user enable --now birdnet-web.service

# Web UI の手動起動（サービス未使用の場合）
cd web && uv run uvicorn app:app --host 0.0.0.0 --port 8765
```

### Pi 側（新規セットアップ）

```bash
# N97 から Pi へスクリプト転送
scp pi-scripts/*.{sh,service} mushipi@<PI_IP>:~/BirdProject/
ssh mushipi@<PI_IP> "bash ~/BirdProject/install_pi.sh"
```

## 主要コマンド

```bash
# 手動で推論処理を実行
cd /home/mushipi/BirdProject
uv run python process.py --pi-id mushipi-bird01

# DB の状況確認
sqlite3 db/bird_calls.db "SELECT status, COUNT(*) FROM detections GROUP BY status;"

# ログ確認
tail -f ingest.log
```

## 設定ファイル

`settings.json` — Web UI の設定画面から変更可能。保存時に Pi の録音設定も SSH 経由で反映される。

| キー | 説明 |
|------|------|
| `pi_host` | Pi の SSH 接続先 |
| `raw_ingest_dir` | Pi からの WAV 受け皿 |
| `processed_dir` | 推論済みファイルの保存先 |
| `db_path` | SQLite DB のパス |
| `ingest_interval_min` | 推論処理の間隔（分） |

## 重要な制約

- **`numpy<2` 固定必須**: `tflite-runtime` が NumPy 1.x ビルドのため。外すと `SystemError` でクラッシュする
- noise WAV（`processed/*/unknown/`）は推論処理後に自動削除される（DB には記録が残る）

## 機密ファイルのセットアップ

`mail_config.json` は `.gitignore` で除外されている。初回セットアップ時に作成:

```bash
cp mail_config.json.sample mail_config.json
# Gmail のアプリパスワード等を編集
chmod 600 mail_config.json
```

## ドキュメント

| ファイル | 内容 |
|---------|------|
| `DESIGN.md` | システム全体設計（ハードウェア・API・DB スキーマ, 現行単段の運用基盤） |
| `docs/plans/two_stage_pipeline_design.md` | **2段パイプライン全体設計**（BirdNET→群専用Stage2, dispatcher/統合/移行） |
| `DEVLOG.md` | 開発記録・技術的決定事項（時系列） |
| `docs/README.md` | ドキュメント目次・追加ルール |
| `docs/hardware/parabolic_mic.md` | 自作パラボリックマイク設計書 |
| `docs/plans/future_model_improvements.md` | モデル性能向上 中長期計画（Phase 5-D / 5-E） |
| `archive/README.md` | 過去資料の索引（採用見送り・実装完了済み）|
