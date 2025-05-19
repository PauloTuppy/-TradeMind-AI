import { useState, useEffect } from 'react';
import CandlestickChart from './CandlestickChart';

const LiveTicker = () => {
  const [prices, setPrices] = useState([]);
  
  useEffect(() => {
    const ws = new WebSocket('wss://api.trademind.ai/prices');
    ws.onmessage = (e) => setPrices(JSON.parse(e.data));
    return () => ws.close();
  }, []);
  
  return <CandlestickChart data={prices} />;
};

export default LiveTicker;
