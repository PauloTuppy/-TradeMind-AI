import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime

import numpy as np
import redis
import tensorflow as tf
from aiokafka import AIOKafkaConsumer
from confluent_kafka import Producer

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_client import ModelClient
from data.normalizer import DataNormalizer
from models.fallback_handler import FallbackHandler

logger = logging.getLogger(__name__)

class PredictionStream:
    def __init__(self, redis_url=None, kafka_servers=None):
        """
        Initialize the prediction stream service
        
        Args:
            redis_url: URL for Redis connection
            kafka_servers: Kafka bootstrap servers
        """
        # Initialize connections
        self.redis_url = redis_url or os.environ.get('REDIS_URL', 'redis://localhost:6379')
        self.kafka_servers = kafka_servers or os.environ.get('KAFKA_SERVERS', 'localhost:9092')
        
        # Initialize Redis client
        self.redis_client = redis.from_url(self.redis_url)
        
        # Initialize model client
        self.model_client = ModelClient(os.environ.get('TF_SERVING_URL', 'localhost:8500'))
        
        # Initialize data normalizer
        self.data_normalizer = DataNormalizer(self.redis_url)
        
        # Initialize fallback handler
        self.fallback_handler = FallbackHandler(self.redis_url)
        
        # Initialize Kafka producer for predictions
        self.producer = Producer({
            'bootstrap.servers': self.kafka_servers,
            'client.id': 'prediction-service',
            'acks': 'all',
            'retries': 5,
            'retry.backoff.ms': 500,
            'linger.ms': 5,
            'batch.size': 16384,
            'compression.type': 'snappy'
        })
        
        # Configure window size for predictions
        self.window_size = 60
        self.prediction_horizon = 24
        
        # Track last prediction time for each symbol
        self.last_prediction_time = {}
        self.prediction_interval = 300  # 5 minutes in seconds
        
        # Supported symbols
        self.supported_symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT']
        
        # Metrics
        self.metrics = {
            'processed_messages': 0,
            'successful_predictions': 0,
            'failed_predictions': 0,
            'start_time': time.time()
        }
    
    async def start_stream(self):
        """
        Start consuming price updates from Kafka and generating predictions
        """
        logger.info(f"Starting prediction stream with Kafka servers: {self.kafka_servers}")
        
        # Create consumer
        consumer = AIOKafkaConsumer(
            'crypto-prices',
            bootstrap_servers=self.kafka_servers,
            group_id='prediction-service',
            auto_offset_reset='latest',
            enable_auto_commit=True,
            value_deserializer=lambda m: json.loads(m.decode('utf-8'))
        )
        
        # Start consumer
        await consumer.start()
        logger.info("Kafka consumer started")
        
        try:
            # Process messages
            async for message in consumer:
                try:
                    # Update metrics
                    self.metrics['processed_messages'] += 1
                    
                    # Process message
                    await self.process_message(message.value)
                    
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    self.metrics['failed_predictions'] += 1
        
        except Exception as e:
            logger.error(f"Error in Kafka consumer: {e}")
        
        finally:
            # Stop consumer
            await consumer.stop()
            logger.info("Kafka consumer stopped")
    
    async def process_message(self, message):
        """
        Process a price update message
        
        Args:
            message: Price update message
        """
        try:
            # Extract data from message
            symbol = message.get('symbol')
            price = message.get('price')
            timestamp = message.get('timestamp')
            
            if not symbol or not price:
                logger.warning(f"Invalid message format: {message}")
                return
            
            # Check if symbol is supported
            if symbol not in self.supported_symbols:
                return
            
            # Process market data through normalizer
            self.data_normalizer.process_market_data(message)
            
            # Check if we should generate a new prediction
            current_time = time.time()
            last_time = self.last_prediction_time.get(symbol, 0)
            
            if current_time - last_time >= self.prediction_interval:
                # Generate prediction
                await self.generate_prediction(symbol)
                
                # Update last prediction time
                self.last_prediction_time[symbol] = current_time
        
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            raise
    
    async def generate_prediction(self, symbol):
        """
        Generate prediction for a symbol
        
        Args:
            symbol: Trading pair symbol
        """
        try:
            logger.info(f"Generating prediction for {symbol}")
            
            # Get historical data
            features = self.data_normalizer.feature_store.get_features(symbol)
            historical_data = features.get('history', [])
            
            if not historical_data:
                logger.warning(f"No historical data available for {symbol}")
                return
            
            # Extract prices and prepare sequence
            prices = [float(f.get('price', 0)) for f in historical_data]
            prices.reverse()  # Oldest first
            
            # Ensure we have enough data
            if len(prices) < self.window_size:
                logger.warning(f"Not enough data for prediction. Need {self.window_size}, got {len(prices)}")
                return
            
            # Generate prediction
            sequence = self.preprocess(prices)
            prediction = await self.model_client.predict(sequence)
            
            # If prediction failed, use fallback
            if prediction is None or len(prediction) == 0:
                logger.warning(f"Prediction failed for {symbol}, using fallback")
                prediction = self.fallback_handler.generate_simple_prediction(symbol, prices)
                prediction_values = prediction.get('predictions', {}).get('price', [])
            else:
                # Format prediction
                prediction_values = prediction.flatten().tolist()
                
                # Create prediction object
                prediction = {
                    'symbol': symbol,
                    'timestamp': datetime.now().timestamp(),
                    'predictions': {
                        'price': prediction_values,
                        'models': {
                            'lstm': prediction_values
                        }
                    },
                    'confidence': 0.8,
                    'is_fallback': False
                }
            
            # Publish prediction
            await self.publish_prediction(symbol, prediction)
            
            # Update metrics
            self.metrics['successful_predictions'] += 1
            
            return prediction
        
        except Exception as e:
            logger.error(f"Error generating prediction: {e}")
            self.metrics['failed_predictions'] += 1
            raise
    
    def preprocess(self, prices):
        """
        Preprocess price data for prediction
        
        Args:
            prices: List of historical prices
            
        Returns:
            Preprocessed sequence for model input
        """
        try:
            # Convert to numpy array
            data = np.array(prices[-self.window_size*2:])
            
            # Create sequence
            sequence = np.array([data[-self.window_size:]])
            
            # Reshape for LSTM input (batch_size, timesteps, features)
            sequence = sequence.reshape((1, self.window_size, 1))
            
            return sequence
        
        except Exception as e:
            logger.error(f"Error preprocessing data: {e}")
            raise
    
    async def publish_prediction(self, symbol, prediction):
        """
        Publish prediction to Kafka and Redis
        
        Args:
            symbol: Trading pair symbol
            prediction: Prediction data
        """
        try:
            # Publish to Kafka
            self.producer.produce(
                'crypto-predictions',
                key=symbol.encode('utf-8'),
                value=json.dumps(prediction).encode('utf-8'),
                callback=self._delivery_report
            )
            self.producer.poll(0)  # Trigger any callbacks
            
            # Publish to Redis
            await self.publish_to_redis(symbol, prediction)
            
            logger.info(f"Published prediction for {symbol}")
        
        except Exception as e:
            logger.error(f"Error publishing prediction: {e}")
            raise
    
    async def publish_to_redis(self, symbol, prediction):
        """
        Publish prediction to Redis
        
        Args:
            symbol: Trading pair symbol
            prediction: Prediction data
        """
        try:
            # Store current prediction
            key = f"predictions:{symbol}:current"
            self.redis_client.set(key, json.dumps(prediction))
            self.redis_client.expire(key, 86400)  # 24 hours TTL
            
            # Add to historical predictions
            hist_key = f"predictions:{symbol}:history"
            self.redis_client.lpush(hist_key, json.dumps(prediction))
            self.redis_client.ltrim(hist_key, 0, 99)  # Keep last 100 predictions
            self.redis_client.expire(hist_key, 86400 * 7)  # 7 days TTL
            
            # Publish event
            self.redis_client.publish('prediction-updates', json.dumps({
                'symbol': symbol,
                'timestamp': prediction.get('timestamp')
            }))
            
            return True
        
        except Exception as e:
            logger.error(f"Error publishing to Redis: {e}")
            return False
    
    def _delivery_report(self, err, msg):
        """
        Kafka producer delivery callback
        
        Args:
            err: Error (if any)
            msg: Message that was delivered
        """
        if err is not None:
            logger.error(f"Message delivery failed: {err}")
        else:
            logger.debug(f"Message delivered to {msg.topic()} [{msg.partition()}]")
    
    def get_metrics(self):
        """
        Get service metrics
        
        Returns:
            Dictionary with service metrics
        """
        uptime = time.time() - self.metrics['start_time']
        
        return {
            **self.metrics,
            'uptime': uptime,
            'messages_per_second': self.metrics['processed_messages'] / uptime if uptime > 0 else 0,
            'success_rate': (self.metrics['successful_predictions'] / 
                            (self.metrics['successful_predictions'] + self.metrics['failed_predictions'])) * 100 
                            if (self.metrics['successful_predictions'] + self.metrics['failed_predictions']) > 0 else 0
        }
    
    async def close(self):
        """Close all connections"""
        try:
            # Close model client
            self.model_client.close()
            
            # Flush producer
            self.producer.flush()
            
            logger.info("Prediction stream service closed")
        except Exception as e:
            logger.error(f"Error closing prediction stream: {e}")