import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime

import aiohttp
from confluent_kafka import Producer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

class MarketDataProducer:
    def __init__(self, kafka_servers=None):
        """
        Initialize market data producer
        
        Args:
            kafka_servers: Kafka bootstrap servers
        """
        self.kafka_servers = kafka_servers or os.environ.get('KAFKA_SERVERS', 'localhost:9092')
        
        # Initialize Kafka producer
        self.producer = Producer({
            'bootstrap.servers': self.kafka_servers,
            'client.id': 'market-data-producer',
            'acks': 'all',
            'retries': 5,
            'retry.backoff.ms': 500,
            'linger.ms': 5,
            'batch.size': 16384,
            'compression.type': 'snappy'
        })
        
        # Supported symbols
        self.symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT']
        
        # API endpoints
        self.binance_api = 'https://api.binance.com/api/v3'
        
        # Metrics
        self.metrics = {
            'messages_produced': 0,
            'api_calls': 0,
            'errors': 0,
            'start_time': time.time()
        }
        
        # Shutdown flag
        self.running = True
    
    async def start(self):
        """Start fetching and publishing market data"""
        logger.info(f"Starting market data producer with Kafka servers: {self.kafka_servers}")
        
        # Create HTTP session
        async with aiohttp.ClientSession() as session:
            # Run until stopped
            while self.running:
                try:
                    # Fetch and publish data for all symbols
                    await self.fetch_and_publish_all(session)
                    
                    # Wait before next fetch
                    await asyncio.sleep(5)  # 5 seconds interval
                    
                except Exception as e:
                    logger.error(f"Error in market data producer: {e}")