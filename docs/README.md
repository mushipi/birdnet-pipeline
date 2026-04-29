# BirdProject ドキュメント目次

このディレクトリは BirdProject の補助ドキュメント置き場。
プロジェクトの根幹資料は `BirdProject/` 直下にある。

---

## 構成

```
BirdProject/
├── README.md           プロジェクト全体のエントリポイント
├── DESIGN.md           システム設計書（ハードウェア・API・DB スキーマ）
├── DEVLOG.md           開発記録・技術的決定事項の時系列ログ
├── docs/
│   ├── README.md       (このファイル)
│   ├── hardware/       ハードウェア設計資料
│   │   └── parabolic_mic.md   自作パラボリックマイク設計（3D 印刷 / Blender）
│   └── plans/          中長期計画書
│       └── future_model_improvements.md   モデル性能向上ロードマップ（Phase 5-D / 5-E）
└── archive/            過去の調査・検討資料（参照のみ、現役運用ではない）
    ├── inference_review_2026-03-20.md   推論精度レビュー（2026-03 提案、Phase 4-A までで実装済み）
    └── vm_q1_report.md                  SAIREN VM-Q1 マイク検証（2026-03 採用見送り）
```

---

## ドキュメントの読み方

### はじめての方

1. `BirdProject/README.md` — システム概要・セットアップ手順
2. `BirdProject/DESIGN.md` — 全体アーキテクチャ
3. `BirdProject/DEVLOG.md` — 経緯と決定事項

### 何かを変更・追加する前に

- 該当機能の最新仕様は `DESIGN.md` を確認
- 過去の経緯・決定事項は `DEVLOG.md` を時系列で確認
- ハードウェアを変更する場合は `docs/hardware/` を参照
- モデル精度を上げたい場合は `docs/plans/future_model_improvements.md` を参照

### ドキュメント追加ルール

| 種類 | 配置先 |
|---|---|
| システム全体に影響する設計変更 | `DESIGN.md` を更新 |
| 個別の作業・意思決定の記録 | `DEVLOG.md` に時系列で追記 |
| ハードウェア仕様・設計図 | `docs/hardware/` に新規作成 |
| 将来の実装計画・ロードマップ | `docs/plans/` に新規作成 |
| 採用見送りや過去の検討資料 | `archive/` に移動 |

---

## Web UI からの閲覧

`/docs` で `DESIGN.md`・`DEVLOG.md`・`docs/hardware/parabolic_mic.md` がブラウザから閲覧できる。
追加ドキュメントを Web UI に出したい場合は `web/app.py` の `/docs` ルートに登録する。
