import tensorflow as tf
import numpy as np
from sklearn.preprocessing import MinMaxScaler
import logging

logger = logging.getLogger(__name__)

class LSTMModel:
    def __init__(self, model_path, window_size=60):
        """
        Initialize LSTM model
        
        Args:
            model_path: Path to saved model
            window_size: Size of input window
        """
        self.window_size = window_size
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        
        try:
            self.model = tf.keras.models.load_model(model_path)
            logger.info(f"LSTM model loaded from {model_path}")
        except Exception as e:
            logger.error(f"Error loading LSTM model: {e}")
            # Create a fallback model if loading fails
            self._create_fallback_model()
    
    def _create_fallback_model(self):
        """Create a fallback model if loading fails"""
        logger.warning("Creating fallback LSTM model")
        
        self.model = tf.keras.Sequential([
            tf.keras.layers.LSTM(50, return_sequences=True, input_shape=(self.window_size, 1)),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.LSTM(50, return_sequences=False),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(25),
            tf.keras.layers.Dense(1)
        ])
        
        self.model.compile(optimizer='adam', loss='mean_squared_error')
    
    async def predict(self, data, prediction_steps=24):
        """
        Generate predictions using LSTM model
        
        Args:
            data: Historical price data
            prediction_steps: Number of steps to predict
            
        Returns:
            Array of predicted prices
        """
        try:
            # Ensure we have enough data
            if len(data) < self.window_size:
                logger.warning(f"Not enough data for LSTM prediction. Need {self.window_size}, got {len(data)}")
                return np.zeros(prediction_steps)
            
            # Scale data
            data_reshaped = np.array(data).reshape(-1, 1)
            scaled_data = self.scaler.fit_transform(data_reshaped)
            
            # Create prediction sequence
            x_pred = []
            x_pred.append(scaled_data[-self.window_size:])
            x_pred = np.array(x_pred)
            
            # Make initial prediction
            predictions = []
            
            # Use the model to predict the next 'prediction_steps' values
            curr_frame = x_pred[0]
            for _ in range(prediction_steps):
                # Get prediction (scaled)
                pred = self.model.predict(curr_frame.reshape(1, self.window_size, 1), verbose=0)
                
                # Add prediction to the sequence
                predictions.append(pred[0])
                
                # Update frame for next prediction
                curr_frame = np.append(curr_frame[1:], pred, axis=0)
            
            # Inverse transform to get actual price values
            predictions = np.array(predictions).reshape(-1, 1)
            predictions = self.scaler.inverse_transform(predictions)
            
            return predictions.flatten()
            
        except Exception as e:
            logger.error(f"Error in LSTM prediction: {e}")
            return np.zeros(prediction_steps)