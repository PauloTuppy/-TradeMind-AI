import numpy as np
import logging
from sklearn.preprocessing import MinMaxScaler
import os
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model_client import ModelClient

logger = logging.getLogger(__name__)

class TFServingModel:
    def __init__(self, server_url=None, window_size=60):
        """
        Initialize TensorFlow Serving model client
        
        Args:
            server_url: URL of TensorFlow Serving server
            window_size: Size of input window
        """
        self.window_size = window_size
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        self.client = ModelClient(server_url)
        
        # Initialize connection
        try:
            logger.info("Initializing connection to TensorFlow Serving")
            self.client.connect()
        except Exception as e:
            logger.error(f"Error connecting to TensorFlow Serving: {e}")
    
    async def predict(self, data, prediction_steps=24):
        """
        Generate predictions using TensorFlow Serving
        
        Args:
            data: Historical price data
            prediction_steps: Number of steps to predict
            
        Returns:
            Array of predicted prices
        """
        try:
            # Ensure we have enough data
            if len(data) < self.window_size:
                logger.warning(f"Not enough data for prediction. Need {self.window_size}, got {len(data)}")
                return np.zeros(prediction_steps)
            
            # Scale data
            data_reshaped = np.array(data).reshape(-1, 1)
            scaled_data = self.scaler.fit_transform(data_reshaped)
            
            # Create prediction sequence
            x_pred = scaled_data[-self.window_size:].reshape(1, self.window_size, 1)
            
            # Make predictions
            predictions = []
            curr_frame = x_pred[0]
            
            for _ in range(prediction_steps):
                # Get prediction from TensorFlow Serving
                pred = await self.client.predict(curr_frame.reshape(1, self.window_size, 1))
                
                # Add prediction to results
                predictions.append(pred[0])
                
                # Update frame for next prediction
                curr_frame = np.append(curr_frame[1:], pred, axis=0)
            
            # Inverse transform to get actual price values
            predictions = np.array(predictions).reshape(-1, 1)
            predictions = self.scaler.inverse_transform(predictions)
            
            return predictions.flatten()
            
        except Exception as e:
            logger.error(f"Error in TF Serving prediction: {e}")
            
            # Fallback to simple prediction
            return self._fallback_prediction(data, prediction_steps)
    
    def _fallback_prediction(self, data, prediction_steps):
        """Fallback prediction method"""
        logger.warning("Using fallback prediction method")
        
        # Simple moving average of last 5 values
        if len(data) >= 5:
            avg = np.mean(data[-5:])
            return np.array([avg] * prediction_steps)
        else:
            # Just repeat the last value
            return np.array([data[-1]] * prediction_steps)
    
    def close(self):
        """Close connection to TensorFlow Serving"""
        if self.client:
            self.client.close()