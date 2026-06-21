# ADR(Draft): 種の同一性キーを iNat taxon_id に一本化する

- ステータス: **Draft / Proposed**（2026-06-21 起草、たたき台）
- 関連: `docs/plans/two_stage_pipeline_design.md` §10（契約ファイル所有権の未決）, `docs/retrain_candidates.md`, species_master.csv
- 起草の引き金: Stage1 v2 再学習（165→200クラス）で、種の同定キーがバラついて何度も手作業の照合事故が起きた。

## Context（なぜ）

「1種」を指すキーがパイプの層ごとに違い、しかもどのキーも別々の理由で不安定。

| 層 | 現在のキー |
|---|---|
| XC収集 | 学名(sci)。ただしXC側の属が違う例あり（ズグロカモメ＝eBird `Saundersilarus saundersi` / XCは `Chroicocephalus saundersi`） |
| data/raw・02_prepare・学習クラス | **フォルダ名＝英名** |
| models_Labels.txt | Sci_Common（学名_英名） |
| dispatch(stage2_refine) | 学名(sci)（commit 89c99cf で sci 駆動化） |
| 季節表 seasonal_occurrence | 学名(sci) |
| species_master.csv | taxon_id / sci / **en_inat / en_birdnet（英名2系統）** / ja / family / order |
| 表示 | 和名(ja) |

### 各キーの不安定要因

- **学名(sci)**: 分類改名で動く。v2作業だけで コチドリ Charadrius→**Thinornis**、アマサギ ibis→**coromandus**、ズグロカモメ属が3表記。「sci正規化」しても改名で過去ラベルと突合がズレる。
- **英名(en)**: 表記揺れ＋ iNat英名 ≠ BirdNET英名（だから species_master に列が2本）。**フォルダ名＝英名**＝最も揺れる鍵が学習クラスの主キーになっている。
- **和名(ja)**: 分離説（オオコノハズク/コノハズク）、人手入力でsciとズレる（v2でユーザー提示リストに和名↔学名ズレ15件）。

### 実害（v2で実際に起きた）

- コチドリを「欠落」と誤判定（旧属名 Charadrius で突合、実際は `Thinornis dubius` で165入り済み）。
- ユーザー提示の追加リストで和名↔学名/英名のズレ15件。手作業の目視照合で吸収＝再現性なし・見落としリスク。
- イカル/シマアオジ等の XC 照会で属違い・別名のハンドリングが場当たり（`xc_sci=` でその場対処）。

## Decision（決め）

### 1. 不変主キー = **iNaturalist taxon_id**

- 既に species_master が `taxon_id` 列を保持（200/225 充足・重複ゼロ、`scripts/utils/sync_species_inat.py` が iNat API で採番）。
- iNatは改名時もIDを保持し、分割時は新IDを採番＝**変更が明示的・稀**。sciの「黙って属が変わる」より御しやすい。
- 完全不変ではない（分割で新ID・稀に統合でID退役）が、**起きたら検出可能**にするのが眼目（下のlint）。

### 2. species_master = 唯一の正本クロスウォーク

- `taxon_id` を主キー化（unique制約・非null）。`sci` / `en_birdnet` / `ja` / `family` 等は taxon_id から引く**表示属性**に降格。
- 列を追加:
  - `xc_sci`: XCが別属で持つ種の照会別名（行は taxon_id でキー）。
  - `model_label`: models_Labels.txt の正確な "Sci_Common" 文字列（凍結ラベル↔taxon_id の橋）。

### 3. モデルラベルは "Sci_Common" 据え置き（凍結扱い）

- BirdNET枠組みの制約でラベル自体は Sci_Common。ただしモデルは学習時点で**凍結**＝勝手に変わらない。
- species_master の `model_label` 列で **凍結ラベル↔taxon_id** を対応。突合は常に taxon_id 経由。

### 4. 各層を taxon_id 解決に寄せる

- 季節表 `build_seasonal_occurrence.py` と dispatch（stage2_refine）を sci→**taxon_id** キーに（どちらも再生成・再設定するので**次の再生成時に一緒に**やれば追加コスト小）。
- data/raw フォルダは英名のまま（人間可読）でよいが、**フォルダ↔taxon_id の対応をspecies_masterで保証**（フォルダ名は表示、突合は別）。

### 5. 検証 lint（retrain/配備前ゲート）

- data/rawフォルダ / models_Labels / 季節表 / dispatch の各エントリが **species_master の taxon_id 1行に必ず解決する**かを検査。1つでも解決しなければ fail。
- これがあれば コチドリ誤判定・15件のズレ・XC属違いを**機械的に検出**できた。
- [[custom-model-baseline-guard]] のゲートと同じ場所（retrain/配備前）に置く。

## Consequences

**Pros**: 改名に強い／突合が機械化（手目視を廃止）／新種intakeを「和名→iNatで自動補完」にして自由入力起因のズレを断てる／2系統の英名・XC属違いを契約で吸収。

**Cons・留保**:
- iNat taxon_id も永続保証ではない（分割で新ID・統合でID退役）。→ lint が「解決しないtaxon_id」を弾くので**変更が可視化される**ことで許容。
- 配線が広い（季節表・dispatch・lint・intake）。一度に全部やらず段階移行。
- 契約ファイル(species_master)の所有権は研究repo(bird-fine-classifier)に在り、運用(BirdProject)が食う構図（two_stage §10 の未決）。taxon_id化はこの所有権整理と一緒に考えると綺麗。

## 移行プラン（bounded・段階）

1. **欠けてる25件の taxon_id 補完**（v2新規35種含む）: `sync_species_inat.py` か iNat API 種別ルックアップ。
2. species_master に `xc_sci` / `model_label` 列追加 + taxon_id unique/non-null 制約（検査スクリプト）。
3. **lint を1本**実装（上記4層→taxon_id解決チェック）。まずは警告モードで現状の不整合を洗い出し。
4. 季節表・dispatch を taxon_id キーに（**次回再生成・再設定のタイミングで**）。
5. 新種 intake フローを「和名→iNat自動補完」に置換（自由入力廃止）。

## Open Questions

- authority を iNat 単独でいくか、eBird/Clements との二重持ち（model_labelはeBird系taxonomy由来）にするか。→ 当面 iNat主キー＋model_labelで橋渡しで足りる見込み。
- フォルダ名を英名のまま残すか taxon_id にするか（可読性 vs 一意性）。→ 英名据え置き＋マニフェストで保証が現実的。
- 契約ファイル所有権（two_stage §10）との統合順序。

---
*このドラフトは v2 学習完了後に正式 ADR 化する想定。番号採番・docs/adr/ への移動は確定時に。*
