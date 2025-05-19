import os
import tensorflow as tf
import tensorflow_transform as tft
from tensorflow.keras import layers, models
from tfx.components.trainer.fn_args_utils import FnArgs
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Feature names
FEATURE_KEYS = [f'price_t-{i}' for i in range(60, 0, -1)]
LABEL_KEY = 'price_next_day'

def _get_serve_tf_examples_fn(model, tf_transform_output):
    """Returns a function that parses a serialized tf.Example and applies TFT."""
    
    @tf.function
    def serve_tf_examples_fn(serialized_tf_examples):
        """Serves predictions for input examples."""
        feature_spec = tf_transform_output.raw_feature_spec()
        feature_spec.pop(LABEL_KEY)
        parsed_features = tf.io.parse_example(serialized_tf_examples, feature_spec)
        
        transformed_features = tf_transform_output.transform_raw_features(
            parsed_features)
        
        return model(transformed_features)
    
    return serve_tf_examples_fn

def _input_fn(file_pattern, tf_transform_output, batch_size=32):
    """Generates features and labels for training or evaluation."""
    transformed_feature_spec = (
        tf_transform_output.transformed_feature_spec().copy())
    
    dataset = tf.data.experimental.make_batched_features_dataset(
        file_pattern=file_pattern,
        batch_size=batch_size,
        features=transformed_feature_spec,
        reader=tf.data.TFRecordDataset,
        label_key=LABEL_KEY,
        shuffle=True,
        shuffle_buffer_size=10000,
        prefetch_buffer_size=tf.data.experimental.AUTOTUNE)
    
    return dataset

def preprocessing_fn(inputs):
    """Preprocess input features into transformed features."""
    outputs = {}
    
    # Scale numerical features
    for key in FEATURE_KEYS:
        outputs[key] = tft.scale_to_0_1(inputs[key])
    
    # Scale label
    outputs[LABEL_KEY] = tft.scale_to_0_1(inputs[LABEL_KEY])
    
    return outputs

def _build_keras_model(hyperparameters):
    """Creates a LSTM model for time series forecasting."""
    # Get hyperparameters
    lstm_units = hyperparameters.get('lstm_units', 128)
    dense_units = hyperparameters.get('dense_units', 64)
    dropout_rate = hyperparameters.get('dropout_rate', 0.2)
    learning_rate = hyperparameters.get('learning_rate', 0.001)
    window_size = hyperparameters.get('window_size', 60)
    
    # Define the model
    inputs = tf.keras.Input(shape=(window_size, 1), name='price_input')
    
    # LSTM layers
    x = layers.LSTM(lstm_units, return_sequences=True)(inputs)
    x = layers.Dropout(dropout_rate)(x)
    x = layers.LSTM(lstm_units // 2)(x)
    x = layers.Dropout(dropout_rate)(x)
    
    # Dense layers
    x = layers.Dense(dense_units, activation='relu')(x)
    x = layers.Dropout(dropout_rate)(x)
    
    # Output layer
    outputs = layers.Dense(1, name='price_prediction')(x)
    
    # Create model
    model = models.Model(inputs=inputs, outputs=outputs)
    
    # Compile model
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate),
        loss='mse',
        metrics=['mae', 'mse']
    )
    
    model.summary()
    
    return model

def run_fn(fn_args: FnArgs):
    """Train the model based on given args.
    
    Args:
        fn_args: Holds args as name/value pairs.
    """
    tf_transform_output = tft.TFTransformOutput(fn_args.transform_output)
    
    # Get hyperparameters
    hyperparameters = fn_args.custom_config.get('hyperparameters', {})
    batch_size = hyperparameters.get('batch_size', 32)
    epochs = hyperparameters.get('epochs', 50)
    window_size = hyperparameters.get('window_size', 60)
    
    # Create train and eval input functions
    train_dataset = _input_fn(
        fn_args.train_files, tf_transform_output, batch_size)
    eval_dataset = _input_fn(
        fn_args.eval_files, tf_transform_output, batch_size)
    
    # Build the model
    model = _build_keras_model(hyperparameters)
    
    # Add callbacks
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor='val_loss',
            patience=5,
            restore_best_weights=True
        ),
        tf.keras