import { RateLimiter, CircuitBreaker } from './circuit';
import axios from 'axios';
import WebSocket from 'ws';
import type { WebSocket as WSType } from 'ws';
import { Observable, Subject } from 'rxjs';
import { v4 as uuidv4 } from 'uuid';
import * as crypto from 'crypto';
import { Redis } from 'ioredis';

// Configure Redis client
const redis = new Redis(process.env.REDIS_URL || 'redis://localhost:6379');

// Types
export interface TradeOrder {
  id?: string;
  userId: string;
  exchange: string;
  pair: string;
  side: 'BUY' | 'SELL';
  type: 'MARKET' | 'LIMIT' | 'STOP' | 'ICEBERG';
  quantity: number;
  price?: number;
  stopPrice?: number;
  icebergQty?: number;
  timeInForce?: 'GTC' | 'IOC' | 'FOK';
  clientOrderId?: string;
  timestamp?: number;
}

export interface ExecutionResult {
  orderId: string;
  clientOrderId: string;
  pair: string;
  executedPrice: number;
  executedQuantity: number;
  fee: number;
  feeCurrency: string;
  timestamp: number;
  status: 'FILLED' | 'PARTIALLY_FILLED' | 'REJECTED' | 'CANCELED';
  trades?: Array<{
    id: string;
    price: number;
    quantity: number;
    fee: number;
    timestamp: number;
  }>;
}

export interface OrderBook {
  exchange: string;
  pair: string;
  timestamp: number;
  bids: Array<[number, number]>; // [price, quantity]
  asks: Array<[number, number]>; // [price, quantity]
}

export interface LiquidityAnalysis {
  pair: string;
  timestamp: number;
  bidDepth: number;
  askDepth: number;
  spreadPercentage: number;
  slippageMap: {
    '0.1%': number;
    '0.5%': number;
    '1%': number;
    '2%': number;
    '5%': number;
  };
  score: number; // 0-100 liquidity score
  recommendation: {
    maxOrderSize: number;
    shouldSplit: boolean;
    optimalChunkSize?: number;
  };
}

export interface ExchangeInfo {
  name: string;
  status: 'ONLINE' | 'DEGRADED' | 'OFFLINE';
  tradingFees: {
    maker: number;
    taker: number;
  };
  withdrawalFees: Record<string, number>;
  minOrderSize: Record<string, number>;
  precision: Record<string, {
    price: number;
    quantity: number;
  }>;
  rateLimit: {
    requests: number;
    period: number; // in milliseconds
  };
}

// Exchange Adapter Interface
export interface ExchangeAdapter {
  getName(): string;
  getInfo(): Promise<ExchangeInfo>;
  executeOrder(order: TradeOrder): Promise<ExecutionResult>;
  cancelOrder(orderId: string, pair: string): Promise<boolean>;
  getOrderStatus(orderId: string, pair: string): Promise<ExecutionResult>;
  getOpenOrders(userId: string): Promise<ExecutionResult[]>;
  getOrderBook(pair: string, depth?: number): Promise<OrderBook>;
  streamOrderBook(pair: string): Observable<OrderBook>;
  getLiquidity(pair: string): Promise<LiquidityAnalysis>;
  getBalance(userId: string): Promise<Record<string, number>>;
}

// Base Exchange Adapter
export abstract class BaseExchangeAdapter implements ExchangeAdapter {
  protected apiKey: string;
  protected apiSecret: string;
  protected baseUrl: string;
  protected wsUrl: string;
  protected orderBookStreams: Map<string, Subject<OrderBook>> = new Map();
  protected wsConnections: Map<string, WebSocket> = new Map();
  protected rateLimiter: RateLimiter;
  protected circuitBreaker: CircuitBreaker;
  
  constructor(
    apiKey: string,
    apiSecret: string,
    baseUrl: string,
    wsUrl: string
  ) {
    this.apiKey = apiKey;
    this.apiSecret = apiSecret;
    this.baseUrl = baseUrl;
    this.wsUrl = wsUrl;
    this.rateLimiter = new RateLimiter(10, 1000); // 10 requests per second
    this.circuitBreaker = new CircuitBreaker(5, 30000); // 5 failures, 30s reset
  }
  
  abstract getName(): string;
  abstract getInfo(): Promise<ExchangeInfo>;
  abstract executeOrder(order: TradeOrder): Promise<ExecutionResult>;
  abstract cancelOrder(orderId: string, pair: string): Promise<boolean>;
  abstract getOrderStatus(orderId: string, pair: string): Promise<ExecutionResult>;
  abstract getOpenOrders(userId: string): Promise<ExecutionResult[]>;
  abstract getOrderBook(pair: string, depth?: number): Promise<OrderBook>;
  abstract getLiquidity(pair: string): Promise<LiquidityAnalysis>;
  abstract getBalance(userId: string): Promise<Record<string, number>>;
  
  streamOrderBook(pair: string): Observable<OrderBook> {
    if (!this.orderBookStreams.has(pair)) {
      const subject = new Subject<OrderBook>();
      this.orderBookStreams.set(pair, subject);
      this.initOrderBookStream(pair);
    }
    return this.orderBookStreams.get(pair)!.asObservable();
  }
  
  protected abstract initOrderBookStream(pair: string): void;
  
  protected async executeWithRetry<T>(
    fn: () => Promise<T>,
    maxRetries = 3,
    initialDelay = 1000
  ): Promise<T> {
    let retries = 0;
    let delay = initialDelay;
    
    while (true) {
      try {
        // Check circuit breaker
        if (this.circuitBreaker.isOpen()) {
          throw new Error(`Circuit breaker open for ${this.getName()}`);
        }
        
        // Wait for rate limiter
        await this.rateLimiter.acquire();
        
        // Execute function
        const result = await fn();
        
        // Reset circuit breaker on success
        this.circuitBreaker.onSuccess();
        
        return result;
      } catch (error) {
        // Record failure in circuit breaker
        this.circuitBreaker.onFailure();
        
        retries++;
        if (retries > maxRetries) {
          console.error(`Max retries exceeded for ${this.getName()}: ${error}`);
          throw error;
        }
        
        // Exponential backoff
        await new Promise(resolve => setTimeout(resolve, delay));
        delay *= 2;
        
        console.warn(`Retrying operation on ${this.getName()}, attempt ${retries}`);
      }
    }
  }
  
  protected calculateAdjustedQuantity(order: TradeOrder): number {
    // Implement quantity adjustment logic based on exchange rules
    return order.quantity;
  }
  
  protected calculateIcebergQty(order: TradeOrder): number | undefined {
    if (order.type !== 'ICEBERG') return undefined;
    
    // Default to 10% of total quantity if not specified
    return order.icebergQty || order.quantity * 0.1;
  }
  
  protected logOrder(order: TradeOrder, result: ExecutionResult): void {
    // Log order to database for audit trail
    console.log(`Order executed: ${order.pair} ${order.side} ${order.quantity} @ ${result.executedPrice}`);
    
    // Store in Redis for quick access
    redis.hset(
      `orders:${order.userId}`,
      result.orderId,
      JSON.stringify({
        order,
        result,
        timestamp: Date.now()
      })
    );
    
    // Set TTL for Redis cache
    redis.expire(`orders:${order.userId}`, 86400); // 24 hours
  }
}

// Binance Exchange Adapter
export class BinanceAdapter extends BaseExchangeAdapter {
  constructor(apiKey: string, apiSecret: string) {
    super(
      apiKey,
      apiSecret,
      'https://api.binance.com',
      'wss://stream.binance.com:9443/ws'
    );
  }
  
  getName(): string {
    return 'binance';
  }
  
  async getInfo(): Promise<ExchangeInfo> {
    return this.executeWithRetry(async () => {
      const response = await axios.get(`${this.baseUrl}/api/v3/exchangeInfo`);
      
      // Extract relevant information
      return {
        name: 'Binance',
        status: 'ONLINE',
        tradingFees: {
          maker: 0.001, // 0.1%
          taker: 0.001  // 0.1%
        },
        withdrawalFees: {
          'BTC': 0.0005,
          'ETH': 0.005,
          'USDT': 1
        },
        minOrderSize: {
          'BTCUSDT': 0.0001,
          'ETHUSDT': 0.001
        },
        precision: {
          'BTCUSDT': {
            price: 2,
            quantity: 6
          },
          'ETHUSDT': {
            price: 2,
            quantity: 5
          }
        },
        rateLimit: {
          requests: 1200,
          period: 60000 // 1 minute
        }
      };
    });
  }
  
  async executeOrder(order: TradeOrder): Promise<ExecutionResult> {
    return this.executeWithRetry(async () => {
      // Generate client order ID if not provided
      const clientOrderId = order.clientOrderId || `${order.userId}-${uuidv4()}`;
      
      // Prepare request parameters
      const params = new URLSearchParams();
      params.append('symbol', order.pair);
      params.append('side', order.side);
      params.append('type', order.type);
      params.append('quantity', this.calculateAdjustedQuantity(order).toString());
      params.append('newClientOrderId', clientOrderId);
      
      if (order.price) {
        params.append('price', order.price.toString());
      }
      
      if (order.stopPrice) {
        params.append('stopPrice', order.stopPrice.toString());
      }
      
      if (order.icebergQty) {
        params.append('icebergQty', this.calculateIcebergQty(order)!.toString());
      }
      
      if (order.timeInForce) {
        params.append('timeInForce', order.timeInForce);
      }
      
      // Add timestamp
      const timestamp = Date.now();
      params.append('timestamp', timestamp.toString());
      
      // Generate signature
      const signature = crypto
        .createHmac('sha256', this.apiSecret)
        .update(params.toString())
        .digest('hex');
      
      params.append('signature', signature);
      
      // Execute order
      const response = await axios.post(
        `${this.baseUrl}/api/v3/order`,
        params.toString(),
        {
          headers: {
            'X-MBX-APIKEY': this.apiKey,
            'Content-Type': 'application/x-www-form-urlencoded'
          }
        }
      );
      
      // Transform response to ExecutionResult
      const result: ExecutionResult = {
        orderId: response.data.orderId,
        clientOrderId: response.data.clientOrderId,
        pair: order.pair,
        executedPrice: parseFloat(response.data.price),
        executedQuantity: parseFloat(response.data.executedQty),
        fee: parseFloat(response.data.fills?.[0]?.commission || '0'),
        feeCurrency: response.data.fills?.[0]?.commissionAsset || '',
        timestamp: response.data.transactTime,
        status: response.data.status as 'FILLED' | 'PARTIALLY_FILLED' | 'REJECTED' | 'CANCELED',
        trades: response.data.fills?.map((fill: any) => ({
          id: fill.tradeId,
          price: parseFloat(fill.price),
          quantity: parseFloat(fill.qty),
          fee: parseFloat(fill.commission),
          timestamp: response.data.transactTime
        }))
      };
      
      // Log order
      this.logOrder(order, result);
      
      return result;
    });
  }
  
  async cancelOrder(orderId: string, pair: string): Promise<boolean> {
    return this.executeWithRetry(async () => {
      // Prepare request parameters
      const params = new URLSearchParams();
      params.append('symbol', pair);
      params.append('orderId', orderId);
      
      // Add timestamp
      const timestamp = Date.now();
      params.append('timestamp', timestamp.toString());
      
      // Generate signature
      const signature = crypto
        .createHmac('sha256', this.apiSecret)
        .update(params.toString())
        .digest('hex');
      
      params.append('signature', signature);
      
      // Cancel order
      const response = await axios.delete(
        `${this.baseUrl}/api/v3/order?${params.toString()}`,
        {
          headers: {
            'X-MBX-APIKEY': this.apiKey
          }
        }
      );
      
      return response.data.status === 'CANCELED';
    });
  }
  
  async getOrderStatus(orderId: string, pair: string): Promise<ExecutionResult> {
    return this.executeWithRetry(async () => {
      // Prepare request parameters
      const params = new URLSearchParams();
      params.append('symbol', pair);
      params.append('orderId', orderId);
      
      // Add timestamp
      const timestamp = Date.now();
      params.append('timestamp', timestamp.toString());
      
      // Generate signature
      const signature = crypto
        .createHmac('sha256', this.apiSecret)
        .update(params.toString())
        .digest('hex');
      
      params.append('signature', signature);
      
      // Get order status
      const response = await axios.get(
        `${this.baseUrl}/api/v3/order?${params.toString()}`,
        {
          headers: {
            'X-MBX-APIKEY': this.apiKey
          }
        }
      );
      
      // Transform response to ExecutionResult
      return {
        orderId: response.data.orderId,
        clientOrderId: response.data.clientOrderId,
        pair: response.data.symbol,
        executedPrice: parseFloat(response.data.price),
        executedQuantity: parseFloat(response.data.executedQty),
        fee: 0, // Not available in order status
        feeCurrency: '',
        timestamp: response.data.time,
        status: response.data.status as 'FILLED' | 'PARTIALLY_FILLED' | 'REJECTED' | 'CANCELED'
      };
    });
  }
  
  async getOpenOrders(userId: string): Promise<ExecutionResult[]> {
    return this.executeWithRetry(async () => {
      // Prepare request parameters
      const params = new URLSearchParams();
      
      // Add timestamp
      const timestamp = Date.now();
      params.append('timestamp', timestamp.toString());
      
      // Generate signature
      const signature = crypto
        .createHmac('sha256', this.apiSecret)
        .update(params.toString())
        .digest('hex');
      
      params.append('signature', signature);
      
      // Get open orders
      const response = await axios.get(
        `${this.baseUrl}/api/v3/openOrders?${params.toString()}`,
        {
          headers: {
            'X-MBX-APIKEY': this.apiKey
          }
        }
      );
      
      // Transform response to ExecutionResult[]
      return response.data.map((order: any) => ({
        orderId: order.orderId,
        clientOrderId: order.clientOrderId,
        pair: order.symbol,
        executedPrice: parseFloat(order.price),
        executedQuantity: parseFloat(order.executedQty),
        fee: 0, // Not available in open orders
        feeCurrency: '',
        timestamp: order.time,
        status: order.status as 'FILLED' | 'PARTIALLY_FILLED' | 'REJECTED' | 'CANCELED'
      }));
    });
  }
  
  async getOrderBook(pair: string, depth = 100): Promise<OrderBook> {
    return this.executeWithRetry(async () => {
      // Get order book
      const response = await axios.get(
        `${this.baseUrl}/api/v3/depth?symbol=${pair}&limit=${depth}`
      );
      
      // Transform response to OrderBook
      return {
        exchange: this.getName(),
        pair,
        timestamp: Date.now(),
        bids: response.data.bids.map((bid: string[]) => [parseFloat(bid[0]), parseFloat(bid[1])]),
        asks: response.data.asks.map((ask: string[]) => [parseFloat(ask[0]), parseFloat(ask[1])])
      };
    });
  }
  
  async getLiquidity(pair: string): Promise<LiquidityAnalysis> {
    return this.executeWithRetry(async () => {
      // Get order book
      const orderBook = await this.getOrderBook(pair, 1000);
      
      // Calculate bid and ask depth
      const bidDepth = orderBook.bids.reduce((sum, [price, quantity]) => sum + price * quantity, 0);
      const askDepth = orderBook.asks.reduce((sum, [price, quantity]) => sum + price * quantity, 0);
      
      // Calculate spread
      const bestBid = orderBook.bids[0][0];
      const bestAsk = orderBook.asks[0][0];
      const spreadPercentage = ((bestAsk - bestBid) / bestBid) * 100;
      
      // Calculate slippage for different order sizes
      const slippageMap = this.calculateSlippageMap(orderBook);
      
      // Calculate liquidity score (0-100)
      const score = this.calculateLiquidityScore(bidDepth, askDepth, spreadPercentage, slippageMap);
      
      // Calculate optimal order size
      const maxOrderSize = this.calculateMaxOrderSize(orderBook, 0.5); // 0.5% max slippage
      
      return {
        pair,
        timestamp: orderBook.timestamp,
        bidDepth,
        askDepth,
        spreadPercentage,
        slippageMap,
        score,
        recommendation: {
          maxOrderSize,
          shouldSplit: maxOrderSize > 1000, // Arbitrary threshold
          optimalChunkSize: maxOrderSize > 1000 ? maxOrderSize / 3 : undefined
        }
      };
    });
  }
  
  async getBalance(userId: string): Promise<Record<string, number>> {
    return this.executeWithRetry(async () => {
      // Prepare request parameters
      const params = new URLSearchParams();
      
      // Add timestamp
      const timestamp = Date.now();
      params.append('timestamp', timestamp.toString());
      
      // Generate signature
      const signature = crypto
        .createHmac('sha256', this.apiSecret)
        .update(params.toString())
        .digest('hex');
      
      params.append('signature', signature);
      
      // Get account information
      const response = await axios.get(
        `${this.baseUrl}/api/v3/account?${params.toString()}`,
        {
          headers: {
            'X-MBX-APIKEY': this.apiKey
          }
        }
      );
      
      // Transform response to balance record
      const balances: Record<string, number> = {};
      response.data.balances.forEach((balance: any) => {
        balances[balance.asset] = parseFloat(balance.free);
      });
      return balances;
    });
  }

  protected initOrderBookStream(pair: string): void {
    const streamName = `${pair.toLowerCase()}@depth`;
    const wsUrl = `${this.wsUrl}/${streamName}`;
    
    const ws = new WebSocket(wsUrl);
    this.wsConnections.set(pair, ws);
    
    ws.on('open', () => {
      console.log(`Order book stream connected for ${pair}`);
    });
    
    ws.on('message', (data: string) => {
      try {
        const message = JSON.parse(data);
        const subject = this.orderBookStreams.get(pair);
        
        if (subject) {
          const orderBook: OrderBook = {
            exchange: this.getName(),
            pair,
            timestamp: Date.now(),
            bids: message.bids.map((bid: string[]) => [parseFloat(bid[0]), parseFloat(bid[1])]),
            asks: message.asks.map((ask: string[]) => [parseFloat(ask[0]), parseFloat(ask[1])])
          };
          subject.next(orderBook);
        }
      } catch (error) {
        console.error(`Error processing order book update for ${pair}:`, error);
      }
    });
    
    ws.on('error', (error) => {
      console.error(`Order book stream error for ${pair}:`, error);
    });
    
    ws.on('close', () => {
      console.log(`Order book stream closed for ${pair}`);
      this.wsConnections.delete(pair);
    });
  }

  private calculateSlippageMap(orderBook: OrderBook): {
    '0.1%': number;
    '0.5%': number;
    '1%': number;
    '2%': number;
    '5%': number;
  } {
    const midPrice = (orderBook.bids[0][0] + orderBook.asks[0][0]) / 2;

    return {
      '0.1%': this.calculateSlippage(orderBook, midPrice * 0.001),
      '0.5%': this.calculateSlippage(orderBook, midPrice * 0.005),
      '1%': this.calculateSlippage(orderBook, midPrice * 0.01),
      '2%': this.calculateSlippage(orderBook, midPrice * 0.02),
      '5%': this.calculateSlippage(orderBook, midPrice * 0.05)
    };
  }

  private calculateSlippage(orderBook: OrderBook, targetSlippage: number): number {
    // Calculate order size that would cause target slippage
    let cumulativeValue = 0;
    let cumulativeSize = 0;
    
    for (const [price, qty] of orderBook.bids) {
      const value = price * qty;
      if (cumulativeValue + value > targetSlippage) {
        const remaining = targetSlippage - cumulativeValue;
        cumulativeSize += remaining / price;
        break;
      }
      cumulativeValue += value;
      cumulativeSize += qty;
    }
    
    return cumulativeSize;
  }

  private calculateLiquidityScore(
    bidDepth: number,
    askDepth: number,
    spreadPercentage: number,
    slippageMap: Record<string, number>
  ): number {
    // Score based on depth (50%), spread (30%), and slippage (20%)
    const depthScore = Math.min(100, (bidDepth + askDepth) / 1000);
    const spreadScore = Math.max(0, 100 - (spreadPercentage * 10));
    const slippageScore = Math.min(100, slippageMap['1%'] * 10);

    return (depthScore * 0.5) + (spreadScore * 0.3) + (slippageScore * 0.2);
  }

  private calculateMaxOrderSize(orderBook: OrderBook, maxSlippagePct: number): number {
    const midPrice = (orderBook.bids[0][0] + orderBook.asks[0][0]) / 2;
    const maxSlippage = midPrice * maxSlippagePct;
    return this.calculateSlippage(orderBook, maxSlippage);
  }
}
