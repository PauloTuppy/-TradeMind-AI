import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
import redis
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class DataNormalizer:
    def __init__(self, redis_url):
        """
        Initialize the data normalizer with Redis connection
        
        Args:
            redis_url: URL for Redis connection
        """
        self.redis_client = redis.from_url(redis_url)
        self.scalers = {}
        self.feature_store = FeatureStore(redis_url)
        
    def process_market_data(self, market_data):
        """
        Process incoming market data and store normalized features
        
        Args:
            market_data: Dictionary containing market data
        """
        try:
            symbol = market_data.get('symbol')
            if not symbol:
                logger.warning("Received market data without symbol")
                return
                
            # Extract price and timestamp
            price = float(market_data.get('price', 0))
            timestamp = market_data.get('timestamp', datetime.now().timestamp())
            
            # Get or create scaler for this symbol
            if symbol not in self.scalers:
                self.scalers[symbol] = MinMaxScaler(feature_range=(0, 1))
                
            # Normalize price
            normalized_price = self.scalers[symbol].fit_transform([[price]])[0][0]
            
            # Create feature vector
            features = {
                'price': price,
                'normalized_price': normalized_price,
                'timestamp': timestamp
            }
            
            # Add technical indicators if we have enough data
            historical_data = self.get_historical_data(symbol, limit=100)
            if len(historical_data) > 20:
                df = pd.DataFrame(historical_data)
                
                # Add technical indicators
                features.update({
                    'rsi_14': self.calculate_rsi(df['price'], period=14),
                    'ma_20': self.calculate_moving_average(df['price'], period=20),
                    'ma_50': self.calculate_moving_average(df['price'], period=50),
                    'volatility': self.calculate_volatility(df['price'], period=20)
                })
            
            # Store features
            self.feature_store.store_features(symbol, features)
            
            return features
            
        except Exception as e:
            logger.error(f"Error processing market data: {e}")
            return None
    
    def get_historical_data(self, symbol, limit=100):
        """Get historical data from Redis"""
        try:
            data = self.redis_client.lrange(f"historical:{symbol}", 0, limit-1)
            return [json.loads(item) for item in data]
        except Exception as e:
            logger.error(f"Error fetching historical data: {e}")
            return []
    
    def calculate_rsi(self, prices, period=14):
        """Calculate Relative Strength Index"""
        try:
            delta = prices.diff()
            gain = delta.where(delta > 0, 0).rolling(window=period).mean()
            loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
            
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            
            return rsi.iloc[-1]
        except:
            return 50  # Default value if calculation fails
    
    def calculate_moving_average(self, prices, period=20):
        """Calculate Moving Average"""
        try:
            return prices.rolling(window=period).mean().iloc[-1]
        except:
            return prices.iloc[-1] if not prices.empty else 0
    
    def calculate_volatility(self, prices, period=20):
        """Calculate price volatility"""
        try:
            return prices.rolling(window=period).std().iloc[-1] / prices.iloc[-1]
        except:
            return 0

class FeatureStore:
    def __init__(self, redis_url):
        """
        Initialize the feature store with Redis connection
        
        Args:
            redis_url: URL for Redis connection
        """
        self.redis_client = redis.from_url(redis_url)
        self.ttl = 86400  # 24 hours
        
    def store_features(self, symbol, features):
        """
        Store features in Redis
        
        Args:
            symbol: Trading pair symbol
            features: Dictionary of features
        """
        try:
            # Store current features
            key = f"features:{symbol}:current"
            self.redis_client.set(key, json.dumps(features))
            
            # Add to historical features list
            hist_key = f"features:{symbol}:history"
            self.redis_client.lpush(hist_key, json.dumps(features))
            self.redis_client.ltrim(hist_key, 0, 999)  # Keep last 1000 entries
            
            # Set TTL
            self.redis_client.expire(key, self.ttl)
            self.redis_client.expire(hist_key, self.ttl)
            
            return True
        except Exception as e:
            logger.error(f"Error storing features: {e}")
            return False
    
    def get_features(self, symbol, limit=100):
        """
        Get features from Redis
        
        Args:
            symbol: Trading pair symbol
            limit: Number of historical entries to retrieve
            
        Returns:
            Dictionary with current and historical features
        """
        try:
            # Get current features
            current_key = f"features:{symbol}:current"
            current = self.redis_client.get(current_key)
            
            # Get historical features
            hist_key = f"features:{symbol}:history"
            history = self.redis_client.lrange(hist_key, 0, limit-1)
            
            return {
                'current': json.loads(current) if current else None,
                'history': [json.loads(item) for item in history]
            }
        except Exception as e:
            logger.error(f"Error retrieving features: {e}")
            return {'current': None, 'history': []}