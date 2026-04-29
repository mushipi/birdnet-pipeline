# BirdProject 推論ロジック精度レビュー

## 現状のデータ概況

| ステータス | 件数 | 割合 |
|-----------|------|------|
| confirmed | 56 | 2.0% |
| pending | 467 | 17.1% |
| noise | 2,212 | 80.9% |
| **合計** | **2,735** | |

確信度の分布（confirmed + pending のみ）:

| 帯域 | 件数 |
|------|------|
| 0.90+ | 24 |
| 0.80-0.89 | 16 |
| 0.70-0.79 | 16 |
| 0.50-0.69 | 34 |
| 0.30-0.49 | 74 |
| 0.10-0.29 | 359 |

> [!IMPORTANT]
> noise が **80%** を占めている。これは「検知漏れ」か「そもそも鳥が鳴いていない時間帯のファイルが大量にある」かのどちらか。後者であれば正常だけど、前者なら改善余地が大きいわ。

---

## 改善ポイント一覧

### 1. `overlap` パラメーターの最適化

**現状:** `overlap=1.5`（[process.py:80](file:///home/mushipi/BirdProject/process.py#L80)）

BirdNET は 3 秒のウィンドウで解析するの。`overlap=1.5` だと 1.5 秒刻みでスライドするから、**そこそこ良い設定**よ。ただし、短い鳴き声（ヒヨドリの「ピー」など）がウィンドウの境界にかかりやすい場合は、**`overlap=2.0`** に上げると拾いやすくなるわ。

```diff
 recording = Recording(
     analyzer,
     str(mono_path),
     date=ts,
     lat=lat,
     lon=lon,
     min_conf=CONF_LOW,
-    overlap=1.5,
+    overlap=2.0,
 )
```

> [!TIP]
> `overlap` を上げると処理時間が増えるけど、60秒ファイルなら N97 の CPU でも問題ないレベルよ。

---

### 2. `sensitivity` パラメーターの追加

**現状:** 未指定（デフォルト = `1.0`）

BirdNET には `sensitivity` パラメーターがあって、0.5〜1.5 の範囲で「検出のしやすさ」を調整できるの。現状はデフォルトの 1.0 だけど、**`1.25`** くらいに上げると、遠くで鳴いている鳥や音量の小さい鳴き声もキャッチしやすくなるわ。

```diff
 recording = Recording(
     analyzer,
     str(mono_path),
     date=ts,
     lat=lat,
     lon=lon,
     min_conf=CONF_LOW,
     overlap=2.0,
+    sensitivity=1.25,
 )
```

> [!WARNING]
> sensitivity を上げすぎると false positive（誤検知）が増える。上げた場合は `CONF_HIGH`（確定閾値）を 0.7 のまま維持して、pending をレビューする運用がベストよ。

---

### 3. サンプルレート不一致の修正

**現状:**
- Pi 側録音: **48,000 Hz**（[record.sh:9](file:///home/mushipi/BirdProject/pi-scripts/record.sh#L9) の `-r 48000`）
- BirdNET モデル: **48,000 Hz** を期待

これは合ってるわね。OK。

---

### 4. `CONF_HIGH` / `CONF_LOW` 閾値の見直し

**現状:**
- `CONF_HIGH = 0.7`（confirmed の閾値）
- `CONF_LOW = 0.1`（birdnetlib の min_conf）

```
confirmed: 56件 (0.7以上)
pending:  467件 (0.1〜0.7)
```

pending が多すぎて使い物にならない可能性があるわ。**2段階の見直し**を提案するわね：

| パラメーター | 現在 | 提案 | 理由 |
|-------------|------|------|------|
| `CONF_LOW` | 0.1 | **0.25** | 0.1〜0.25 帯域の 359 件はほぼノイズ。DBに入れるだけ無駄 |
| `CONF_HIGH` | 0.7 | **0.65** | 0.65〜0.70 の検知もかなり信頼できる。confirmed を増やせる |

```diff
-CONF_HIGH = 0.7   # confirmed として記録する確信度下限
-CONF_LOW = 0.1    # birdnetlib の min_conf に渡す値
+CONF_HIGH = 0.65  # confirmed として記録する確信度下限
+CONF_LOW = 0.25   # birdnetlib の min_conf に渡す値（これ未満は無視）
```

---

### 5. 種ごとの集約ロジックの改善

**現状:** [aggregate_by_species](file:///home/mushipi/BirdProject/process.py#L92-L104) は種ごとに「最高確信度のエントリ 1 件」だけを保存しているわ。

**問題:** 1 ファイル（60 秒）の中で同じ鳥が複数回鳴いた場合、最も確信度が高い 1 回分しか記録されない。**検出回数（count）** の情報が失われている。

**提案:** 検出回数を DB に保存するカラムを追加し、同じファイル内で何回検知されたかを記録する。

```diff
 def aggregate_by_species(detections: list, min_conf: float) -> dict[str, dict]:
     best: dict[str, dict] = {}
+    count: dict[str, int] = {}
     for d in detections:
         if d["confidence"] < min_conf:
             continue
         name = d["common_name"]
+        count[name] = count.get(name, 0) + 1
         if name not in best or d["confidence"] > best[name]["confidence"]:
             best[name] = d
+    for name in best:
+        best[name]["det_count"] = count[name]
     return best
```

> [!NOTE]
> これにより「1 ファイル中に 8 回鳴いた」という情報が残るので、活動量の推定精度が上がるわ。

---

### 6. 日本固有種リストによるフィルタリング（オプション）

BirdNET は世界 6,000 種に対応しているけど、日本（特にこの緯度経度付近）では明らかに生息しない種が検出されることがあるわ。

**提案:** `lat` / `lon` パラメーターは既に渡しているので BirdNET 側でフィルタはかかっているはず。ただし、明らかに誤検知が多い種（例: Ruddy Shelduck は福岡では珍しい）をブラックリストで除外するオプションがあると便利よ。

> [!TIP]
> これは将来的な改善でいいわ。まずは 1〜5 を先にやるのがコスパ良いと思う。

---

## 優先度まとめ

| 優先 | 項目 | 影響度 | 工数 |
|------|------|--------|------|
| **高** | 閾値の見直し（CONF_HIGH/LOW） | DB の無駄データ削減 + confirmed 増加 | 小 |
| **高** | sensitivity 追加 | 検出漏れ削減 | 小 |
| **中** | overlap 微調整 | 境界鳴き声の拾い漏れ改善 | 小 |
| **中** | 検出回数の保存 | 活動量データの質向上 | 中 |
| **低** | 種のブラックリスト | 明らかな誤検知排除 | 小 |

---

## 今後の課題（中長期）

### A. 録音セグメント長の検討

**現状:** 60 秒固定（[record.sh](file:///home/mushipi/BirdProject/pi-scripts/record.sh)）

| セグメント長 | メリット | デメリット |
|-------------|---------|-----------|
| 15〜30秒 | 推論が速い、ファイル小さい | 長い鳴き声が途切れる可能性 |
| 60秒（現状） | バランス良い | N97 CPU で問題なし |
| 120〜180秒 | 文脈が増えて精度向上の可能性 | ファイルサイズ増大、メモリ負荷 |

> [!NOTE]
> BirdNET は 3 秒ウィンドウで解析するから、セグメント長自体は精度に直接影響しないわ。ただしファイルが短すぎると「鳴き始め」が切れるリスクがあるし、長すぎるとストレージと転送時間がネックになる。**現状の 60 秒は妥当**だけど、将来的に 30 秒に短縮してリアルタイム性を上げる選択肢もあるわね。

---

### B. BirdNET モデルの選択とアップデート

**現状:** `BirdNET_GLOBAL_6K_V2.4`（TFLite, birdnetlib 経由）

- **V2.4** は 2024 年時点の最新安定版で、6,000+ 種に対応
- birdnetlib のバージョンアップで新モデルが利用可能になることがある
- **カスタムモデル（ファインチューニング）** も BirdNET-Analyzer は対応しているけど、birdnetlib 経由だと制約がある

**今後の選択肢:**

| 方針 | 内容 | 難易度 |
|------|------|--------|
| birdnetlib アップデート追従 | 新バージョンが出たら `uv add birdnetlib@latest` | 低 |
| BirdNET-Analyzer 直接利用 | birdnetlib を捨てて Python API を直接叩く | 中 |
| カスタム分類器の学習 | 自分の録音データで再学習して地域特化 | 高 |

> [!TIP]
> まずは birdnetlib のアップデート追従で十分。データが数万件たまったら、カスタム分類器も視野に入れると面白いわね。

---

### C. 環境音の前処理（ノイズ除去）

**現状:** `sox` でモノラル変換のみ。ノイズ除去は行っていない。

環境音（風、雨、車、虫）が多い録音では BirdNET の精度が大幅に落ちるわ。以下の前処理を段階的に導入する価値があるわね：

| 手法 | 効果 | 実装コスト |
|------|------|-----------|
| **ハイパスフィルター（500Hz〜）** | 低周波ノイズ（風、車）を除去 | `sox` 一行追加で簡単 |
| **ノイズプロファイル減算** | 定常ノイズの除去 | `sox noisered` で可能。ノイズサンプル必要 |
| **スペクトラルゲーティング** | Python (`noisereduce` ライブラリ) で高度なノイズ除去 | 中程度。ライブラリ追加が必要 |
| **VAD（音声活動検出）** | 無音区間をスキップして推論時間を短縮 | 中程度 |

```bash
# ハイパスフィルターの例（sox で簡単に追加可能）
sox input.wav output.wav highpass 500
```

> [!IMPORTANT]
> **ハイパスフィルターは即効性が高い**。鳥の鳴き声は大半が 1kHz 以上なので、500Hz 以下をカットするだけでもかなり改善する可能性があるわ。`to_mono()` に 1 行足すだけで実装できるわよ。

---

### D. ストレージ戦略

**現状:** 全 WAV ファイルを `processed/` に永続保存

| 項目 | 現状 |
|------|------|
| Pi 側ディスク使用量 | 7% |
| N97 側 processed/ | 1,360 ファイル |

- **noise ファイルの自動削除:** 30 日以上前の noise ファイルは自動で消す cron を検討
- **WAV → FLAC 圧縮:** confirmed/pending だけ FLAC に変換してサイズ半減
- **クラウドバックアップ:** confirmed だけ Google Drive や S3 に退避する運用も将来的にあり

---

### E. マルチ Pi 運用への備え

**現状:** Pi 1 台（`mushipi-bird01`）のみ

将来的に複数台の Pi を設置する場合に備えて：
- `pi_id` は既にレコードに紐付いているので **DB 側は対応済み**
- `ingest_and_process.sh` が `mushipi-bird01` をハードコードしているので、動的に対応する必要あり
- `settings.json` を Pi 複数台に対応する構造に拡張する設計が必要

---

## 次のアクション

上記のどれから着手するか教えてね。全部一気にやっても OK よ（工数的にはそれほど大きくないわ）。
今後の課題（A〜E）は中長期のロードマップとして頭に入れておいて、まずは改善ポイント 1〜5 を優先するのがおすすめよ。
