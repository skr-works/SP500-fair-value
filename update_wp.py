import os
import sys
import datetime
import pytz
import requests
import pandas as pd
import yfinance as yf
import time

# --- 設定: 環境変数から取得 ---
try:
    WP_URL = os.environ["WP_URL"]
    WP_USER = os.environ["WP_USER"]
    WP_APP_PASS = os.environ["WP_APP_PASS"]
    WP_PAGE_ID = os.environ["WP_PAGE_ID"]
except KeyError as e:
    print(f"エラー: 環境変数 {e} が設定されていません。")
    sys.exit(1)

# --- 1. データ鮮度チェック (市場休日判定) ---
def check_market_status():
    print("市場ステータスを確認中...")
    
    # 米国東部時間 (ET) を基準にする
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(et_tz)
    
    # 代表銘柄 (SPY) の最新データを取得
    spy = yf.Ticker("SPY")
    # 直近5日分取得して最後の行を見る
    hist = spy.history(period="5d")
    
    if hist.empty:
        print("データが取得できませんでした。終了します。")
        sys.exit(0)

    # 最終取引日を取得 (日付型)
    last_trade_date = hist.index[-1].astimezone(et_tz).date()
    current_date = now_et.date()
    
    # 最終取引日が「今日」または「昨日」ならOK
    # (日本の朝実行時、米国は「昨日の夕方」か「今日の深夜」)
    days_diff = (current_date - last_trade_date).days
    
    if days_diff > 1:
        print(f"最新データ日付: {last_trade_date}, 現在日付(ET): {current_date}")
        print("市場休日のため、データが更新されていません。処理を終了します。")
        sys.exit(0)
    
    print(f"データ確認OK (最終取引日: {last_trade_date})")

# --- 2. データ取得と計算 ---
def get_sp500_data():
    print("S&P500リストを取得中...")
    # Wikipediaからリスト取得
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    tables = pd.read_html(url)
    df_sp500 = tables[0]
    tickers = df_sp500['Symbol'].tolist()
    
    # yfinance用にドットをハイフンに変換 (例: BRK.B -> BRK-B)
    tickers = [t.replace('.', '-') for t in tickers]
    
    # テスト用（全銘柄やると時間がかかる場合、ここを tickers[:10] などに制限）
    # tickers = tickers[:20] 

    results = []
    
    print(f"{len(tickers)} 銘柄のデータ取得を開始します...")
    
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # 必須データの取得
            price = info.get('currentPrice')
            eps = info.get('trailingEps')
            
            # 成長率: earningsGrowth (次回決算予想成長率) または revenueGrowth を代用
            # ここでは earningsGrowth を優先し、なければ revenueGrowth、それもなければ除外
            growth_raw = info.get('earningsGrowth')
            if growth_raw is None:
                growth_raw = info.get('revenueGrowth')
            
            # 配当利回り
            yield_raw = info.get('dividendYield', 0) # なければ0
            if yield_raw is None: yield_raw = 0

            short_name = info.get('shortName', ticker)
            sector = info.get('sector', 'Unknown')

            # 計算不能なデータの除外
            if price is None or eps is None or growth_raw is None:
                continue

            # --- 計算ロジック (Peter Lynch Fair Value) ---
            # データは小数 (例: 0.15) で来るため、パーセント整数 (15) に変換して計算
            growth_pct = growth_raw * 100
            yield_pct = yield_raw * 100
            
            # マイナス成長や異常値のガード（簡易的にマイナス成長は除外など）
            # ここではそのまま計算するが、フェアバリューがマイナスになる場合は後で弾く
            
            # FV = EPS * (Growth + Yield)
            fair_value = eps * (growth_pct + yield_pct)
            
            if fair_value <= 0 or price <= 0:
                continue

            # Upside = ((FV - Price) / Price) * 100
            upside = ((fair_value - price) / price) * 100
            
            results.append({
                'ticker': ticker,
                'name': short_name,
                'price': price,
                'fair_value': fair_value,
                'upside': upside,
                'sector': sector
            })
            
            # API制限回避のため少し待機
            time.sleep(0.1)
            
        except Exception as e:
            # エラーが出ても止まらず次へ
            print(f"Error fetching {ticker}: {e}")
            continue

    # Upside順にソート (降順)
    sorted_data = sorted(results, key=lambda x: x['upside'], reverse=True)
    return sorted_data

# --- 3. HTML生成 ---
def generate_html(data):
    print("HTML生成中...")
    
    # ロジック解説エリア
    html = """
    <h2>算出ロジックについて</h2>
    <p>伝説のファンドマネージャー、ピーター・リンチ氏が提唱した簡易式に基づき算出しています。</p>
    <blockquote>適正株価 = EPS × (成長率 + 配当利回り)</blockquote>
    <p>※PEGレシオ=1を基準とした簡易モデルです。成長率はアナリスト予想等を使用しています。</p>
    <br>
    """
    
    # テーブル開始
    html += "<table>"
    html += """
    <thead>
        <tr>
            <th>ティッカー / 社名</th>
            <th>現在株価</th>
            <th>理論株価</th>
            <th>割安度</th>
            <th>セクター</th>
        </tr>
    </thead>
    <tbody>
    """
    
    for item in data:
        # 割安度の色付け
        upside_val = item['upside']
        upside_str = f"{upside_val:+.1f}%"
        
        if upside_val > 0:
            upside_html = f'<span style="color: #0033cc; font-weight: bold;">{upside_str}</span>'
        else:
            upside_html = f'<span style="color: #cc0000; font-weight: bold;">{upside_str}</span>'
            
        row = f"""
        <tr>
            <td><strong>{item['ticker']}</strong><br><small>{item['name']}</small></td>
            <td>${item['price']:.2f}</td>
            <td>${item['fair_value']:.2f}</td>
            <td>{upside_html}</td>
            <td>{item['sector']}</td>
        </tr>
        """
        html += row

    html += "</tbody></table>"
    
    # 免責事項
    html += """
    <br>
    <small>本情報は自動計算されたものであり、投資勧誘を目的としたものではありません。投資判断は自己責任で行ってください。</small>
    """
    
    return html

# --- 4. WordPressへ投稿 ---
def update_wordpress(html_content):
    print("WordPressへ投稿中...")
    
    api_url = f"{WP_URL}/wp-json/wp/v2/pages/{WP_PAGE_ID}"
    
    # Basic認証用のヘッダー作成
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
    # 1. 市場チェック
    check_market_status()
    
    # 2. データ取得・計算
    data = get_sp500_data()
    
    if not data:
        print("有効なデータが1件もありませんでした。終了します。")
        sys.exit(0)
        
    # 3. HTML生成
    html_content = generate_html(data)
    
    # 4. WordPress更新
    update_wordpress(html_content)
