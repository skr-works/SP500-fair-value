import os
import sys
import datetime
import pytz
import requests
import pandas as pd
import yfinance as yf
import time
import json
import math  # 追加: 平方根計算用
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
        if price is None:
            return None
        
        # --- 1. EPS (1株当たり利益) の取得 ---
        # 予想EPSを優先、なければ実績EPS
        eps = info.get('forwardEps')
        if eps is None:
            eps = info.get('trailingEps')
        
        # EPSがない、または赤字の場合は計算不能 (グレアム数はルート計算するため正の数必須)
        if eps is None or eps <= 0:
            return None

        # --- 2. BPS (1株当たり純資産) の取得 ---
        bps = info.get('bookValue')
        
        # BPSがない場合、PBRから逆算 (BPS = 株価 / PBR)
        if bps is None:
            pbr = info.get('priceToBook')
            if pbr and pbr > 0:
                bps = price / pbr
        
        # 債務超過(BPSマイナス)の場合は計算不能
        if bps is None or bps <= 0:
            return None

        short_name = info.get('shortName', ticker)
        sector = info.get('sector', 'Unknown')

        # --- 3. グレアム数 (理論株価) の計算 ---
        # 公式: √ (22.5 * EPS * BPS)
        try:
            graham_number = math.sqrt(22.5 * eps * bps)
        except ValueError:
            return None

        fair_value = graham_number
        
        # 理論株価がマイナスや異常に低い場合は除外
        if fair_value <= 0:
            return None

        upside = ((fair_value - price) / price) * 100
        
        # ノイズ除去フィルタ: 
        # グレアム数で+300%以上はよほどの資産バリュー株でない限り稀（データエラーの可能性大）
        if upside > 300:
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
    <p>ベンジャミン・グレアムのミックス係数に基づき算出しています。</p>
    <blockquote>適正株価 = √(22.5 × EPS × BPS)</blockquote>
    <p>※PER 15倍 × PBR 1.5倍 = 22.5 を基準とした理論値です。<br>資産と利益の両面から見た保守的な適正価格を示します。</p>
    <br>
    """
    
    html += '<table style="font-size: 10px; line-height: 1.2; border-collapse: collapse; width: 100%;">'
    html += """
    <thead>
        <tr>
            <th style="padding: 2px 4px;">ティッカー</th>
            <th style="padding: 2px 4px;">社名</th>
            <th style="padding: 2px 4px;">現在株価</th>
            <th style="padding: 2px 4px;">適正株価</th>
            <th style="padding: 2px 4px;">割安度</th>
        </tr>
    </thead>
    <tbody>
    """
    
    for item in data:
        upside_val = item['upside']
        upside_str = f"{upside_val:+.1f}%"
        
        # 割安(プラス)は赤(#cc0000)、割高(マイナス)は青(#0033cc)
        # ※元コードの色定義に合わせて調整
        if upside_val > 0:
            upside_html = f'<span style="color: #cc0000; font-weight: bold;">{upside_str}</span>'
        else:
            upside_html = f'<span style="color: #0033cc; font-weight: bold;">{upside_str}</span>'
            
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
