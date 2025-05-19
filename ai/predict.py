from tensorflow.python.keras import models
import tensorflow as tf
from sklearn.preprocessing._data import MinMaxScaler
import numpy as np
from fastapi import APIRouter, HTTPException
import pandas as pd

router = APIRouter()

from typing import Optional, cast
from tensorflow.python.keras.models import Model

class PricePredictor:
    def __init__(self):
        self.model: Optional[Model] = None
        try:
            self.model = cast(Model, models.load_model('models/crypto_lstm.h5'))
            self.scaler = MinMaxScaler(feature_range=(0, 1))
        except Exception as e:
            print(f"Error initializing model: {e}")
            raise
        
    async def predict_next_24h(self, historical_data):
        """
        Predict the next 24 hours of price movement based on historical data
        
        Args:
            historical_data: DataFrame with columns ['time', 'close']
        
        Returns:
            Array of predicted prices for next 24 hours
        """
        try:
            # Extract close prices and reshape for scaling
            close_prices = historical_data['close'].values.reshape(-1, 1)
            
            # Scale the data
            scaled_data = self.scaler.fit_transform(close_prices)
            
            # Create sequence for prediction
            sequence = self.create_sequences(scaled_data)
            
            # Make prediction
            prediction = self.model.predict(sequence[-1:])  # type: ignore
            
            # Convert to numpy array and reshape for inverse transform
            prediction_array = np.array(prediction).reshape(-1, 1)
            
            # Inverse transform to get actual price values
            return self.scaler.inverse_transform(prediction_array)[0]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Prediction error: {str(e)}")
    
    def create_sequences(self, data, window_size=60):
        """
        Create sequences for LSTM model input
        """
        sequences = []
        for i in range(window_size, len(data)):
            sequences.append(data[i-window_size:i])
        return np.array(sequences)

# Initialize predictor
predictor = PricePredictor()

@router.post("/predict")
async def predict_prices(symbol: str, timeframe: str = "1h"):
    """
    Endpoint to predict future prices for a given crypto symbol
    """
    try:
        # In a real implementation, fetch historical data from database or API
        # For now, we'll use dummy data
        historical_data = fetch_historical_data(symbol, timeframe)
        
        predictions = await predictor.predict_next_24h(historical_data)
        
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "predictions": predictions.tolist(),
            "timestamps": generate_future_timestamps(24, timeframe)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def fetch_historical_data(symbol, timeframe):
    """
    Fetch historical data for a given symbol and timeframe
    In production, this would connect to your database or exchange API
    """
    # Dummy implementation - replace with actual data fetching
    return pd.DataFrame({
        'time': pd.date_range(end=pd.Timestamp.now(), periods=500, freq='1H'),
        'close': np.random.normal(20000, 1000, 500)
    })

def generate_future_timestamps(periods, timeframe):
    """
    Generate future timestamps based on the timeframe
    """
    freq_map = {"1h": "1H", "4h": "4H", "1d": "1D"}
    freq = freq_map.get(timeframe, "1H")
    
    future_times = pd.date_range(
        start=pd.Timestamp.now(), 
        periods=periods, 
        freq=freq
    )
    
    return [ts.isoformat() for ts in future_times]
