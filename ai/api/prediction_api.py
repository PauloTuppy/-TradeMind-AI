from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
import os
import sys
import asyncio

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.orchestrator import ModelOrchestrator
from models.fallback_handler import FallbackHandler
from data.normalizer import DataNormalizer

logger = logging.getLogger(__name__)

# Initialize components
redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
model_orchestrator = ModelOrchestrator(redis_url)
fallback_handler = FallbackHandler(redis_url)
data_normalizer = DataNormalizer(redis_url)

# Define API models
class PredictionRequest(BaseModel):
    symbol: str
    timeframe: str = "1h"
    include_features: bool = False

class PredictionResponse(BaseModel):
    symbol: str
    timeframe: str
    timestamp: float
    predictions: Dict[str, Any]
    confidence: float
    is_fallback: bool = False

# Create router
router = APIRouter()

@router.post("/predict", response_model=PredictionResponse)
async def predict_prices(
    request: PredictionRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate price predictions for a cryptocurrency
    """
    try:
        # Generate predictions
        prediction = await model_orchestrator.generate_predictions(
            request.symbol, 
            request.timeframe
        )
        
        # If prediction failed, try fallback
        if not prediction:
            logger.warning(f"Primary prediction failed for {request.symbol}, using fallback")
            prediction = fallback_handler.get_fallback_prediction(
                request.symbol, 
                request.timeframe
            )
            
            # If no fallback available, generate simple prediction
            if not prediction:
                logger.warning(f"No fallback available for {request.symbol}, generating simple prediction")
                
                # Get historical data
                features = data_normalizer.feature_store.get_features(request.symbol)
                historical_prices = [f.get('price', 0) for f in features.get('history', [])]
                
                prediction = fallback_handler.generate_simple_prediction(
                    request.symbol,
                    historical_prices
                )
        
        # Store successful predictions for fallback
        if prediction and not prediction.get('is_fallback', False):
            background_tasks.add_task(
                fallback_handler.store_prediction,
                request.symbol,
                request.timeframe,
                prediction
            )
        
        # If still no prediction, raise error
        if not prediction:
            raise HTTPException(status_code=500, detail="Failed to generate prediction")
        
        # Include features if requested
        if request.include_features:
            features = data_normalizer.feature_store.get_features(request.symbol)
            prediction['features'] = features
        
        return prediction
        
    except Exception as e:
        logger.error(f"Error generating prediction: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/models/status")
async def get_model_status():
    """
    Get status of prediction models
    """
    try:
        # Get model status
        status = {
            'models': {},
            'tf_serving': {
                'url': os.environ.get('TF_SERVING_URL', 'http://localhost:8501'),
                'status': 'unknown'
            }
        }
        
        # Check TF Serving status
        try:
            from model_manager import ModelManager
            manager = ModelManager()
            tf_serving_ready = await asyncio.to_thread(manager.wait_for_serving_ready, 5)
            status['tf_serving']['status'] = 'ready' if tf_serving_ready else 'unavailable'
        except Exception as e:
            status['tf_serving']['status'] = f'error: {str(e)}'
        
        # Check model status
        for model_name, model in model_orchestrator.models.items():
            status['models'][model_name] = {
                'enabled': model_orchestrator.config[model_name]['enabled'],
                'weight': model_orchestrator.config[model_name]['weight']
            }
        
        return status
        
    except Exception as e:
        logger.error(f"Error getting model status: {e}")
        raise HTTPException(status_code=500, detail=str(e))