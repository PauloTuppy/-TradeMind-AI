import pandas as pd
import numpy as np
from prophet import Prophet
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ProphetModel:
    def __init__(self, periods=24):
        """
        Initialize Prophet model
        
        Args:
            periods: Number of periods to forecast
        """
        self.periods = periods
        self.model = None
    
    async def predict(self, data):
        """
        Generate predictions using Prophet model
        
        Args:
            data: List of (timestamp, price) tuples
            
        Returns:
            Array of predicted prices
        """
        try:
            # Convert data to DataFrame
            df = pd.DataFrame(data, columns=['ds', 'y'])
            
            # Create and fit model
            self.model = Prophet(
                daily_seasonality=True,
                weekly_seasonality=True,
                changepoint_prior_scale=0.05
            )
            
            self.model.fit(df)
            
            # Create future dataframe
            future = self.model.make_future_dataframe(periods=self.periods, freq='H')
            
            # Make prediction
            forecast = self.model.predict(future)
            
            # Extract predicted values
            predictions = forecast['yhat'].tail(self.periods).values
            
            return predictions
            
        except Exception as e:
            logger.error(f"Error in Prophet prediction: {e}")
            
            # Fallback to simple moving average
            return self._fallback_prediction(data)
    
    def _fallback_prediction(self, data):
        """Fallback prediction method using moving average"""
        try:
            # Extract prices
            prices = [price for _,