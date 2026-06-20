# Stage1 カスタムCNN 再学習候補リスト

カスタム CNN(`models/models.tflite`, 165クラス, 九州北部 applicable モデル) に**欠落**している種のうち、
北部九州(福岡市西区北原)で**実在しうる**ものを次回再学習で追加する候補。

経緯: 2026-06-20、素 6K → カスタム CNN へ Stage1 切替。素 6K が検出していた種を本番DBで突合し、
165 クラスに無い種を抽出 → 明確な FP(山地種/外洋海鳥/冬鳥)を除き、妥当なものを候補化。
**165 クラスは「species_master 225種 のうち XC 録音 ≥30 件あった 162-165 種」で構成**されており、
ここに無い = ①species_master 未収載 か ②録音 30 件未満で学習除外、のどちらか。
→ 追加には **species_master へ追記 + XC 等で録音 ≥30 件確保 → 再学習**(朝の applicable モデル手順)が必要。

各候補は★要確認(現地・公的データ)。tier は暫定。

## Tier 1: 最優先（実在確定 / 機能上の欠落）
| 和名 | 学名 | en | 根拠 | 備考 |
|---|---|---|---|---|
| ホトトギス | Cuculus poliocephalus | Lesser Cuckoo | **現地で夜間に鳴いているのをユーザー確認**。6K では拾えた(0.99)がCNN欠落 | 夏鳥。最優先で追加 |
| スズガモ | Aythya marila | Greater Scaup | **Stage2 duck dispatch 対象なのにCNN欠落**=Stage1が出せずrefine不能 | 冬カモ。dispatch完全性 |
| オオセグロカモメ | Larus schistisagus | Slaty-backed Gull | **Stage2 gull dispatch 対象なのにCNN欠落** | 冬カモメ。dispatch完全性 |
| ズグロカモメ | Saundersilarus saundersi | Saunders's Gull | **Stage2 gull dispatch 対象なのにCNN欠落** | 冬カモメ(有明海)。dispatch完全性 |

## Tier 2: 妥当な夏鳥・留鳥（6K検出あり・季節地域的に合う）
| 和名 | 学名 | en | 6K検出 | 備考 |
|---|---|---|---|---|
| カッコウ | Cuculus canorus | Common Cuckoo | 9 (0.81) | 夏鳥・鳴く |
| ツツドリ | Cuculus optatus | Oriental Cuckoo | 6 (0.89) | 夏鳥・鳴く |
| コチドリ | Charadrius dubius | Little Ringed Plover | 16 (0.98) | 夏鳥・水辺繁殖 |
| アオバト | Treron sieboldii | White-bellied Green-Pigeon | 9 (0.63) | 留鳥(本土種。Ryukyu系FPの正しい対応種) |
| オオコノハズク/コノハズク | Otus sunia | Oriental Scops-Owl | 1 (0.80) | 夏鳥(Ryukyu系FPの正しい対応種)・夜鳴き |
| アマサギ | Bubulcus ibis | Cattle Egret | 2 (0.34) | 夏鳥 |
| コアジサシ | Sternula albifrons | Little Tern | 1 (0.78) | 夏鳥・海岸繁殖 |
| アカゲラ | Dendrocopos major | Great Spotted Woodpecker | 4 (0.62) | 留鳥(林) |

## Tier 3: 要精査（passage/分布拡大中/不確実）
| 和名 | 学名 | en | 備考 |
|---|---|---|---|
| キョウジョシギ | Arenaria interpres | Ruddy Turnstone | 旅鳥(春秋) |
| ヤツガシラ | Upupa epops | Eurasian Hoopoe | 旅鳥(稀) |
| ウズラ | Coturnix japonica | Japanese Quail | 留鳥/冬鳥・減少 |
| アリスイ | Jynx torquilla | Eurasian Wryneck | 冬鳥/旅鳥 |
| ツルクイナ | Gallicrex cinerea | Watercock | 夏鳥(稀)・湿地 |
| シロハラクイナ | Amaurornis phoenicurus | White-breasted Waterhen | 分布拡大中 |
| シロガシラ | Pycnonotus sinensis | Light-vented Bulbul | 九州で分布拡大中 |
| (各種ムシクイ類) | Phylloscopus spp. | leaf warblers | 旅鳥・要選別 |

## 除外（6Kが出したが当地の FP）
山地種(オオアカゲラ/クマゲラ/トラツグミ/ウソ)、外洋海鳥(オオミズナギドリ近縁 Bulwer's Petrel)、
冬鳥(トラフズク/ニシセグロカモメ/クロガモ 等)、迷鳥。→ 追加しない。

## 次アクション
1. Tier1/2 を species_master.csv に追記(status/group/sci 整備)。
2. XC 等から各種 ≥30 録音を収集(朝の applicable モデル手順 `scripts/`)。録音 30 未満は保留。
3. 再学習 → models.tflite 更新 → seasonal_occurrence.csv 再生成(新ラベルで突合) → A/B 再評価。
4. dispatch 欠落3種(Tier1)が入れば Stage2 gull/duck の取りこぼしも解消。
