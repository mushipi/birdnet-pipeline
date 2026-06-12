# 全体設計: 2段 野鳥識別パイプライン（BirdNET → カモ10種 Stage2）

最終更新: 2026-06-12 / ステータス: **設計（Stage2は別repoで実装済、統合は未配線）**

このドキュメントは BirdProject（運用本体）と bird-fine-classifier（カモ細分類 Stage2）を横断した
**システム全体設計**。Phase 5-E（`future_model_improvements.md`）の「ステージング(Cascading)」を
カモ類で具現化したもの。具体の Stage2 設計が文書化されていなかったため、ここに集約する。

---

## 1. 目的・背景

- 運用中の BirdNET_GLOBAL_6K_V2.4（汎用6千種 CNN）は、**近縁カモの細分類が不正確**
  （特にカルガモ↔マガモは交雑するほど近縁で**音響的に分離不能**＝Perch本体でも0/30で実証済）。
- 解: **2段化**。BirdNET（汎用検出 Stage1）→ 検出種が「カモ類」なら **専門 Stage2（10種）** で精緻化。
- Stage2 = `bird-fine-classifier`（mushipi-pc で開発、KD蒸留で完成）。

## 2. システム全体構成

```
[Pi mushipi-bird01]  arecord 60秒WAV → rsync(10分, Tailscale)
        ↓
[推論機: 現N97 / 移行先GT105]  ingest_and_process.sh(cron10分) → process.py
     ├ Stage1: sox/ノイズ除去 → birdnetlib(BirdNET_GLOBAL_6K) 推論
     ├ Dispatcher: 検出種の group を species_master で判定
     │     └ group==duck → Stage2 へ（それ以外は従来通り）
     ├ Stage2: カモ10種 AST(KD-soup) で同一3秒窓を再分類 → 種 or 複合に上書き
     └ db.py SQLite(bird_calls.db)
        ↓
[Web UI :8765]  一覧/統計/設定/export
```

## 3. Stage1: BirdNET（現行・実装済）

- モデル: BirdNET_GLOBAL_6K_V2.4（`birdnetlib.Analyzer`）。
- 設定（`process.py`）: `min_conf=CONF_LOW(0.25)`, `sensitivity=1.25`, lat/lon=33.579/130.257（北部九州）。
- 閾値: `CONF_HIGH=0.65` 以上を confirmed として DB 記録。地域フィルタ＋eBirdホワイトリスト。
- 仕分け: detected / review / unknown。
- **役割（2段化後）**: 「カモ類が居る」までの**検出・トリガ**に徹する（種同定は Stage2 に委譲）。

## 4. Dispatcher / ルーティング

- **`species_taxonomy.yaml`**（bird-fine-classifier）: グループ別の推論設定の単一の真実。
  ```yaml
  duck:
    pipeline: { stage2_model, energy_threshold, energy_temperature }
    display_groups: { Mallard: {label: "マガモ/カルガモ", ...} }
  ```
- **`species_master.csv`**: 種 → group / status（target / ood_tier*）。
- ルーティング規約: BirdNET 検出種の group が `duck`（status=target/ood）なら、その音声窓を Stage2 へ。

## 5. Stage2: カモ10種分類器（別repo・実装済）

- **運用モデル**: `models/ast-duck-C-kd-soup`（Perch→KD蒸留 3seed soup）。test 録音単位 macro-f1 **0.871**。
- **対象10種**: マガモ/コガモ/オナガガモ/ハシビロガモ/ヒドリガモ/オカヨシガモ/キンクロハジロ/ホシハジロ/ホオジロガモ/ウミアイサ。
- **3秒固定チャンク**: BirdNET の3s窓に整合（運用制約）。
- **OOD energy ゲート**: `predict.py` が録音平均 energy で判定。閾値 **2.717**（録音単位再キャリブレ, 真カモ保持0.90）。
  非カモ（BirdNET誤検出）を棄却。対象外カモ類の漏れは複合/分類側で受容。
- **複合クラス出力（適応的解像度）**: 音響的に割れないペアは複合(slash)で出す。
  - 種（既定）: 分離できる8種。
  - 複合: **マガモ/カルガモ**（カルガモはモデル上Mallardに化ける＝relabelで誠実、最頻種を捨てない）。
  - カモ科 sp.（種不明, 低信頼後退・**未実装**）/ 非カモ（棄却）。

## 6. 蒸留と CPU デプロイ（移行の鍵）

- Phase 5-E は「Perchは重くGPU推奨、N97/CPUでは複数モデル並列は厳しい」を**将来ブロッカー**としていた。
- **本設計はこれを蒸留で解消**: Perch（教師, GPU重）→ AST（生徒）へ知識蒸留＋soup。
  **推論時に Perch は不要＝蒸留済み AST 単体で動く**。AST は CPU 推論可（3秒チャンクは軽量）。
- → **N97 / GT105（どちらもCPU）に Stage2 をデプロイ可能**。蒸留は精度向上だけでなく
  「運用機にデプロイ可能にする」価値を持つ。

## 7. 統合（未実装＝本設計の作業）

1. **`process.py` にルーティングフック**: BirdNET 推論後、group==duck の検出に対し該当窓を Stage2 推論。
   - **メモリ・ハンドオフ前提**: Stage1 が**デコード済みの波形(numpy)＋検出の時間オフセット**を保持し、
     Stage2 FE へ**メモリ上でスライス渡し**（ファイル再読込を回避）。CPUベンチの前処理611ms/chunkの
     大半は `librosa.load`（ファイル読込＋48k→16kリサンプル）。再読込を消せば前処理は **~150-250ms** に。
     可能なら **BirdNET が叩いた3秒窓をそのまま再利用**（窓の整合）。残コスト＝リサンプル＋mel（不可避）。
2. **Stage2 のデプロイ**: モデル(`ast-duck-C-kd-soup`)＋`predict.py`＋`species_taxonomy.yaml`を推論機へ。
   CPU torch/transformers 環境（GT105 `.venv-cpu` 検証済）。
3. **DBスキーマ（非破壊が必須）**:
   - **Stage1 の元カラム（species/scientific_name/confidence）は絶対不変**。Stage2 結果は**別カラム追加**:
     `refined_species` / `refined_label`(表示=マガモ/カルガモ) / `refined_by` / `refined_confidence` /
     `refined_energy` / **`stage2_model_version`**（モデル更新の追跡）/ `refined_at`。
   - **原本音声(WAV)を保持**: カモ検出セグメントは削除しない（仕分けで消えない運用）。
     理由＝(a) BirdNET自身の混同実証に生の鳴き声が要る (b) モデル改善時の Stage2 再実行
     (c) 人手ラベル監査 (d) ロールバック。

## 8. 移行: N97 → GT105

- 移行先 GT105 は CPU 運用ハブ（Tailscale）。Stage2 が CPU で動くため移行と整合。
- **前提検証（2026-06-12 実測・合格）**: GT105(CPU 16core/30G) で AST KD-soup 推論を計測:
  モデルロード0.8s/RSS452MB、**推論 109ms/chunk(16thread, 8.6ch/s)**、前処理(FE+load)611ms/chunk、ピークRSS1.4GB。
  energyゲート＋複合クラス込みの完全推論を CPU で再現（マガモ→「マガモ/カルガモ」, 非カモ→棄却）。
  運用cadence(10分バッチ・カモは検出の一部=数chunk)に対し**桁違いの余裕**。→ **移行・デプロイは演算面で問題なし**。
  ベンチ: `bird-fine-classifier/tools/bench_cpu_inference.py`（CPU venv `.venv-cpu`）。
- 補足: 前処理(ASTFeatureExtractor)が推論の5倍＝律速。統合時はBirdNETの音声ロードと共有して削れる余地。
- 既存の移管テンプレ: `~/MIGRATION_minipc_to_gt105.md` / `~/.claude/docs/project-ops.md`。

## 9. 評価・運用規律

- **録音単位 macro-f1 ＋ 録音クラスタ bootstrap CI**（`analysis/compare_runs_ci.py`）。
  chunk単位点推定で優劣を断定しない。CI が重なる差(≈±0.05)は「差なし」。
- 弱種の評価解像度（ヒドリ等の小標本）は **test拡大で対応中**（B級worldwide収集→再split→再学習, 進行中）。
- **OOD閾値のフィールド再キャリブレ（重要・デプロイ後必須）**: 現 2.717 は **Xeno-canto域の暫定値**。
  実フィールド（パラボラマイク・固定地点・水辺/風/他鳥/機械音）は energy 分布が異なり、誤りの向き
  （ノイズ→自信↓→真カモ過剰棄却 / 背景音→誤受理）は**現地で測らないと不明**。
  → デプロイ後、**Pi実録音の energy ＋ 人手ラベル(Phase 5-C の correct/wrong)** で `ood_fp_audit.py` の
  録音単位手法を**フィールドデータに当てて再導出**。季節（冬鳥飛来期・水位/風）で変動しうるため
  **固定値でなく監視・調整する tunable** として扱う。Xeno-canto→フィールドのドメインギャップの一断面。

## 10. 未実装・残課題

- [x] **GT105 CPU 推論テスト**（2026-06-12 合格: 109ms/chunk・energyゲート/複合クラス CPU 再現・cadence余裕）
- [ ] **process.py ルーティングフック**＋Stage2 デプロイ ← **次の本命**（演算面の前提クリア済）
  - メモリ・ハンドオフ（波形渡しで前処理短縮）/ DB非破壊（refined_*列＋原本WAV保持）を設計に織込（§7）
- [ ] test拡大の再学習(Cv2)評価・昇格判定（進行中）
- [ ] BirdNET 自身のカルガモ/マガモ混同の実証（複合化の運用適用根拠／原本WAV保持が前提）
- [ ] **OOD閾値のフィールド再キャリブレ**（デプロイ後・人手ラベル蓄積後／季節変動の監視）
- [ ] data/ood_processed 再生成（陳腐化）
- [ ] 「カモ科 sp.」低信頼後退（信頼閾値キャリブレ要）

## 付録: 関連資料

- Stage2 全経緯: `bird-fine-classifier/docs/perch_kd_report.md`
- 旧改善計画: `BirdProject/docs/plans/future_model_improvements.md`（Phase 5-D/5-E）
- 運用設計: `BirdProject/DESIGN.md`（現行単段）
