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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MY_BUDGET_KRW = int(os.getenv("MY_BUDGET_KRW", 10000000))
MY_BUDGET_USD = int(os.getenv("MY_BUDGET_USD", 10000))

# 한국 주식 한글 이름 매핑 (주요 종목)
KR_NAME_MAP = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "035420.KS": "NAVER", "035720.KS": "카카오",
    "005380.KS": "현대차", "068270.KS": "셀트리온", "005935.KS": "삼성전자우", "207940.KS": "삼성바이오로직스",
    "051910.KS": "LG화학", "000270.KS": "기아", "006400.KS": "삼성SDI", "005490.KS": "POSCO홀딩스",
    "032830.KS": "삼성생명", "012330.KS": "현대모비스", "010950.KS": "S-Oil", "066570.KS": "LG전자",
    "034730.KS": "SK", "011780.KS": "금호석유", "034220.KS": "LG디스플레이", "010130.KS": "고려아연",
    "000100.KS": "유한양행", "000720.KS": "현대건설", "017670.KS": "SK텔레콤", "011070.KS": "LG이노텍",
    "003670.KS": "포스코퓨처엠", "011200.KS": "HMM", "009150.KS": "삼성전기", "015760.KS": "한국전력"
}

def get_stock_name(ticker, info):
    if ticker in KR_NAME_MAP: return KR_NAME_MAP[ticker]
    return info.get('shortName', ticker)

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
    entry_price = FloatField() # 매수 권장가 (장 마감가)
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
    if market == "US":
        tickers = [
            "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "BRK-B", "V", "UNH", 
            "LLY", "JPM", "AVGO", "XOM", "MA", "JNJ", "PG", "COST", "HD", "ABBV", 
            "MRK", "ADBE", "CRM", "CVX", "NFLX", "AMD", "PEP", "TMO", "KO", "WMT",
            "MCD", "DIS", "CSCO", "INTC", "PFE", "ORCL", "BAC", "CMCSA", "VZ", "PLTR",
            "SNPS", "CDNS", "INTU", "ISRG", "GE", "NOW", "IBM", "CAT", "HON", "AMGN"
        ]
    else:
        tickers = [
            "005930.KS", "000660.KS", "035420.KS", "035720.KS", "005380.KS", "068270.KS",
            "005935.KS", "207940.KS", "051910.KS", "000270.KS", "006400.KS", "005490.KS",
            "032830.KS", "012330.KS", "010950.KS", "066570.KS", "034730.KS", "011780.KS",
            "034220.KS", "010130.KS", "000100.KS", "000720.KS", "017670.KS", "011070.KS",
            "003670.KS", "011200.KS", "009150.KS", "015760.KS", "033780.KS", "018260.KS"
        ]
    
    index_ticker = "^GSPC" if market == "US" else "^KS11"
    budget = MY_BUDGET_USD if market == "US" else MY_BUDGET_KRW
    currency = "$" if market == "US" else "원"
    
    try:
        data = yf.download(tickers, period="1y", group_by="ticker", threads=True, progress=False)
        
        results = []
        def analyze(ticker):
            try:
                hist = data[ticker].dropna(how='all')
                if hist.empty: return None
                close = hist['Close']
                info = yf.Ticker(ticker).info
                
                # 점수 계산 (추세 + 모멘텀)
                sma50 = close.rolling(window=50).mean().iloc[-1]
                sma200 = close.rolling(window=200).mean().iloc[-1]
                score = 50
                if close.iloc[-1] > sma50: score += 20
                if sma50 > sma200: score += 20
                if close.iloc[-1] > close.iloc[-5]: score += 10 # 최근 5일 상승
                
                qty, target, stop = calculate_strategy(ticker, close.iloc[-1], hist, budget)
                return {
                    "market": market, "ticker": ticker, "name": get_stock_name(ticker, info),
                    "entry_price": float(close.iloc[-1]), "current_price": float(close.iloc[-1]), "max_price": float(close.iloc[-1]),
                    "score": score, "reason": "AI 추세 분석 완료", "target_price": target, "stop_loss": stop, "quantity": qty
                }
            except: return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            scanned = [r for r in list(executor.map(analyze, tickers)) if r]
        
        # 무조건 점수 높은 순으로 상위 10개 추출
        scanned.sort(key=lambda x: x['score'], reverse=True)
        top_10 = scanned[:10]
        new_tickers = [s['ticker'] for s in top_10]

        with db.atomic():
            PortfolioItem.update(status="EXITED", exit_date=datetime.datetime.now(), exit_price=PortfolioItem.current_price).where((PortfolioItem.market == market) & (PortfolioItem.status == "ACTIVE") & (PortfolioItem.ticker << new_tickers == False)).execute()
            for s in top_10:
                item, created = PortfolioItem.get_or_create(market=market, ticker=s['ticker'], status="ACTIVE", defaults=s)
                if not created: item.current_price = s['entry_price']; item.save()
        
        # --- 리포트 발송 ---
        msg = f"📍 *[AI 가이드] 오늘의 {market} 핵심 전략*\n\n"
        for i in top_10:
            msg += f"✅ *{i['name']}* ({i['ticker']})\n"
            msg += f"   • 매수 권장가: `{i['entry_price']:,}{currency}` (±1.5% 진입)\n"
            msg += f"   • 권장 수량: `{i['quantity']}주`\n"
            msg += f"   • 익절가: `{i['target_price']:,}{currency}`\n"
            msg += f"   • 손절가: `{i['stop_loss']:,}{currency}`\n\n"
        send_telegram_message(msg)
        
    except Exception as e: print(f"Scan error: {e}")

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
            
            currency = "$" if active_market == "US" else "원"
            # 1. 매수 적기 알림 (권장가 돌파 시)
            if not item.buy_alert_sent and curr_price >= item.entry_price * 1.01:
                alert = f"🚀 *[매수 진입] {item.ticker} ({item.name})*\n"
                alert += f"매수 권장가 돌파! 현재가 `{curr_price:,.0f}{currency}`\n"
                alert += f"지금 바로 `{item.quantity}주` 진입을 권장합니다.\n"
                alert += f"익절: {item.target_price:,} | 손절: {item.stop_loss:,}"
                send_telegram_message(alert)
                item.buy_alert_sent = True; item.save()
            
            # 2. 익절/손절 알림 (동일)
            elif not item.target_alert_sent and curr_price >= item.target_price:
                send_telegram_message(f"💰 *[익절!] {item.ticker}* 목표가 도달! 현재가: {curr_price:,.0f}{currency}")
                item.target_alert_sent = True; item.save()
            elif not item.stop_alert_sent and curr_price <= item.stop_loss:
                send_telegram_message(f"🚨 *[손절!] {item.ticker}* 생명선 이탈! 현재가: {curr_price:,.0f}{currency}")
                item.stop_alert_sent = True; item.save()
        except: continue

scheduler = BackgroundScheduler(timezone="Asia/Seoul")
scheduler.add_job(run_full_market_scan, CronTrigger(hour=16, minute=5), args=['KR'])
scheduler.add_job(run_full_market_scan, CronTrigger(hour=6, minute=5), args=['US'])
scheduler.add_job(monitor_market_signals, 'interval', minutes=1)
scheduler.start()

# --- API ---
@app.get("/api/portfolio")
async def get_ai_portfolio(market: str = "US", show_history: bool = False):
    status = "EXITED" if show_history else "ACTIVE"
    items = PortfolioItem.select().where((PortfolioItem.market == market) & (PortfolioItem.status == status)).order_by(PortfolioItem.entry_date.desc())
    return [{
        "ticker": i.ticker, "name": i.name, "entryPrice": i.entry_price, "currentPrice": i.current_price,
        "currentYield": round(((i.current_price/i.entry_price)-1)*100, 2), "maxYield": round(((i.max_price/i.entry_price)-1)*100, 2),
        "targetPrice": i.target_price, "stopLoss": i.stop_loss, "quantity": i.quantity,
        "entryDate": i.entry_date.strftime('%Y-%m-%d'), "status": i.status
    } for i in items]

@app.post("/api/rebalance")
async def trigger_manual_rebalance(market: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_full_market_scan, market)
    return {"message": "Scanning started."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
