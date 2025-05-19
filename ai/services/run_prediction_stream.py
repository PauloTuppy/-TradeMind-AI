import asyncio
import logging
import os
import signal
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.prediction_stream import PredictionStream

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('prediction_stream.log')
    ]
)

logger = logging.getLogger(__name__)

# Global variables
prediction_stream = None
shutdown_event = asyncio.Event()

async def start_service():
    """Start the prediction stream service"""
    global prediction_stream
    
    try:
        # Initialize service
        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
        kafka_servers = os.environ.get('KAFKA_SERVERS', 'localhost:9092')
        
        prediction_stream = PredictionStream(redis_url, kafka_servers)
        
        # Start metrics reporter
        asyncio.create_task(report_metrics())
        
        # Start stream processing
        await prediction_stream.start_stream()
        
    except Exception as e:
        logger.error(f"Error starting prediction stream service: {e}")
        raise

async def report_metrics():
    """Report service metrics periodically"""
    global prediction_stream, shutdown_event
    
    while not shutdown_event.is_set():
        try:
            if prediction_stream:
                metrics = prediction_stream.get_metrics()
                logger.info(f"Service metrics: {metrics}")
        except Exception as e:
            logger.error(f"Error reporting metrics: {e}")
        
        # Wait for next report or shutdown
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass

async def shutdown():
    """Shutdown the service gracefully"""
    global prediction_stream, shutdown_event
    
    logger.info("Shutting down prediction stream service...")
    
    # Signal shutdown
    shutdown_event.set()
    
    # Close service
    if prediction_stream:
        await prediction_stream.close()
    
    logger.info("Prediction stream service shutdown complete")

def handle_signals():
    """Handle OS signals for graceful shutdown"""
    loop = asyncio.get_event_loop()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

async def main():
    """Main entry point"""
    try:
        # Setup signal handlers
        handle_signals()
        
        # Start service
        await start_service()
        
    except Exception as e:
        logger.error(f"Error in main: {e}")
        await shutdown()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())