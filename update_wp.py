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
import concurrent.futures # 追加: 並列処理用

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

# --- 個別銘柄処理用関数 (並列化のために切り出し) ---
def process_ticker(ticker):
    try:
        stock = yf.Ticker(ticker)
        # fast_infoは早いが項目が足りないことがあるため、通常のinfoを使用
        info = stock.info
        
        price = info.get('currentPrice')
        
        # EPS取得: 予想EPS優先
        eps = info.get('forwardEps')
        if eps is None:
            eps = info.get('trailingEps')
        
        # 必須データ(価格とEPS)がない、または赤字企業は除外
        if price is None or eps is None or eps <= 0:
            return None

        # 成長率取得
        growth_raw = info.get('earningsGrowth')
        if growth_raw is None:
            growth_raw = info.get('revenueGrowth')
        
        # 修正: 成長率データが取れない場合はスキップせず、保守的な値(5%)を割り当てる
        # これにより出力される企業数が大幅に増えます
        if growth_raw is None:
            growth_raw = 0.05 

        yield_raw = info.get('dividendYield', 0)
        if yield_raw is None: yield_raw = 0

        short_name = info.get('shortName', ticker)
        sector = info.get('sector', 'Unknown')

        growth_pct = growth_raw * 100
        yield_pct = yield_raw * 100
        
        # 成長率キャップ: 最大25% (ピーター・リンチ式)
        capped_growth_pct = min(growth_pct, 25.0)
        
        # マイナス成長の場合は0%とする（株価算出の安定化）
        if capped_growth_pct < 0:
            capped_growth_pct = 0
        
        # 理論株価計算
        fair_value = eps * (capped_growth_pct + yield_pct)
        
        # 理論株価がマイナスや異常に低い場合は除外
        if fair_value <= 0:
            return None

        upside = ((fair_value - price) / price) * 100
        
        # ノイズ除去フィルタ: 
        # 上限を300%から1000%に緩和（より多くの企業を通すため）
        if upside > 1000:
            return None

        return {
            'ticker': ticker,
            'name': short_name,
            'price': price,
            'fair_value': fair_value,
            'upside': upside,
            'sector': sector
        }
        
    except Exception:
        return None

# --- 2. データ取得と計算 (並列処理版) ---
def get_sp500_data():
    print("S&P500リストを取得中...")
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    
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
    
    print(f"{len(tickers)} 銘柄のデータ取得を開始します (並列処理)...")
    
    results = []
    
    # 並列処理: 同時に20銘柄ずつ処理を行うことで時間を短縮
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # process_ticker関数を各tickerに適用
        futures = list(executor.map(process_ticker, tickers))
        
    # Noneを除外して結果を格納
    for res in futures:
        if res is not None:
            results.append(res)

    print(f"取得完了: 有効データ {len(results)} 件")

    sorted_data = sorted(results, key=lambda x: x['upside'], reverse=True)
    return sorted_data

# --- 3. HTML生成 ---
def generate_html(data):
    print("HTML生成中...")
    
    html = """
    <h2>算出ロジックについて</h2>
    <p>伝説のファンドマネージャー、ピーター・リンチ氏が提唱した簡易式に基づき算出しています。</p>
    <blockquote>適正株価 = 予想EPS × (成長率 + 配当利回り)</blockquote>
    <p>※PEGレシオ=1を基準とした簡易モデルです。成長率は最大25%を上限とし、データ欠損時は保守的な値を採用しています。</p>
    <br>
    """
    
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
