from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import numpy as np
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

scan_progress = {
    "US": {"status": "IDLE", "percent": 0},
    "KOSPI": {"status": "IDLE", "percent": 0},
    "KOSDAQ": {"status": "IDLE", "percent": 0}
}

def send_telegram_message(message: str):
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN" or not TELEGRAM_BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except Exception as e: print(f"Telegram error: {e}")

# --- DATABASE SETUP ---
db = SqliteDatabase('stock_insight.db')

class PortfolioItem(Model):
    market = CharField() # US, KOSPI, KOSDAQ
    ticker = CharField()
    name = CharField()
    entry_price = FloatField()
    current_price = FloatField()
    max_price = FloatField()
    score = FloatField()
    target_price = FloatField()
    stop_loss = FloatField()
    quantity = IntegerField()
    status = CharField(default="ACTIVE")
    entry_date = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db

db.connect()
db.create_tables([PortfolioItem])

# --- CLASSIC INDICATORS ---

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_strategy(ticker, price, hist, budget):
    try:
        high_low = hist['High'] - hist['Low']
        atr = high_low.rolling(window=14).mean().iloc[-1]
        stop_loss = price - (atr * 2.0)
        risk_per_share = price - stop_loss
        quantity = int((budget * 0.02) / risk_per_share) if risk_per_share > 0 else 1
        target_price = price + (risk_per_share * 3.0) # 리스크 대비 보상비 1:1.5 적용
        return max(1, quantity), round(target_price, 2), round(stop_loss, 2)
    except:
        return 1, round(price * 1.15, 2), round(price * 0.90, 2)

# --- ENGINE ---

def run_full_market_scan(market: str):
    global scan_progress
    tickers = []
    name_map = {}
    
    if market == "US":
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "AVGO", "NFLX", "AMD", "PLTR", "WMT", "COST", "CRM", "ADBE"]
    else:
        df_krx = fdr.StockListing('KRX')
        code_col = 'Code' if 'Code' in df_krx.columns else 'Symbol'
        # KOSPI 또는 KOSDAQ 필터링
        target_market = 'KOSPI' if market == 'KOSPI' else 'KOSDAQ'
        df_filtered = df_krx[df_krx['Market'] == target_market]
        
        for _, row in df_filtered.iterrows():
            symbol = row[code_col]
            t = f"{symbol}.KS" if market == 'KOSPI' else f"{symbol}.KQ"
            tickers.append(t)
            name_map[t] = row['Name']
    
    total_count = len(tickers)
    scan_progress[market] = {"status": "SCANNING", "percent": 0}
    budget = MY_BUDGET_USD if market == "US" else MY_BUDGET_KRW
    
    try:
        batch_size = 30 # 서버 안정성을 위해 배치 사이즈 축소
        scanned = []
        
        for i in range(0, total_count, batch_size):
            batch = tickers[i:i+batch_size]
            try:
                data = yf.download(batch, period="1y", group_by="ticker", threads=True, progress=False)
                
                def analyze(ticker):
                    try:
                        hist = data[ticker].dropna(how='all')
                        if len(hist) < 50: return None
                        close = hist['Close']
                        curr_price = float(close.iloc[-1])
                        
                        sma50 = close.rolling(window=50).mean().iloc[-1]
                        sma200 = close.rolling(window=200).mean().iloc[-1]
                        rsi = calculate_rsi(close).iloc[-1]
                        
                        score = 50
                        if curr_price > sma50: score += 20
                        if sma50 > sma200: score += 20
                        if 40 < rsi < 70: score += 10 # 과매수/과매도 제외 적정 구간
                        
                        if market != "US" and curr_price < 1000: return None
                        
                        qty, target, stop = calculate_strategy(ticker, curr_price, hist, budget)
                        return {
                            "market": market, "ticker": ticker, "name": name_map.get(ticker, ticker),
                            "entry_price": curr_price, "current_price": curr_price, "max_price": curr_price,
                            "score": score, "target_price": target, "stop_loss": stop, "quantity": qty
                        }
                    except: return None

                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    batch_res = [r for r in list(executor.map(analyze, batch)) if r]
                scanned.extend(batch_res)
                
                percent = int((min(i + batch_size, total_count) / total_count) * 100)
                scan_progress[market]["percent"] = percent
            except: continue
        
        scanned.sort(key=lambda x: x['score'], reverse=True)
        top_10 = scanned[:10]
        
        with db.atomic():
            PortfolioItem.delete().where(PortfolioItem.market == market).execute()
            for s in top_10:
                PortfolioItem.create(**s)
        
        scan_progress[market] = {"status": "IDLE", "percent": 0}
        send_telegram_message(f"✅ {market} TOP 10 스캔 완료!")
        
    except Exception as e:
        print(f"Scan error: {e}")
        scan_progress[market] = {"status": "IDLE", "percent": 0}

# --- SCHEDULER ---
scheduler = BackgroundScheduler(timezone="Asia/Seoul")
scheduler.add_job(run_full_market_scan, CronTrigger(hour=16, minute=10), args=['KOSPI'])
scheduler.add_job(run_full_market_scan, CronTrigger(hour=16, minute=20), args=['KOSDAQ'])
scheduler.add_job(run_full_market_scan, CronTrigger(hour=6, minute=10), args=['US'])
scheduler.start()

@app.get("/api/portfolio")
async def get_ai_portfolio(market: str = "KOSPI"):
    items = PortfolioItem.select().where(PortfolioItem.market == market).order_by(PortfolioItem.score.desc())
    return [{
        "ticker": i.ticker, "name": i.name, "entryPrice": i.entry_price, "currentPrice": i.current_price,
        "currentYield": round(((i.current_price/i.entry_price)-1)*100, 2), "maxYield": round(((i.max_price/i.entry_price)-1)*100, 2),
        "targetPrice": i.target_price, "stopLoss": i.stop_loss, "quantity": i.quantity,
        "entryDate": i.entry_date.strftime('%Y-%m-%d')
    } for i in items]

@app.get("/api/scan-status")
async def get_scan_status(market: str = "KOSPI"):
    return scan_progress.get(market, {"status": "IDLE", "percent": 0})

@app.get("/api/stocks/{ticker}/history")
async def get_stock_history(ticker: str):
    data = yf.Ticker(ticker).history(period="1y")
    return [{"time": int(index.timestamp()), "open": row["Open"], "high": row["High"], "low": row["Low"], "close": row["Close"]} for index, row in data.iterrows()]

@app.post("/api/rebalance")
async def trigger_manual_rebalance(market: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_full_market_scan, market)
    return {"message": f"{market} Scan started."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
