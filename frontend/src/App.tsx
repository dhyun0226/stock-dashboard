import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { createChart, IChartApi, ColorType } from 'lightweight-charts';
import './App.css';

const API_BASE_URL = 'http://localhost:8000/api';

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
  const [loading, setLoading] = useState(false);
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    fetchPortfolio();
    const interval = setInterval(fetchPortfolio, 60000); // 1분마다 자동 갱신
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

  const renderChart = async () => {
    if (!chartContainerRef.current) return;
    if (chartRef.current) chartRef.current.remove();

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
        <div className="brand">STOCK<span>INSIGHT</span> AI</div>
        <div className="market-switches">
          <button className={market === 'US' ? 'active' : ''} onClick={() => setMarket('US')}>US MARKET</button>
          <button className={market === 'KR' ? 'active' : ''} onClick={() => setMarket('KR')}>KR MARKET</button>
        </div>
      </header>

      <main className="main-layout">
        <section className="portfolio-section card">
          <div className="section-header">
            <h3>AI Active Portfolio (Top 10)</h3>
            <span className="live-badge">LIVE MONITORING</span>
          </div>
          <div className="portfolio-table-wrapper">
            <table className="portfolio-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Entry Price</th>
                  <th>Current</th>
                  <th>Yield (Max)</th>
                  <th>Target (Stop)</th>
                  <th>Qty</th>
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
                      <div className="max-yield">Max: {item.maxYield}%</div>
                    </td>
                    <td>
                      <div className="target">T: {item.targetPrice.toLocaleString()}</div>
                      <div className="stop">S: {item.stopLoss.toLocaleString()}</div>
                    </td>
                    <td>{item.quantity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="chart-section card">
          <div className="chart-header">
            <h2>{ticker} Real-time Analysis</h2>
          </div>
          <div ref={chartContainerRef} className="chart-box"></div>
        </section>
      </main>
    </div>
  );
}

export default App;
