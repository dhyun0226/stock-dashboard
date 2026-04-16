import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { createChart, type IChartApi, ColorType } from 'lightweight-charts';
import './App.css';

const API_BASE_URL = 'http://134.185.114.170:8000/api';

interface PortfolioItem {
  ticker: string;
  name: string;
  entryPrice: number;
  currentPrice: number;
  currentYield: number;
  maxYield: number;
  targetPrice: number;
  stopLoss: number;
  quantity: number;
  entryDate: string;
}
function App() {
  const [market, setMarket] = useState<'US' | 'KR'>('US');
  const [ticker, setTicker] = useState('AAPL');
  const [portfolio, setPortfolio] = useState<PortfolioItem[]>([]);
  const [scanStatus, setScanStatus] = useState({ status: 'IDLE', percent: 0 });
  const [loading, setLoading] = useState(false);
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    fetchPortfolio();
    fetchScanStatus();
    const interval = setInterval(() => {
      fetchPortfolio();
      fetchScanStatus();
    }, 5000); // 5초마다 갱신
    return () => clearInterval(interval);
  }, [market]);

  useEffect(() => {
    renderChart();
  }, [ticker]);

  const fetchPortfolio = async () => {
    try {
      const res = await axios.get(`${API_BASE_URL}/portfolio?market=${market}`);
      setPortfolio(res.data);
      if (res.data.length > 0 && !ticker) setTicker(res.data[0].ticker);
    } catch (err) { console.error(err); }
  };

  const fetchScanStatus = async () => {
    try {
      const res = await axios.get(`${API_BASE_URL}/scan-status?market=${market}`);
      setScanStatus(res.data);
    } catch (err) { console.error(err); }
  };

  const renderChart = async () => {
...
  return (
    <div className="app-container">
      {scanStatus.status !== 'IDLE' && (
        <div className="progress-overlay">
          <div className="progress-card">
            <h4>{market === 'KR' ? '한국' : '미국'} 전 종목 AI 스캔 중...</h4>
            <div className="progress-bar-container">
              <div className="progress-bar-fill" style={{ width: `${scanStatus.percent}%` }}></div>
            </div>
            <div className="progress-text">{scanStatus.percent}% 완료</div>
          </div>
        </div>
      )}

      <header className="main-header">

    const chart = createChart(chartContainerRef.current, {
      layout: { background: { type: ColorType.Solid, color: '#0f1117' }, textColor: '#d1d4dc' },
      grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
      width: chartContainerRef.current.clientWidth,
      height: 450,
    });

    const series = chart.addCandlestickSeries({ upColor: '#26a69a', downColor: '#ef5350' });
    const res = await axios.get(`${API_BASE_URL}/stocks/${ticker}/history`);
    series.setData(res.data);
    chartRef.current = chart;
  };

  return (
    <div className="app-container">
      <header className="main-header">
        <div className="brand">주식<span>인사이트</span> AI</div>
        <div className="market-switches">
          <button className={market === 'US' ? 'active' : ''} onClick={() => setMarket('US')}>미국 시장</button>
          <button className={market === 'KR' ? 'active' : ''} onClick={() => setMarket('KR')}>한국 시장</button>
        </div>
      </header>

      <main className="main-layout">
        <section className="portfolio-section card">
          <div className="section-header">
            <h3>AI 액티브 포트폴리오 (상위 10선)</h3>
            <span className="live-badge">실시간 모니터링</span>
          </div>
          <div className="portfolio-table-wrapper">
            <table className="portfolio-table">
              <thead>
                <tr>
                  <th>종목</th>
                  <th>매수 권장가</th>
                  <th>현재가</th>
                  <th>수익률 (최고)</th>
                  <th>목표(손절)</th>
                  <th>수량</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.map(item => (
                  <tr key={item.ticker} onClick={() => setTicker(item.ticker)} className={ticker === item.ticker ? 'active' : ''}>
                    <td>
                      <div className="t-name">{item.name}</div>
                      <div className="t-code">{item.ticker.split('.')[0]}</div>
                    </td>
                    <td>{item.entryPrice.toLocaleString()}</td>
                    <td className={item.currentYield >= 0 ? 'up' : 'down'}>{item.currentPrice.toLocaleString()}</td>
                    <td>
                      <div className={`yield ${item.currentYield >= 0 ? 'up' : 'down'}`}>{item.currentYield}%</div>
                      <div className="max-yield">최고: {item.maxYield}%</div>
                    </td>
                    <td>
                      <div className="target">목표: {item.targetPrice.toLocaleString()}</div>
                      <div className="stop">손절: {item.stopLoss.toLocaleString()}</div>
                    </td>
                    <td>{item.quantity}주</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="chart-section card">
          <div className="chart-header">
            <h2>{ticker} 실시간 AI 분석 차트</h2>
          </div>
          <div ref={chartContainerRef} className="chart-box"></div>
        </section>
      </main>
    </div>
  );
}

export default App;
