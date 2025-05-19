import tensorflow as tf
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
import os

def train_lstm_model(data_path, epochs=50, batch_size=32):
    """
    Train an LSTM model for crypto price prediction
    
    Args:
        data_path: Path to CSV with historical price data
        epochs: Number of training epochs
        batch_size: Batch size for training
        
    Returns:
        Trained TensorFlow model
    """
    # Load data
    df = pd.read_csv(data_path)
    
    # Extract close prices
    data = df['close'].values.reshape(-1, 1)
    
    # Scale data
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaled_data = scaler.fit_transform(data)
    
    # Create sequences
    X, y = create_sequences(scaled_data)
    
    # Split into train and test sets
    train_size = int(len(X) * 0.8)
    X_train, X_test = X[:train_size], X[train_size:]
    y_train, y_test = y[:train_size], y[train_size:]
    
    # Build model
    model = tf.keras.Sequential([
        tf.keras.layers.LSTM(50, return_sequences=True, input_shape=(X_train.shape[1], 1)),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.LSTM(50, return_sequences=False),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(25),
        tf.keras.layers.Dense(1)
    ])
    
    # Compile model
    model.compile(optimizer='adam', loss='mean_squared_error')
    
    # Train model
    model.fit(
        X_train, y_train,
        epochs=epochs,
        batch_size=batch_size,
        validation_data=(X_test, y_test),
        verbose=1
    )
    
    # Create models directory if it doesn't exist
    os.makedirs('models', exist_ok=True)
    
    # Save model
    model.save('models/crypto_lstm.h5')
    
    return model

def create_sequences(data, window_size=60):
    """
    Create sequences for LSTM training
    """
    X, y = [], []
    for i in range(window_size, len(data)):
        X.append(data[i-window_size:i, 0])
        y.append(data[i, 0])
    
    return np.array(X).reshape(-1, window_size, 1), np.array(y)

if __name__ == "__main__":
    # Example usage
    # You would need to replace this with your actual data path
    train_lstm_model('data/btc_historical.csv')
    print("Model training complete. Saved to models/crypto_lstm.h5")