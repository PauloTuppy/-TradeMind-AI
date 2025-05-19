import logging
import numpy as np
import redis
import json
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class FallbackHandler:
    def __init__(self, redis_url):
        """
        Initialize fallback handler
        
        Args:
            redis_url: URL for Redis connection
        """
        self.redis_client = redis.from_url(redis_url)
        self.ttl = 86400 * 7  # 7 days
    
    def store_prediction(self, symbol, timeframe, prediction):
        """
        Store prediction for fallback use
        
        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe for prediction
            prediction: Prediction data
            
        Returns:
            True if successful, False otherwise
        """
        try:
            key = f"fallback:predictions:{symbol}:{timeframe}"
            
            # Add timestamp to prediction
            prediction_with_ts = prediction.copy()
            prediction_with_ts['stored_at'] = datetime.now().timestamp()
            
            # Store in Redis
            self.redis_client.set(key, json.dumps(prediction_with_ts))
            self.redis_client.expire(key, self.ttl)
            
            return True
        except Exception as e:
            logger.error(f"Error storing fallback prediction: {e}")
            return False
    
    def get_fallback_prediction(self, symbol, timeframe):
        """
        Get fallback prediction
        
        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe for prediction
            
        Returns:
            Fallback prediction or None if not available
        """
        try:
            key = f"fallback:predictions:{symbol}:{timeframe}"
            data = self.redis_client.get(key)
            
            if not data:
                return None
                
            prediction = json.loads(data)
            
            # Check if prediction is too old (more than 24 hours)
            stored_at = prediction.get('stored_at', 0)
            if datetime.now().timestamp() - stored_at > 86400:
                logger.warning(f"Fallback prediction for {symbol} is too old")
                return None
            
            # Add fallback flag
            prediction['is_fallback'] = True
            
            return prediction
        except Exception as e:
            logger.error(f"Error getting fallback prediction: {e}")
            return None
    
    def generate_simple_prediction(self, symbol, historical_data):
        """
        Generate simple prediction when all else fails
        
        Args:
            symbol: Trading pair symbol
            historical_data: Historical price data
            
        Returns:
            Simple prediction
        """
        try:
            # Extract prices
            prices = [float(p) for p in historical_data]
            
            if not prices:
                return None
                
            # Calculate simple moving average
            last_price = prices[-1]
            avg_5 = np.mean(prices[-5:]) if len(prices) >= 5 else last_price
            avg_20 = np.mean(prices[-20:]) if len(prices) >= 20 else last_price
            
            # Calculate simple trend
            trend = (avg_5 / avg_20) - 1  # Percentage change
            
            # Generate prediction for next 24 hours
            predictions = []
            for i in range(24):
                # Simple linear projection with some noise
                pred = last_price * (1 + trend * (i+1)/24)
                # Add small random noise
                pred *= (1 + np.random.normal(0, 0.001))
                predictions.append(float(pred))
            
            return {
                'symbol': symbol,
                'timestamp': datetime.now().timestamp(),
                'predictions': {
                    'price': predictions,
                    'models': {
                        'simple': predictions
                    }
                },
                'confidence': 0.3,  # Low confidence for fallback
                'is_fallback': True,
                'fallback_type': 'simple_projection'
            }
        except Exception as e:
            logger.error(f"Error generating simple prediction: {e}")
            return None