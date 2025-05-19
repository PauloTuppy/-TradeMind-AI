import logging
import numpy as np
import tensorflow as tf
from datetime import datetime
import redis
import json
import os
from threading import Lock

# Import model implementations
from models.lstm import LSTMModel
from models.prophet import ProphetModel
from models.sentiment import SentimentModel
from models.ensemble import EnsembleModel

logger = logging.getLogger(__name__)

class ModelOrchestrator:
    def __init__(self, redis_url, model_config=None):
        """
        Initialize the model orchestrator
        
        Args:
            redis_url: URL for Redis connection
            model_config: Configuration for models
        """
        self.redis_client = redis.from_url(redis_url)
        self.prediction_cache = PredictionCache(redis_url)
        self.model_lock = Lock()
        
        # Default configuration
        self.config = model_config or {
            'lstm': {
                'enabled': True,
                'weight': 0.5,
                'window_size': 60
            },
            'prophet': {
                'enabled': True,
                'weight': 0.3,
                'periods': 24
            },
            'sentiment': {
                'enabled': True,
                'weight': 0.2,
                'sources': ['twitter', 'news']
            }
        }
        
        # Initialize models
        self.models = {}
        self._initialize_models()
        
        # Initialize ensemble
        self.ensemble = EnsembleModel(self.config)
    
    def _initialize_models(self):
        """Initialize prediction models"""
        try:
            if self.config['lstm']['enabled']:
                self.models['lstm'] = LSTMModel(
                    model_path=os.environ.get('LSTM_MODEL_PATH', 'models/lstm_model.h5'),
                    window_size=self.config['lstm']['window_size']
                )
            
            if self.config['prophet']['enabled']:
                self.models['prophet'] = ProphetModel(
                    periods=self.config['prophet']['periods']
                )
            
            if self.config['sentiment']['enabled']:
                self.models['sentiment'] = SentimentModel(
                    api_key=os.environ.get('OPENAI_API_KEY'),
                    sources=self.config['sentiment']['sources']
                )
        except Exception as e:
            logger.error(f"Error initializing models: {e}")
    
    async def generate_predictions(self, symbol, timeframe='1h'):
        """
        Generate predictions for a symbol
        
        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe for predictions
            
        Returns:
            Dictionary with predictions
        """
        try:
            # Check cache first
            cached = self.prediction_cache.get_predictions(symbol, timeframe)
            if cached:
                logger.info(f"Using cached predictions for {symbol}")
                return cached
            
            # Get features from Redis
            features_key = f"features:{symbol}:history"
            features_data = self.redis_client.lrange(features_key, 0, 999)
            
            if not features_data:
                logger.warning(f"No feature data available for {symbol}")
                return None
            
            # Parse features
            features = [json.loads(item) for item in features_data]
            
            # Generate predictions from each model
            predictions = {}
            
            with self.model_lock:  # Ensure thread safety for model inference
                # LSTM predictions
                if 'lstm' in self.models:
                    lstm_input = self._prepare_lstm_input(features)
                    predictions['lstm'] = await self.models['lstm'].predict(lstm_input)
                
                # Prophet predictions
                if 'prophet' in self.models:
                    prophet_input = self._prepare_prophet_input(features)
                    predictions['prophet'] = await self.models['prophet'].predict(prophet_input)
                
                # Sentiment predictions
                if 'sentiment' in self.models:
                    sentiment_input = symbol.split('USDT')[0]  # Extract base currency
                    predictions['sentiment'] = await self.models['sentiment'].predict(sentiment_input)
            
            # Combine predictions with ensemble
            ensemble_prediction = self.ensemble.combine_predictions(predictions)
            
            # Format final prediction
            final_prediction = {
                'symbol': symbol,
                'timeframe': timeframe,
                'timestamp': datetime.now().timestamp(),
                'predictions': {
                    'price': ensemble_prediction,
                    'models': predictions
                },
                'confidence': self._calculate_confidence(predictions)
            }
            
            # Cache prediction
            self.prediction_cache.store_predictions(symbol, timeframe, final_prediction)
            
            return final_prediction
            
        except Exception as e:
            logger.error(f"Error generating predictions: {e}")
            return None
    
    def _prepare_lstm_input(self, features):
        """Prepare input data for LSTM model"""
        # Extract prices and normalize
        prices = np.array([f.get('price', 0) for f in features])
        timestamps = np.array([f.get('timestamp', 0) for f in features])
        
        # Sort by timestamp
        idx = np.argsort(timestamps)
        sorted_prices = prices[idx]
        
        return sorted_prices
    
    def _prepare_prophet_input(self, features):
        """Prepare input data for Prophet model"""
        # Extract prices and timestamps
        data = [(datetime.fromtimestamp(f.get('timestamp', 0)), f.get('price', 0)) 
                for f in features]
        
        # Sort by timestamp
        data.sort(key=lambda x: x[0])
        
        return data
    
    def _calculate_confidence(self, predictions):
        """Calculate confidence score for predictions"""
        # Simple implementation - can be enhanced
        if not predictions:
            return 0.0
            
        # Base confidence on model agreement
        if len(predictions) == 1:
            return 0.7  # Single model
            
        # Calculate variance between model predictions
        values = []
        for model, pred in predictions.items():
            if model == 'sentiment':
                # Convert sentiment score to price direction
                continue
            if isinstance(pred, list) and pred:
                values.append(pred[0])  # First prediction point
            elif isinstance(pred, (int, float)):
                values.append(pred)
                
        if not values:
            return 0.5
            
        # Lower variance = higher confidence
        variance = np.var(values) if len(values) > 1 else 0
        normalized_variance = min(1.0, variance / (values[0] * 0.1))  # Normalize to 0-1
        
        confidence = 1.0 - normalized_variance
        return min(0.95, max(0.3, confidence))  # Clamp between 0.3 and 0.95

class PredictionCache:
    def __init__(self, redis_url):
        """
        Initialize the prediction cache
        
        Args:
            redis_url: URL for Redis connection
        """
        self.redis_client = redis.from_url(redis_url)
        self.ttl = 3600  # 1 hour
    
    def store_predictions(self, symbol, timeframe, predictions):
        """
        Store predictions in Redis
        
        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe for predictions
            predictions: Prediction data
        """
        try:
            key = f"predictions:{symbol}:{timeframe}"
            self.redis_client.set(key, json.dumps(predictions))
            self.redis_client.expire(key, self.ttl)
            return True
        except Exception as e:
            logger.error(f"Error storing predictions: {e}")
            return False
    
    def get_predictions(self, symbol, timeframe):
        """
        Get predictions from Redis
        
        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe for predictions
            
        Returns:
            Dictionary with predictions or None if not found
        """
        try:
            key = f"predictions:{symbol}:{timeframe}"
            data = self.redis_client.get(key)
            
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Error retrieving predictions: {e}")
            return None