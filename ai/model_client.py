import tensorflow as tf
import numpy as np
import grpc
import logging
import os
import json
from tensorflow_serving.apis import predict_pb2
from tensorflow_serving.apis import prediction_service_pb2_grpc
from tensorflow.core.framework import tensor_shape_pb2
from tensorflow.core.framework import types_pb2
from tensorflow.python.framework import tensor_util
import asyncio
from functools import lru_cache

logger = logging.getLogger(__name__)

class ModelClient:
    def __init__(self, server_url=None):
        """
        Initialize TensorFlow Serving client
        
        Args:
            server_url: URL of TensorFlow Serving server (host:port)
        """
        self.server_url = server_url or os.environ.get('TF_SERVING_URL', 'localhost:8500')
        self.channel = None
        self.stub = None
        self.model_name = os.environ.get('MODEL_NAME', 'crypto-lstm')
        self.signature_name = os.environ.get('SIGNATURE_NAME', 'serving_default')
        self.timeout = float(os.environ.get('TF_SERVING_TIMEOUT', 5.0))
        self.connect()
        
    def connect(self):
        """Establish gRPC connection to TensorFlow Serving"""
        try:
            # Create secure channel with retry and keepalive options
            options = [
                ('grpc.max_receive_message_length', 100 * 1024 * 1024),  # 100MB
                ('grpc.max_send_message_length', 100 * 1024 * 1024),     # 100MB
                ('grpc.keepalive_time_ms', 30000),                       # 30s
                ('grpc.keepalive_timeout_ms', 10000),                    # 10s
                ('grpc.keepalive_permit_without_calls', True),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.http2.min_time_between_pings_ms', 10000),         # 10s
            ]
            
            self.channel = grpc.insecure_channel(self.server_url, options=options)
            self.stub = prediction_service_pb2_grpc.PredictionServiceStub(self.channel)
            logger.info(f"Connected to TensorFlow Serving at {self.server_url}")
        except Exception as e:
            logger.error(f"Failed to connect to TensorFlow Serving: {e}")
            raise
    
    async def predict(self, input_data, model_name=None, signature_name=None):
        """
        Make prediction using TensorFlow Serving
        
        Args:
            input_data: Numpy array of input data
            model_name: Optional override for model name
            signature_name: Optional override for signature name
            
        Returns:
            Prediction result as numpy array
        """
        try:
            # Ensure connection is established
            if not self.stub:
                self.connect()
                
            # Prepare request
            request = predict_pb2.PredictRequest()
            request.model_spec.name = model_name or self.model_name
            request.model_spec.signature_name = signature_name or self.signature_name
            
            # Convert input data to TensorFlow tensor
            input_tensor = tf.make_tensor_proto(input_data, dtype=tf.float32)
            request.inputs['lstm_input'].CopyFrom(input_tensor)
            
            # Make prediction with timeout
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: self.stub.Predict(request, timeout=self.timeout)
            )
            
            # Parse response
            output_tensor = response.outputs['dense_1'].float_val
            result = np.array(output_tensor).reshape(-1, 1)
            
            return result
            
        except grpc.RpcError as e:
            status_code = e.code()
            if status_code == grpc.StatusCode.UNAVAILABLE:
                logger.error(f"TensorFlow Serving unavailable: {e.details()}")
                # Try to reconnect
                self.connect()
            elif status_code == grpc.StatusCode.DEADLINE_EXCEEDED:
                logger.error(f"TensorFlow Serving request timed out after {self.timeout}s")
            else:
                logger.error(f"TensorFlow Serving RPC error: {e.details()}")
            
            # Return fallback prediction
            return self._fallback_prediction(input_data)
            
        except Exception as e:
            logger.error(f"Error making prediction: {e}")
            return self._fallback_prediction(input_data)
    
    def _fallback_prediction(self, input_data):
        """Generate fallback prediction when TF Serving fails"""
        logger.warning("Using fallback prediction method")
        
        # Simple moving average as fallback
        if len(input_data.shape) == 3:  # (batch_size, sequence_length, features)
            # Take the last value and repeat it
            last_values = input_data[0, -1, 0]
            return np.array([last_values]).reshape(-1, 1)
        else:
            # Just return zeros
            return np.zeros((1, 1))
    
    def close(self):
        """Close gRPC channel"""
        if self.channel:
            self.channel.close()
            self.channel = None
            self.stub = None