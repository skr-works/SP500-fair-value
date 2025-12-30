import os
import sys
import datetime
import pytz
import requests
import pandas as pd
import yfinance as yf
import time
import json
from io import StringIO  # 追加: 文字列をファイルのように扱うために必要

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

# --- 2. データ取得と計算 (修正: ヘッダー追加による403回避) ---
def get_sp500_data():
    print("S&P500リストを取得中...")
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    
    # 修正: ブラウザのふりをするためのヘッダー
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        # requests経由で取得
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        # StringIOを使ってpandasに渡す
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
            eps = info.get('trailingEps')
            
            growth_raw = info.get('earningsGrowth')
            if growth_raw is None:
                growth_raw = info.get('revenueGrowth')
            
            yield_raw = info.get('dividendYield', 0)
            if yield_raw is None: yield_raw = 0

            short_name = info.get('shortName', ticker)
            
            # HTML生成では使用しないが取得しておく
            sector = info.get('sector', 'Unknown')

            if price is None or eps is None or growth_raw is None:
                continue

            growth_pct = growth_raw * 100
            yield_pct = yield_raw * 100
            
            fair_value = eps * (growth_pct + yield_pct)
            
            if fair_value <= 0 or price <= 0:
                continue

            upside = ((fair_value - price) / price) * 100
            
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

    sorted_data = sorted(results, key=lambda x: x['upside'], reverse=True)
    return sorted_data

# --- 3. HTML生成 ---
def generate_html(data):
    print("HTML生成中...")
    
    html = """
    <h2>算出ロジックについて</h2>
    <p>伝説のファンドマネージャー、ピーター・リンチ氏が提唱した簡易式に基づき算出しています。</p>
    <blockquote>適正株価 = EPS × (成長率 + 配当利回り)</blockquote>
    <p>※PEGレシオ=1を基準とした簡易モデルです。成長率はアナリスト予想等を使用しています。</p>
    <br>
    """
    
    # テーブル設定: フォント10px, 行間詰め, 枠線結合
    html += '<table style="font-size: 10px; line-height: 1.2; border-collapse: collapse; width: 100%;">'
    html += """
    <thead>
        <tr>
            <th style="padding: 2px 4px;">ティッカー</th>
            <th style="padding: 2px 4px;">社名</th>
            <th style="padding: 2px 4px;">現在株価</th>
            <th style="padding: 2px 4px;">理論株価</th>
            <th style="padding: 2px 4px;">割安度</th>
        </tr>
    </thead>
    <tbody>
    """
    
    for item in data:
        upside_val = item['upside']
        upside_str = f"{upside_val:+.1f}%"
        
        if upside_val > 0:
            upside_html = f'<span style="color: #0033cc; font-weight: bold;">{upside_str}</span>'
        else:
            upside_html = f'<span style="color: #cc0000; font-weight: bold;">{upside_str}</span>'
            
        row = f"""
        <tr>
            <td style="padding: 2px 4px;"><strong>{item['ticker']}</strong></td>
            <td style="padding: 2px 4px;"><small style="font-size: 9px;">{item['name']}</small></td>
            <td style="padding: 2px 4px;">${item['price']:.2f}</td>
            <td style="padding: 2px 4px;">${item['fair_value']:.2f}</td>
            <td style="padding: 2px 4px;">{upside_html}</td>
        </tr>
        """
        html += row

    html += "</tbody></table>"
    
    html += """
    <br>
    <small>本情報は自動計算されたものであり、投資勧誘を目的としたものではありません。投資判断は自己責任で行ってください。</small>
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
