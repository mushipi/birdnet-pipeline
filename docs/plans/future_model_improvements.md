# BirdProject モデル性能向上 中長期計画

最終更新: 2026-04-29

このドキュメントは Phase 5-D（カスタム分類器）と Phase 5-E（複数モデルアンサンブル）の
将来計画をまとめたもの。Phase 4-A〜5-C までは実装済み。

---

## 全体像（Phase 4〜5 の位置づけ）

| Phase | 内容 | 状態 | 効果 |
|---|---|---|---|
| 4-1 | ブラックリスト拡充 | ✅ 実装済 | 即時、誤検知削減 |
| 4-2 | 過去 confirmed の遡及降格 | ✅ 実装済 | DB クリーニング |
| 4-3 | 和名マスタ修正 | ✅ 実装済 | レポート可読性 |
| 4-A | UI からブラックリスト管理 | ✅ 実装済 | 運用効率向上 |
| 5-A | BirdNET Species List 対応 | ✅ 実装済 | ホワイトリスト基盤 |
| 5-B | eBird ホワイトリスト自動生成 | ✅ 実装済 | 体系的誤検知防止 |
| 5-C | 人手検証ラベル蓄積 | ✅ 実装済 | 学習データ準備 |
| **5-D** | **カスタム分類器（転移学習）** | 📋 計画 | 大効果（要データ蓄積） |
| **5-E** | **複数モデルアンサンブル** | 📋 計画 | 最高効果（要研究） |

---

## Phase 5-D: カスタム分類器（転移学習）

### 目的

BirdNET 単体では除けない誤検知パターン（特定の機械音、虫の声、地域固有の環境音など）を
学習させて、地域・観測点に特化した分類器を作る。

### 前提条件

- **学習データの量**: 最低でも各クラス（正解／誤検知）あたり **100件以上**、できれば 500件以上
- 現状の蓄積：
  - confirmed: 480件（人手検証なしで自動付与されたものも含む）
  - human_label='correct': 0件（Phase 5-C 実装済、これから蓄積）
  - human_label='wrong': 0件
- → **Phase 5-C で蓄積したラベル付きデータが揃ってから着手**（目安：3〜6ヶ月の運用後）

### アプローチ A: BirdNET embedding + 軽量分類器

最もコスパが良いアプローチ。BirdNET を「特徴抽出器」として使い、軽量な後段分類器を学習させる。

#### ステップ

1. **embedding 抽出**
   - `birdnetlib` の `Recording.extract_embeddings()` で各 WAV から 1024次元ベクトルを取得
   - 既存のラベル付きデータ（human_label が付いた WAV）から抽出
2. **二値分類器の学習**
   - 入力: 1024次元 embedding
   - 出力: `correct` / `wrong` の二値
   - モデル候補: `scikit-learn` の `LogisticRegression` / `SVM` / `RandomForest`
   - 学習データを 8:2 で分割、cross-validation で評価
3. **推論パイプラインへの統合**
   - `process.py` で BirdNET 推論後、CONF_HIGH 以上の検出に対して embedding 抽出
   - 後段分類器が `wrong` と判定したら自動で noise に降格
   - もしくは、自動 confirmed の補助スコアとして使う（信頼度の調整）

#### 必要パッケージ

```bash
uv add scikit-learn numpy joblib
```

#### 想定工数

- データセット準備（既存ラベル → embedding 抽出）: 半日
- 分類器学習・評価: 1日
- 推論パイプライン統合: 1日
- 評価・チューニング: 1〜2日
- **合計: 3〜5日**

#### コード雛形（参考）

```python
# tools/train_custom_classifier.py
from birdnetlib import Recording
from birdnetlib.analyzer import Analyzer
import sqlite3, json, joblib
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
import numpy as np

# 1. ラベル付きデータの読み込み
conn = sqlite3.connect(DB_PATH)
rows = conn.execute(
    "SELECT id, file_path, human_label FROM detections "
    "WHERE human_label IS NOT NULL AND scientific_name IS NOT NULL"
).fetchall()

# 2. embedding 抽出
analyzer = Analyzer()
X, y = [], []
for r in rows:
    rec = Recording(analyzer, r["file_path"])
    rec.extract_embeddings()
    # embeddings は (時間軸, 1024) なので mean pooling
    emb = np.mean(rec.embeddings, axis=0) if rec.embeddings else None
    if emb is not None:
        X.append(emb)
        y.append(1 if r["human_label"] == "correct" else 0)

X, y = np.array(X), np.array(y)

# 3. 学習・評価
clf = LogisticRegression(max_iter=1000, class_weight="balanced")
scores = cross_val_score(clf, X, y, cv=5, scoring="f1")
print(f"5-fold F1: {scores.mean():.3f} ± {scores.std():.3f}")

# 4. 全データで再学習・保存
clf.fit(X, y)
joblib.dump(clf, BASE_DIR / "models/custom_classifier.joblib")
```

### アプローチ B: BirdNET-Analyzer Custom Classifier

BirdNET 公式の転移学習機能。`birdnet_analyzer.train` モジュールを使う。

#### 特徴

- BirdNET の最終層を地域固有種にファインチューニング
- 学習データは「ラベル付き音声フォルダ」形式
- 出力は新しい TFLite モデル
- birdnetlib からも使える（`analyzer = Analyzer(classifier_model_path=...)`）

#### 利点・欠点

- **利点**: BirdNET 全体の精度を上げる（特定種の検出率向上）
- **欠点**: アプローチ A より計算リソースを要求、データ量も多く必要（種ごとに 100件以上推奨）

#### 想定工数

- 5〜10日（各種ごとのデータ整備・学習・評価）

### 推奨方針

**アプローチ A から始める**。データが揃ってきて、特定種の精度向上が必要になったら B に進む。

---

## Phase 5-E: 複数モデルアンサンブル

### 目的

異なる学習データ・アーキテクチャのモデルを組み合わせて、単一モデルの弱点を補う。
複数モデルの一致を要件にすることで誤検知を大幅削減できる。

### 候補モデル

| モデル | 提供元 | 特徴 |
|---|---|---|
| **BirdNET** | Cornell Lab | 6,000+ 種、汎用、TFLite | 既に使用 |
| **Perch / Avesound** | Google Research | embedding ベース、高精度 | TF Hub で公開 |
| **EuroPeoplesParlamentJura など地域モデル** | 各研究機関 | 地域特化 | 公開状況に依存 |
| **Stork** | DCASE 系 | 鳥音響イベント検出 | 研究用途 |
| **YAMNet** | Google | 音響イベント全般（鳥含む） | TF Hub |

### アンサンブル戦略

#### 戦略 1: 多数決（Hard Voting）

- N個のモデルが同じ種を返したら confirmed
- 一部のみ一致なら pending
- 全く一致しない場合は noise

#### 戦略 2: 信頼度加重平均（Soft Voting）

- 各モデルの信頼度を重み付き平均
- 重みはモデルの精度（過去のラベルデータで評価）

#### 戦略 3: ステージング（Cascading）

- ステージ1: BirdNET で粗フィルタ（CONF_LOW=0.25 通過）
- ステージ2: Perch で再評価（embedding 類似度）
- ステージ3: ステージ2 で閾値以上のみ confirmed

### 実装上の課題

1. **計算コスト**
   - BirdNET 1ファイル ≈ 数秒（N97 で）
   - Perch は重い（GPU 推奨）
   - 複数モデル並列実行は N97 では厳しい
2. **モデル間のラベル整合**
   - BirdNET は学名+英名、Perch は別の語彙
   - taxonomy マッピングが必要
3. **アーキテクチャの違い**
   - 入力サンプリングレート、チャンク長が違う
   - 前処理を分岐させる必要

### 想定工数

| 項目 | 工数 |
|---|---|
| モデル選定・性能評価 | 3〜5日 |
| 前処理・推論パイプラインの分岐 | 3日 |
| アンサンブル戦略の実装 | 2〜3日 |
| 評価・チューニング | 3〜5日 |
| **合計** | **2〜3週間** |

### 推奨方針

5-D（カスタム分類器）でラベルデータ + embedding 活用に習熟してから着手。
GPU 環境（クラウド or 別マシン）の用意も検討。

---

## ロードマップ（2026年）

```
2026-04 ─ Phase 4 完了、5-A〜5-C 実装完了
   │
   ▼
2026-05〜10 ─ 運用しながらラベルデータ蓄積（Phase 5-C の人手検証）
   │            目標: 各クラス 500件以上
   │
   ▼
2026-11 ─ Phase 5-D アプローチA 着手（BirdNET embedding + 軽量分類器）
   │      実装〜評価で 1週間
   │
   ▼
2027-Q1 ─ Phase 5-D で十分精度が出たら定期更新の自動化
   │      不足あれば アプローチ B（BirdNET 転移学習）に進む
   │
   ▼
2027-Q2 ─ Phase 5-E（アンサンブル）の検討開始
          GPU 環境の確保、モデル選定・PoC
```

---

## 参考リンク

- BirdNET-Analyzer: https://github.com/kahst/BirdNET-Analyzer
- birdnetlib: https://github.com/joeweiss/birdnetlib
- BirdNET embedding 解説: https://birdnet.cornell.edu/
- Perch (Google): https://tfhub.dev/google/bird-vocalization-classifier/4
- BirdNET-Pi: https://github.com/mcguirepr89/BirdNET-Pi
- eBird API: https://documenter.getpostman.com/view/664302/S1ENwy59
