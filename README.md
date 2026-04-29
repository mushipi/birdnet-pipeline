# BirdProject

Raspberry Pi で野鳥の鳴き声を継続録音し、N97 サーバー上で BirdNET により自動種判定を行い、Web UI で観察記録として管理するシステム。

## システム概要

```
[Raspberry Pi]  録音（arecord, 60秒セグメント）
      ↓ rsync（Tailscale VPN, 10分ごと）
[N97 サーバー]  推論（BirdNET_GLOBAL_6K_V2.4）→ SQLite DB
      ↓
[Web UI]        http://100.102.9.77:8765
```

## ネットワーク構成

| ホスト | Tailscale IP | 役割 |
|--------|-------------|------|
| mushipiubuntuserver (N97) | 100.102.9.77 | 推論・DB・Web |
| mushipi-bird01 (Pi) | 100.78.71.38 | 録音・転送 |

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
| `DESIGN.md` | システム全体設計（ハードウェア・API・DB スキーマ） |
| `DEVLOG.md` | 開発記録・技術的決定事項（時系列） |
| `docs/README.md` | ドキュメント目次・追加ルール |
| `docs/hardware/parabolic_mic.md` | 自作パラボリックマイク設計書 |
| `docs/plans/future_model_improvements.md` | モデル性能向上 中長期計画（Phase 5-D / 5-E） |
| `archive/README.md` | 過去資料の索引（採用見送り・実装完了済み）|
