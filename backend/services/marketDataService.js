const redis = require('redis');
const { promisify } = require('util');
const axios = require('axios');

class MarketDataService {
  constructor() {
    this.redisClient = redis.createClient(process.env.REDIS_URL);
    this.redisPublish = promisify(this.redisClient.publish).bind(this.redisClient);
    this.exchangeApis = {
      binance: axios.create({
        baseURL: 'https://api.binance.com/api/v3',
        timeout: 5000
      })
    };
    
    // Initialize data polling
    this.startDataPolling();
  }
  
  async startDataPolling() {
    // Poll for price updates every 5 seconds
    setInterval(async () => {
      try {
        await this.fetchAndPublishPrices();
      } catch (error) {
        console.error('Error polling price data:', error);
      }
    }, 5000);
    
    // Poll for order book updates every 2 seconds
    setInterval(async () => {
      try {
        await this.fetchAndPublishOrderBooks();
      } catch (error) {
        console.error('Error polling order book data:', error);
      }
    }, 2000);
  }
  
  async fetchAndPublishPrices() {
    // Top trading pairs to monitor
    const pairs = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT'];
    
    for (const pair of pairs) {
      try {
        // Fetch current price from Binance
        const response = await this.exchangeApis.binance.get('/ticker/price', {
          params: { symbol: pair }
        });
        
        const priceData = {
          symbol: pair,
          price: parseFloat(response.data.price),
          timestamp: Date.now()
        };
        
        // Publish to Redis
        await this.redisPublish('crypto-prices', JSON.stringify(priceData));
      } catch (error) {
        console.error(`Error fetching price for ${pair}:`, error);
      }
    }
  }
  
  async fetchAndPublishOrderBooks() {
    // Top trading pairs to monitor
    const pairs = ['BTCUSDT', 'ETHUSDT'];
    
    for (const pair of pairs) {
      try {
        // Fetch order book from Binance (limited depth for performance)
        const response = await this.exchangeApis.binance.get('/depth', {
          params: { symbol: pair, limit: 10 }
        });
        
        const orderBookData = {
          symbol: pair,
          bids: response.data.bids,
          asks: response.data.asks,
          timestamp: Date.now()
        };
        
        // Publish to Redis
        await this.redisPublish('order-book-updates', JSON.stringify(orderBookData));
      } catch (error) {
        console.error(`Error fetching order book for ${pair}:`, error);
      }
    }
  }
  
  // Method to publish custom alerts to user-specific channels
  async publishAlert(userId, alertData) {
    try {
      await this.redisPublish(`user-${userId}-alerts`, JSON.stringify(alertData));
      return true;
    } catch (error) {
      console.error(`Error publishing alert for user ${userId}:`, error);
      return false;
    }
  }
}

module.exports = new MarketDataService();