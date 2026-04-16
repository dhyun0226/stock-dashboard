from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
from typing import List, Optional
import datetime
import concurrent.futures
from peewee import *
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import requests
import FinanceDataReader as fdr
import os
from dotenv import load_dotenv

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURATION ---
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MY_BUDGET_KRW = int(os.getenv("MY_BUDGET_KRW", 10000000))
MY_BUDGET_USD = int(os.getenv("MY_BUDGET_USD", 10000))

# 스캔 상태 추적을 위한 전역 변수
scan_progress = {
    "US": {"status": "IDLE", "current": 0, "total": 0, "percent": 0},
    "KR": {"status": "IDLE", "current": 0, "total": 0, "percent": 0}
}

def send_telegram_message(message: str):
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN": return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except Exception as e: print(f"Telegram error: {e}")

# --- DATABASE SETUP ---
db = SqliteDatabase('stock_insight.db')

class PortfolioItem(Model):
    market = CharField()
    ticker = CharField()
    name = CharField()
    entry_price = FloatField()
    current_price = FloatField()
    max_price = FloatField()
    score = FloatField()
    reason = CharField()
    target_price = FloatField()
    stop_loss = FloatField()
    quantity = IntegerField()
    status = CharField(default="ACTIVE")
    entry_date = DateTimeField(default=datetime.datetime.now)
    exit_date = DateTimeField(null=True)
    exit_price = FloatField(null=True)
    
    buy_alert_sent = BooleanField(default=False)
    target_alert_sent = BooleanField(default=False)
    stop_alert_sent = BooleanField(default=False)

    class Meta:
        database = db

db.connect()
db.create_tables([PortfolioItem])

# --- STRATEGY ---

def calculate_strategy(ticker, price, hist, budget):
    try:
        high_low = hist['High'] - hist['Low']
        atr = high_low.rolling(window=14).mean().iloc[-1]
        stop_loss = price - (atr * 1.5)
        risk_per_share = price - stop_loss
        quantity = int((budget * 0.02) / risk_per_share) if risk_per_share > 0 else 1
        target_price = price + (risk_per_share * 2)
        return max(1, quantity), round(target_price, 2), round(stop_loss, 2)
    except:
        return 1, round(price * 1.1, 2), round(price * 0.95, 2)

# --- ENGINE ---

def run_full_market_scan(market: str):
    global scan_progress
    tickers = []
    name_map = {}
    
    scan_progress[market] = {"status": "STARTING", "current": 0, "total": 0, "percent": 0}
    
    if market == "US":
        tickers = [
            "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "BRK-B", "V", "UNH", 
            "LLY", "JPM", "AVGO", "XOM", "MA", "JNJ", "PG", "COST", "HD", "ABBV", 
            "MRK", "ADBE", "CRM", "CVX", "NFLX", "AMD", "PEP", "TMO", "KO", "WMT"
        ]
    else:
        df_krx = fdr.StockListing('KRX')
        code_col = 'Code' if 'Code' in df_krx.columns else 'Symbol'
        for _, row in df_krx.iterrows():
            symbol = row[code_col]
            m_type = row['Market']
            if m_type == 'KOSPI': t = f"{symbol}.KS"
            elif m_type == 'KOSDAQ': t = f"{symbol}.KQ"
            else: continue
            tickers.append(t)
            name_map[t] = row['Name']
    
    total_count = len(tickers)
    scan_progress[market] = {"status": "SCANNING", "current": 0, "total": total_count, "percent": 0}
    
    budget = MY_BUDGET_USD if market == "US" else MY_BUDGET_KRW
    currency = "$" if market == "US" else "원"
    
    try:
        batch_size = 100
        scanned = []
        
        for i in range(0, total_count, batch_size):
            batch = tickers[i:i+batch_size]
            try:
                data = yf.download(batch, period="1y", group_by="ticker", threads=True, progress=False)
                
                def analyze(ticker):
                    try:
                        hist = data[ticker].dropna(how='all')
                        if len(hist) < 100: return None
                        close = hist['Close']
                        curr_price = float(close.iloc[-1])
                        
                        sma50 = close.rolling(window=50).mean().iloc[-1]
                        sma200 = close.rolling(window=200).mean().iloc[-1]
                        score = 50
                        if curr_price > sma50: score += 20
                        if sma50 > sma200: score += 20
                        if curr_price > close.iloc[-5]: score += 10
                        
                        if market == "KR" and curr_price < 1000: return None
                        
                        qty, target, stop = calculate_strategy(ticker, curr_price, hist, budget)
                        return {
                            "market": market, "ticker": ticker, "name": name_map.get(ticker, ticker),
                            "entry_price": curr_price, "current_price": curr_price, "max_price": curr_price,
                            "score": score, "reason": "전 종목 AI 분석", "target_price": target, "stop_loss": stop, "quantity": qty
                        }
                    except: return None

                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    batch_res = [r for r in list(executor.map(analyze, batch)) if r]
                scanned.extend(batch_res)
                
                # 진행률 업데이트
                current_done = min(i + batch_size, total_count)
                percent = int((current_done / total_count) * 100)
                scan_progress[market] = {"status": "SCANNING", "current": current_done, "total": total_count, "percent": percent}
                
            except: continue
        
        scanned.sort(key=lambda x: x['score'], reverse=True)
        top_10 = scanned[:10]
        new_tickers = [s['ticker'] for s in top_10]

        with db.atomic():
            PortfolioItem.update(status="EXITED", exit_date=datetime.datetime.now(), exit_price=PortfolioItem.current_price).where((PortfolioItem.market == market) & (PortfolioItem.status == "ACTIVE") & (PortfolioItem.ticker << new_tickers == False)).execute()
            for s in top_10:
                item, created = PortfolioItem.get_or_create(market=market, ticker=s['ticker'], status="ACTIVE", defaults=s)
                if not created: 
                    item.current_price = s['entry_price']
                    item.save()
        
        scan_progress[market] = {"status": "IDLE", "current": 0, "total": 0, "percent": 0}
        
        msg = f"📍 *[AI 가이드] 오늘의 {market} 핵심 전략*\n\n"
        for i in top_10:
            msg += f"✅ *{i['name']}* ({i['ticker']})\n"
            msg += f"   • 매수 권장가: `{i['entry_price']:,}{currency}`\n"
            msg += f"   • 권장 수량: `{i['quantity']}주`\n"
            msg += f"   • 익절가: `{i['target_price']:,}{currency}`\n"
            msg += f"   • 손절가: `{i['stop_loss']:,}{currency}`\n\n"
        send_telegram_message(msg)
        
    except Exception as e: 
        print(f"Scan error: {e}")
        scan_progress[market] = {"status": "IDLE", "current": 0, "total": 0, "percent": 0}

def monitor_market_signals():
    now = datetime.datetime.now()
    active_market = "KR" if (9 <= now.hour <= 15) else "US" if (now.hour >= 22 or now.hour <= 5) else None
    if not active_market: return

    items = PortfolioItem.select().where((PortfolioItem.market == active_market) & (PortfolioItem.status == "ACTIVE"))
    for item in items:
        try:
            curr_price = yf.Ticker(item.ticker).fast_info['lastPrice']
            item.current_price = curr_price
            if curr_price > item.max_price: item.max_price = curr_price
            item.save()
        except: continue

scheduler = BackgroundScheduler(timezone="Asia/Seoul")
scheduler.add_job(run_full_market_scan, CronTrigger(hour=16, minute=5), args=['KR'])
scheduler.add_job(run_full_market_scan, CronTrigger(hour=6, minute=5), args=['US'])
scheduler.add_job(monitor_market_signals, 'interval', minutes=1)
scheduler.start()

@app.get("/api/portfolio")
async def get_ai_portfolio(market: str = "US"):
    items = PortfolioItem.select().where((PortfolioItem.market == market) & (PortfolioItem.status == "ACTIVE")).order_by(PortfolioItem.score.desc())
    return [{
        "ticker": i.ticker, "name": i.name, "entryPrice": i.entry_price, "currentPrice": i.current_price,
        "currentYield": round(((i.current_price/i.entry_price)-1)*100, 2), "maxYield": round(((i.max_price/i.entry_price)-1)*100, 2),
        "targetPrice": i.target_price, "stopLoss": i.stop_loss, "quantity": i.quantity,
        "entryDate": i.entry_date.strftime('%Y-%m-%d'), "status": i.status
    } for i in items]

@app.get("/api/scan-status")
async def get_scan_status(market: str = "KR"):
    return scan_progress.get(market, {"status": "IDLE", "percent": 0})

@app.get("/api/stocks/{ticker}/history")
async def get_stock_history(ticker: str):
    data = yf.Ticker(ticker).history(period="1y")
    return [{"time": int(index.timestamp()), "open": row["Open"], "high": row["High"], "low": row["Low"], "close": row["Close"]} for index, row in data.iterrows()]

@app.post("/api/rebalance")
async def trigger_manual_rebalance(market: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_full_market_scan, market)
    return {"message": "Full Scan started."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
