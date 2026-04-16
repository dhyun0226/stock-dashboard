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
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN": return
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
    if len(series) < period + 1: return pd.Series([50] * len(series))
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_strategy(ticker, price, hist, budget):
    try:
        high_low = hist['High'] - hist['Low']
        atr = high_low.rolling(window=14).mean().iloc[-1]
        if pd.isna(atr) or atr == 0: atr = price * 0.02
        stop_loss = price - (atr * 2.0)
        risk_per_share = price - stop_loss
        quantity = int((budget * 0.02) / risk_per_share) if risk_per_share > 0 else 1
        target_price = price + (risk_per_share * 3.0)
        return max(1, quantity), round(target_price, 2), round(stop_loss, 2)
    except:
        return 1, round(price * 1.15, 2), round(price * 0.90, 2)

# --- ENGINE ---

def run_full_market_scan(market: str):
    global scan_progress
    tickers = []
    name_map = {}
    
    print(f"[{market}] Starting Scan Optimization...")
    
    if market == "US":
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "AVGO", "NFLX", "AMD", "PLTR", "WMT", "COST", "CRM", "ADBE"]
    else:
        try:
            df_krx = fdr.StockListing('KRX')
            code_col = 'Code' if 'Code' in df_krx.columns else 'Symbol'
            target_market = 'KOSPI' if market == 'KOSPI' else 'KOSDAQ'
            df_filtered = df_krx[df_krx['Market'] == target_market]
            
            # 시가총액/거래량 상위 300개로 제한 (안정성 및 퀄리티 확보)
            df_top = df_filtered.head(300) 
            
            for _, row in df_top.iterrows():
                symbol = row[code_col]
                t = f"{symbol}.KS" if market == 'KOSPI' else f"{symbol}.KQ"
                tickers.append(t)
                name_map[t] = row['Name']
        except Exception as e:
            print(f"Ticker fetch error: {e}")
            scan_progress[market] = {"status": "IDLE", "percent": 0}
            return

    total_count = len(tickers)
    if total_count == 0:
        scan_progress[market] = {"status": "IDLE", "percent": 0}
        return

    scan_progress[market] = {"status": "SCANNING", "percent": 0}
    budget = MY_BUDGET_USD if market == "US" else MY_BUDGET_KRW
    
    try:
        batch_size = 20
        scanned = []
        
        for i in range(0, total_count, batch_size):
            batch = tickers[i:i+batch_size]
            try:
                data = yf.download(batch, period="1y", group_by="ticker", threads=True, progress=False)
                
                def analyze(ticker):
                    try:
                        if ticker not in data.columns.levels[0]: return None
                        hist = data[ticker].dropna(how='all')
                        if len(hist) < 50: return None
                        close = hist['Close']
                        curr_price = float(close.iloc[-1])
                        
                        sma50 = close.rolling(window=50).mean().iloc[-1]
                        sma200 = close.rolling(window=200).mean().iloc[-1]
                        rsi_series = calculate_rsi(close)
                        rsi = rsi_series.iloc[-1] if not rsi_series.empty else 50
                        
                        score = 50
                        if curr_price > sma50: score += 20
                        if sma50 > sma200: score += 20
                        if 30 < rsi < 75: score += 10
                        
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
                print(f"[{market}] Progress: {percent}% ({len(scanned)} hits)")
            except Exception as e:
                print(f"Batch error: {e}")
                continue
        
        if scanned:
            scanned.sort(key=lambda x: x['score'], reverse=True)
            top_10 = scanned[:10]
            
            with db.atomic():
                PortfolioItem.delete().where(PortfolioItem.market == market).execute()
                for s in top_10:
                    PortfolioItem.create(**s)
            
            send_telegram_message(f"✅ {market} 스캔 완료! {len(top_10)}개 종목 선정.")
        
        scan_progress[market] = {"status": "IDLE", "percent": 0}
        
    except Exception as e:
        print(f"Global scan error: {e}")
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
