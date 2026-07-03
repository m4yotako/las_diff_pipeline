---
name: las-pipeline-safe-workflow
description: >
  LAS差分パイプライン（las_diff_pipeline）の変更作業を、調査→範囲宣言→承認待ち→最小実装→検証→報告の
  5フェーズで安全に進めるためのワークフロー。防災・被害判定に関わる出力の正確性を損なう変更を防ぐ。
  手動起動専用（自動発火しない）。
disable-model-invocation: true
---

# LAS Diff Pipeline 安全作業ワークフロー

このワークフローは `/las-pipeline-safe-workflow` で明示的に起動したときのみ実行する。通常の会話や軽微な質問には適用しない。

## フェーズ1: 調査（実装前）

- 着手前に、対象コードをローカルで実際に読むこと（`pipeline/*.py`, `main.py`, `api/app.py`, `api/worker_tasks.py`, `config.example.yaml`, `CLAUDE.md`）。読まずに記憶や推測でコードの挙動を語らない。
- 外部ライブラリ（PDAL, open3d, laspy, rasterio, geopandas, pyproj, osmnx 等）のAPI仕様やバージョン差異が不明な場合、勝手に「たぶんこう動く」と推測して実装しない。公式ドキュメントを実際に確認できないなら、確認できなかった旨と具体的な確認事項を質問としてまとめ、そこで一度停止する（見たふり禁止）。
- 既知の制約は前提として扱う（`CLAUDE.md` 記載）:
  - DSM差分（`method: "dsm"`）が唯一の動作実装。M3C2 (`method: "m3c2"`) は `difference.py` にスタブのみで `NotImplementedError` を送出する（PDAL 2.8.4 に `filters.m3c2` が存在しないため）。
  - ICP は open3d が無ければ `registration._icp_scipy`（Umeyama法）にフォールバックする。
  - Web経由では `io.pre_las` / `io.post_las` / `io.output_dir` はAPIが自動設定するため、config JSONに含めない。
  - `open3d` / `osmnx` は Docker/CI ではコメントアウトが前提（ビルド時間・サイズ）。`fiona` は Apple Silicon でソースビルドになる。

## フェーズ2: 範囲宣言 → 承認待ち

実装に入る前に、以下を明示して報告し、ユーザーの明示的なOK（「OK」「進めて」など）を待つ。無言や曖昧な反応では進めない。

- 変更対象ファイル（フルパス）
- 変更しないもの（明示的に対象外と宣言する）
- 成功条件（何が達成されたら完了とみなすか）
- 検証方法（フェーズ4でどう確認するか、事前に宣言する）

## フェーズ3: 最小実装

- 承認された範囲だけを外科的に変更する。ついでの周辺リファクタ・命名整理・コメント追加はしない。
- 大きな変更（例: 複数ステップにまたがるデータ構造変更、GeoJSONスキーマ変更、APIエンドポイント追加）は一度に行わず、段階に分割してその都度確認を取る。
- `requirements.txt` の依存追加・バージョン更新は最小限に留め、無断でメジャーバージョンを上げたり、コメントアウトされている重量依存（`open3d`, `osmnx`, `pdal`）を勝手に有効化しない。

## フェーズ4: 検証

変更内容に応じて、実際に手を動かして確認する。「動くはず」で済ませない。

- **共通・必須**: 合成データでのエンドツーエンドスモークテスト
  ```bash
  cd examples
  python generate_synthetic.py
  python main.py --config config_synthetic.yaml
  ```
  これが通らない変更は「検証済み」と報告しない。

- **個別ステップの変更**（preprocess / registration / difference / scoring / aggregation / export）:
  ```bash
  python main.py --config config.yaml --step <該当ステップ>
  ```
  変更したステップだけでなく、後続ステップが壊れていないか `--step` なしのフル実行も通す。

- **CRS・座標変換に関わる変更**: 出力 GeoJSON（`outputs/damage_score.geojson` 相当）を開き、座標が現実的な緯度経度範囲（WGS84）に収まっているか目視で確認する。ここがズレると被害判定の位置情報が全部壊れるため省略不可。

- **API/Web（`api/app.py`, `api/worker_tasks.py`, `docker-compose.yml`）の変更**:
  ```bash
  docker compose up --build
  curl http://localhost:8000/health
  ```
  実行環境の制約でDocker起動を検証できない場合は、「未検証項目」としてフェーズ5で明示する（できたふりをしない）。

- **テストスイート**: 現時点でこのリポジトリに `tests/` ディレクトリや pytest 設定は存在しない（`requirements.txt` では pytest はコメントアウトのみ）。テストが無いことを前提に上記の実運用確認で代替する。将来 `tests/` が追加された場合はここで `pytest` を実行対象に含める。

## フェーズ5: 報告

以下を簡潔にまとめて報告する。

- 変更したファイルとその理由
- 検証結果（何を実行し、何を確認したか）
- 未検証項目（環境上の制約で確認できなかったこと。例: Docker起動、実データでのICP精度など）

## 変更禁止範囲

以下は、明示的な承認なしに変更・弱体化してはいけない。

- `difference.py` の M3C2 スタブを、実装せずに `NotImplementedError` を消して「動くふり」をすること（PDAL側の制約が解消されていない限り虚偽の実装は禁止）
- CRS変換・座標系まわりのロジックを黙って変更すること（被害位置がズレる＝防災用途で致命的）
- `scoring.thresholds` のデフォルト閾値（0.3 / 1.0 / 3.0 m）を無断で変更すること
- 出力 GeoJSON の `properties` スキーマ（`damage_score`, `dz_mean`, `dz_p95`, `n_points_pre`, `n_points_post`, `loss_ratio`, `method`, `significant` 等）を無断で変更・削除すること（Google Maps側の消費者が依存している）
- `/jobs`, `/jobs/{id}`, `/jobs/{id}/result`, `/health` のAPIインターフェースを後方互換なく変更すること
- Docker構成（`api` / `worker` / `redis:7-alpine` の3サービス、`job_data` 名前付きボリューム）を破壊すること
- `open3d` / `osmnx` / `pdal` など重量・ビルド制約のある依存を、Docker/CI側の事情を無視して無断で有効化すること
- `config.example.yaml` のキーを、ドキュメントとしての整合性を壊す形で削除・意味変更すること

## Git運用

このディレクトリは現時点で git リポジトリではない（`.git` が存在しない）。git コマンドを使う前に、まず以下で確認すること。

```bash
git rev-parse --is-inside-work-tree
```

- git管理下でない場合: コミット・push・PRといった概念は適用されない。ファイルの変更はローカルファイルシステム上の変更として報告する。
- 将来 git 管理下に置かれた場合は、次を踏襲する:
  - `main`（または既定ブランチ）への直接pushは禁止
  - 変更はPRとして提案し、ユーザーの指示なしに自動マージしない
  - push / PR作成は、ユーザーが明示的に指示したときのみ実行する
