# S&P 500適正株価算出

米国株S&P 500構成銘柄の財務データを毎日取得し、ピーター・リンチの公式に基づいて「適正株価（Fair Value）」を算出します。

## 機能概要

* **適正株価算出**: ピーター・リンチの簡易式 `Fair Value = EPS * (Growth Rate + Dividend Yield)` を採用。
* **割安度計算**: 現在株価と理論株価の乖離（Upside %）を計算し、割安な順にソート。
* **市場休日判定**: 米国市場が休場（土日祝）の場合、古いデータで更新しないよう処理をスキップする安全装置付き。

## 算出ロジック

伝説のファンドマネージャー、ピーター・リンチ氏の考え方をベースにした簡易モデル（PEGレシオ=1基準）を使用しています。

$$
\text{Fair Value} = \text{EPS (TTM)} \times (\text{Growth Rate} + \text{Dividend Yield})
$$

* **Growth Rate**: アナリスト予想成長率、または過去の売上/利益成長率を使用（取得できない場合は除外）。
* **Dividend Yield**: 配当利回り（%）。

## 動作環境

* Python 3.11+
* GitHub Actions (Ubuntu-latest)

## セットアップ手順

### 1. リポジトリの準備
このリポジトリをForkまたはCloneし、以下のファイルを配置します。
* `update_wp.py`: メインスクリプト
* `requirements.txt`: 依存ライブラリ
* `.github/workflows/wp_update.yml`: 自動化設定

### 2. GitHub Secretsの設定
リポジトリの `Settings` > `Secrets and variables` > `Actions` にて、以下の名前でSecretを**1つ**登録します。

**Name**: `WP_CREDENTIALS`

**Secret (Value)**:
以下のJSON形式で入力してください。
```json
{
  "WP_URL": "[https://your-site.com](https://your-site.com)",
  "WP_USER": "your_username",
  "WP_APP_PASS": "xxxx xxxx xxxx xxxx",
  "WP_PAGE_ID": "1234"
}

```

※ `WP_URL` の末尾にスラッシュは不要です。
※ `WP_PAGE_ID` は更新したい固定ページのIDを指定します。

## 実行スケジュール

`.github/workflows/wp_update.yml` により制御されます。

* **スケジュール**: `30 21 * * 1-5` (UTC)
* 日本時間: **火・水・木・金・土 の 朝 06:30**
* 米国市場（月〜金）のクローズ後にデータを取得して実行されます。


* **手動実行**: GitHub Actionsタブから「Run workflow」ボタンで即時実行可能です。

## 免責事項

本ツールによって算出される数値は機械的に計算されたものであり、将来の株価を保証するものではありません。投資判断は自己責任で行ってください。

```

```
