import os
import sys
import datetime
import pytz
import requests
import pandas as pd
import yfinance as yf
import time
import json
from io import StringIO

# --- 設定: 環境変数(JSON)から一括取得 ---
try:
    creds_json = os.environ["WP_CREDENTIALS"]
    creds = json.loads(creds_json)

    WP_URL = creds["WP_URL"]
    WP_USER = creds["WP_USER"]
    WP_APP_PASS = creds["WP_APP_PASS"]
    WP_PAGE_ID = creds["WP_PAGE_ID"]
    
except KeyError as e:
    print(f"エラー: 環境変数 {e} が見つかりません。")
    sys.exit(1)
except json.JSONDecodeError:
    print("エラー: WP_CREDENTIALS のJSON形式が正しくありません。")
    sys.exit(1)

# --- 1. データ鮮度チェック (市場休日判定) ---
def check_market_status():
    print("市場ステータスを確認中...")
    
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(et_tz)
    
    spy = yf.Ticker("SPY")
    hist = spy.history(period="5d")
    
    if hist.empty:
        print("データが取得できませんでした。終了します。")
        sys.exit(0)

    last_trade_date = hist.index[-1].astimezone(et_tz).date()
    current_date = now_et.date()
    
    days_diff = (current_date - last_trade_date).days
    
    if days_diff > 1:
        print(f"最新データ日付: {last_trade_date}, 現在日付(ET): {current_date}")
        print("市場休日のため、データが更新されていません。処理を終了します。")
        sys.exit(0)
    
    print(f"データ確認OK (最終取引日: {last_trade_date})")

# --- 2. データ取得と計算 (修正版: 現実的なフィルタリング適用) ---
def get_sp500_data():
    print("S&P500リストを取得中...")
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    
    # Wikipediaへのアクセス拒否回避用ヘッダー
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        df_sp500 = tables[0]
    except Exception as e:
        print(f"S&P500リスト取得エラー: {e}")
        sys.exit(1)

    tickers = df_sp500['Symbol'].tolist()
    tickers = [t.replace('.', '-') for t in tickers]
    
    results = []
    
    print(f"{len(tickers)} 銘柄のデータ取得を開始します...")
    
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            price = info.get('currentPrice')
            
            # 【変更点1】 EPSは「予想EPS (Forward EPS)」を優先使用
            # これにより過去の特別損失などの影響を排除し、未来の収益力を反映させる
            eps = info.get('forwardEps')
            if eps is None:
                eps = info.get('trailingEps')
            
            # 成長率の取得
            growth_raw = info.get('earningsGrowth')
            if growth_raw is None:
                growth_raw = info.get('revenueGrowth')
            
            yield_raw = info.get('dividendYield', 0)
            if yield_raw is None: yield_raw = 0

            short_name = info.get('shortName', ticker)
            sector = info.get('sector', 'Unknown')

            # 必須データ欠損チェック
            if price is None or eps is None or growth_raw is None:
                continue
            
            # 赤字見通しの企業は計算対象外とする
            if eps <= 0:
                continue

            growth_pct = growth_raw * 100
            yield_pct = yield_raw * 100
            
            # 【変更点2】 成長率に上限(キャップ)を設定
            # ピーター・リンチの法則: "年率25%を超える成長が永続することは稀"
            # 計算上、成長率は最大25%として扱うことで、異常な理論株価を防ぐ
            capped_growth_pct = min(growth_pct, 25.0)
            
            # 成長率がマイナスの場合は0%として扱い、過度な割安判定を防ぐ
            if capped_growth_pct < 0:
                capped_growth_pct = 0

            # 理論株価算出: EPS × (調整後成長率 + 配当利回り)
            fair_value = eps * (capped_growth_pct + yield_pct)
            
            if fair_value <= 0:
                continue

            upside = ((fair_value - price) / price) * 100
            
            # 【変更点3】 あまりに非現実的な乖離（+300%以上など）はデータノイズの可能性が高いため除外
            if upside > 300:
                continue

            results.append({
                'ticker': ticker,
                'name': short_name,
                'price': price,
                'fair_value': fair_value,
                'upside': upside,
                'sector': sector
            })
            
            time.sleep(0.1)
            
        except Exception as e:
            continue

    # 割安度が高い順にソート
    sorted_data = sorted(results, key=lambda x: x['upside'], reverse=True)
    return sorted_data

# --- 3. HTML生成 ---
def generate_html(data):
    print("HTML生成中...")
    
    # 算出ロジックの説明文も、今回の変更に合わせて更新
    html = """
    <h2>S&P500 割安株ランキング (ピーター・リンチ改良版)</h2>
    <p>伝説の投資家ピーター・リンチ氏の指標を参考に、より実践的に調整した理論株価ランキングです。</p>
    <div style="background-color: #f0f0f0; padding: 10px; border-radius: 5px; font-size: 0.9em;">
        <strong>算出ロジック:</strong><br>
        理論株価 = 予想EPS × (成長率 + 配当利回り)<br>
        <br>
        <small>
        ※異常値を防ぐため、計算上の成長率は<strong>最大25%</strong>に制限しています。<br>
        ※EPS(一株当たり利益)は、来期予想値(Forward EPS)を優先して使用しています。
        </small>
    </div>
    <br>
    """
    
    # テーブル設定
    html += '<table style="font-size: 10px; line-height: 1.2; border-collapse: collapse; width: 100%;">'
    html += """
    <thead>
        <tr style="background-color: #e6e6e6;">
            <th style="padding: 4px; text-align: left;">銘柄</th>
            <th style="padding: 4px; text-align: right;">株価</th>
            <th style="padding: 4px; text-align: right;">理論値</th>
            <th style="padding: 4px; text-align: right;">割安度</th>
        </tr>
    </thead>
    <tbody>
    """
    
    for item in data:
        upside_val = item['upside']
        upside_str = f"{upside_val:+.1f}%"
        
        # 割安度に応じた色分け
        if upside_val > 0:
            upside_html = f'<span style="color: #008000; font-weight: bold;">{upside_str}</span>' # 緑色
        else:
            upside_html = f'<span style="color: #cc0000; font-weight: bold;">{upside_str}</span>' # 赤色
            
        row = f"""
        <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 4px;">
                <strong>{item['ticker']}</strong><br>
                <span style="font-size: 8px; color: #555;">{item['name'][:20]}</span>
            </td>
            <td style="padding: 4px; text-align: right;">${item['price']:.2f}</td>
            <td style="padding: 4px; text-align: right;">${item['fair_value']:.2f}</td>
            <td style="padding: 4px; text-align: right;">{upside_html}</td>
        </tr>
        """
        html += row

    html += "</tbody></table>"
    
    html += """
    <br>
    <p style="font-size: 0.8em; color: #666;">
    免責事項: 本情報は自動計算プログラムによる参考値であり、正確性を保証するものではありません。
    投資判断は必ずご自身の責任において行ってください。
    </p>
    """
    
    return html

# --- 4. WordPressへ投稿 ---
def update_wordpress(html_content):
    print("WordPressへ投稿中...")
    
    api_url = f"{WP_URL}/wp-json/wp/v2/pages/{WP_PAGE_ID}"
    auth = (WP_USER, WP_APP_PASS)
    
    payload = {
        'content': html_content
    }
    
    response = requests.post(api_url, json=payload, auth=auth)
    
    if response.status_code == 200:
        print("成功: 固定ページが更新されました。")
    else:
        print(f"失敗: ステータスコード {response.status_code}")
        print(response.text)
        sys.exit(1)

# --- メイン実行フロー ---
if __name__ == "__main__":
    check_market_status()
    data = get_sp500_data()
    
    if not data:
        print("有効なデータが1件もありませんでした。終了します。")
        sys.exit(0)
        
    html_content = generate_html(data)
    update_wordpress(html_content)
