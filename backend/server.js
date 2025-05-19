const express = require('express');
const jwt = require('jsonwebtoken');
const { createBinanceClient } = require('./exchanges/binance');

const app = express();
const PORT = process.env.PORT || 3001;
const binanceClient = createBinanceClient();

// Market data endpoint
app.get('/api/market-data', async (req, res) => {
  try {
    const data = await binanceClient.fetchOHLCV('BTC/USDT', '1h');
    res.json(transformData(data));
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.listen(PORT, () => console.log(`Server running on port ${PORT}`));