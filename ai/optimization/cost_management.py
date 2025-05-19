import os
import logging
import json
import time
try:
    import redis
except ImportError:
    logging.warning("redis package not found, some features may be limited")
import requests
from datetime import datetime, timedelta
from typing import Optional, Any, Dict

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CostManager:
    """Manages costs for ML infrastructure."""
    
    def __init__(self, redis_url=None):
        """Initialize the cost manager.
        
        Args:
            redis_url: URL for Redis connection
        """
        self.redis_url = redis_url or os.environ.get('REDIS_URL', 'redis://localhost:6379')
        self.redis_client = redis.from_url(self.redis_url)
        
        # Cache settings
        self.cache_ttl = int(os.environ.get('PREDICTION_CACHE_TTL', 300))  # 5 minutes
        
        # Metrics
        self.metrics = {
            'cache_hits': 0,
            'cache_misses': 0,
            'total_requests': 0,
            'start_time': time.time()
        }
    
    def get_cached_prediction(self, symbol, timeframe='1h'):
        """Get a cached prediction.
        
        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe for prediction
            
        Returns:
            Cached prediction or None
        """
        try:
            # Increment metrics
            self.metrics['total_requests'] += 1
            
            # Get cache key
            cache_key = f"predictions:{symbol}:{timeframe}:cache"
            
            # Check if prediction is cached
            cached = self.redis_client.get(cache_key)
            
            if cached:
                # Increment metrics
                self.metrics['cache_hits'] += 1
                
                # Parse cached prediction
                prediction = json.loads(cached)
                
                # Check if prediction is still valid
                timestamp = prediction.get('timestamp', 0)
                current_time = time.time()
                
                if current_time - timestamp <= self.cache_ttl:
                    logger.debug(f"Cache hit for {symbol} {timeframe}")
                    return prediction
            
            # Increment metrics
            self.metrics['cache_misses'] += 1
            
            logger.debug(f"Cache miss for {symbol} {timeframe}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting cached prediction: {e}")
            return None
    
    def cache_prediction(self, symbol, prediction, timeframe='1h'):
        """Cache a prediction.
        
        Args:
            symbol: Trading pair symbol
            prediction: Prediction data
            timeframe: Timeframe for prediction
            
        Returns:
            True if cached successfully, False otherwise
        """
        try:
            # Get cache key
            cache_key = f"predictions:{symbol}:{timeframe}:cache"
            
            # Cache prediction
            self.redis_client.set(cache_key, json.dumps(prediction))
            self.redis_client.expire(cache_key, self.cache_ttl)
            
            logger.debug(f"Cached prediction for {symbol} {timeframe}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error caching prediction: {e}")
            return False
    
    def get_cache_metrics(self):
        """Get cache metrics.
        
        Returns:
            Dictionary with cache metrics
        """
        uptime = time.time() - self.metrics['start_time']
        
        return {
            'cache_hits': self.metrics['cache_hits'],
            'cache_misses': self.metrics['cache_misses'],
            'total_requests': self.metrics['total_requests'],
            'hit_rate': (self.metrics['cache_hits'] / self.metrics['total_requests']) * 100 
                        if self.metrics['total_requests'] > 0 else 0,
            'uptime': uptime,
            'requests_per_second': self.metrics['total_requests'] / uptime if uptime > 0 else 0
        }
    
    def estimate_inference_costs(self, num_requests=1000, instance_type='g4dn.xlarge'):
        """Estimate inference costs.
        
        Args:
            num_requests: Number of requests
            instance_type: EC2 instance type
            
        Returns:
            Dictionary with cost estimates
        """
        # Instance costs per hour (approximate)
        instance_costs = {
            't2.medium': 0.0464,
            't2.large': 0.0928,
            't2.xlarge': 0.1856,
            'g4dn.xlarge': 0.526,
            'g4dn.2xlarge': 0.752,
            'p3.2xlarge': 3.06
        }
        
        # Get hourly cost for instance type
        hourly_cost = instance_costs.get(instance_type, 0.5)  # Default to $0.5/hour
        
        # Estimate requests per hour based on cache hit rate
        hit_rate = self.metrics['hit_rate'] / 100 if self.metrics['total_requests'] > 0 else 0.7  # Default to 70% hit rate
        avg_latency = 0.05  # 50ms average latency for non-cached requests
        
        # Calculate requests per hour
        cached_requests = num_requests * hit_rate
        non_cached_requests = num_requests * (1 - hit_rate)
        
        # Assume cached requests are essentially free in terms of compute
        compute_time_hours = (non_cached_requests * avg_latency) / 3600
        
        # Calculate cost per 1000 requests
        cost_per_1000 = (compute_time_hours * hourly_cost) / (num_requests / 1000)
        
        # Calculate monthly cost (assuming consistent traffic)
        requests_per_month = num_requests * 24 * 30  # Requests per hour * 24 hours * 30 days
        monthly_cost = (requests_per_month / 1000) * cost_per_1000
        
        return {
            'instance_type': instance_type,
            'hourly_instance_cost': hourly_cost,
            'estimated_hit_rate': hit_rate * 100,
            'cost_per_1000_requests': cost_per_1000,
            'estimated_monthly_cost': monthly_cost,
            'estimated_monthly_requests': requests_per_month,
            'estimated_monthly_savings': monthly_cost * hit_rate  # Savings from caching
        }
    
    def optimize_instance_type(self, requests_per_hour, budget_constraint=None):
        """Recommend optimal instance type based on load.
        
        Args:
            requests_per_hour: Expected requests per hour
            budget_constraint: Maximum monthly budget
            
        Returns:
            Recommended instance type and configuration
        """
        # Instance types with their capabilities
        instance_types = {
            't2.medium': {'max_rps': 50, 'cost': 0.0464, 'ram': 4},
            't2.large': {'max_rps': 100, 'cost': 0.0928, 'ram': 8},
            't2.xlarge': {'max_rps': 200, 'cost': 0.1856, 'ram': 16},
            'g4dn.xlarge': {'max_rps': 500, 'cost': 0.526, 'ram': 16, 'gpu': True},
            'g4dn.2xlarge': {'max_rps': 1000, 'cost': 0.752, 'ram': 32, 'gpu': True},
            'p3.2xlarge': {'max_rps': 2000, 'cost': 3.06, 'ram': 64, 'gpu': True}
        }
        
        # Calculate required requests per second
        required_rps = requests_per_hour / 3600
        
        # Calculate hit rate
        hit_rate = self.metrics['hit_rate'] / 100 if self.metrics['total_requests'] > 0 else 0.7
        
        # Adjust required RPS based on cache hit rate
        adjusted_rps = required_rps * (1 - hit_rate)
        
        # Find suitable instance types
        suitable_instances = []
        for instance, specs in instance_types.items():
            if specs['max_rps'] >= adjusted_rps:
                # Calculate monthly cost
                monthly_cost = specs['cost'] * 24 * 30
                
                # Check budget constraint
                if budget_constraint and monthly_cost > budget_constraint:
                    continue
                
                suitable_instances.append({
                    'instance_type': instance,
                    'max_rps': specs['max_rps'],
                    'hourly_cost': specs['cost'],
                    'monthly_cost': monthly_cost,
                    'ram': specs['ram'],
                    'has_gpu': specs.get('gpu', False),
                    'utilization': (adjusted_rps / specs['max_rps']) * 100
                })
        
        # Sort by cost
        suitable_instances.sort(key=lambda x: x['hourly_cost'])
        
        if not suitable_instances:
            return {
                'recommendation': 'No suitable instance found',
                'required_rps': required_rps,
                'adjusted_rps': adjusted_rps,
                'hit_rate': hit_rate * 100
            }
        
        # Get the cheapest suitable instance
        recommended = suitable_instances[0]
        
        # Check if spot instances would be beneficial
        spot_discount = 0.7  # Assume 70% discount for spot instances
        spot_cost = recommended['hourly_cost'] * spot_discount
        
        # Add autoscaling recommendation
        autoscaling_recommendation = self._get_autoscaling_recommendation(
            adjusted_rps, recommended['max_rps'])
        
        return {
            'recommendation': recommended['instance_type'],
            'hourly_cost': recommended['hourly_cost'],
            'monthly_cost': recommended['monthly_cost'],
            'spot_instance_hourly_cost': spot_cost,
            'spot_instance_monthly_cost': spot_cost * 24 * 30,
            'utilization': recommended['utilization'],
            'autoscaling': autoscaling_recommendation,
            'cache_recommendation': self._get_cache_recommendation(hit_rate)
        }
    
    def _get_autoscaling_recommendation(self, required_rps, max_rps):
        """Get autoscaling recommendation.
        
        Args:
            required_rps: Required requests per second
            max_rps: Maximum requests per second for the instance
            
        Returns:
            Autoscaling recommendation
        """
        # Calculate min and max instances
        min_instances = max(1, int(required_rps / max_rps))
        max_instances = min_instances * 3  # Allow for 3x traffic spikes
        
        return {
            'min_instances': min_instances,
            'max_instances': max_instances,
            'scale_up_threshold': 70,  # Scale up when CPU > 70%
            'scale_down_threshold': 30  # Scale down when CPU < 30%
        }
    
    def _get_cache_recommendation(self, hit_rate):
        """Get cache optimization recommendation.
        
        Args:
            hit_rate: Current cache hit rate
            
        Returns:
            Cache optimization recommendation
        """
        if hit_rate < 0.5:
            return {
                'action': 'increase_ttl',
                'current_ttl': self.cache_ttl,
                'recommended_ttl': min(self.cache_ttl * 2, 3600),  # Max 1 hour
                'reason': 'Low hit rate, consider increasing TTL'
            }
        elif hit_rate > 0.9:
            return {
                'action': 'optimize_memory',
                'reason': 'High hit rate, consider optimizing memory usage'
            }
        else:
            return {
                'action': 'maintain',
                'reason': 'Hit rate is optimal'
            }
    
    def optimize_model_size(self, model_path, target_size_mb=None, target_latency_ms=None):
        """Optimize model size using quantization.
        
        Args:
            model_path: Path to the model
            target_size_mb: Target model size in MB
            target_latency_ms: Target inference latency in ms
            
        Returns:
            Optimization results
        """
        try:
            import tensorflow as tf
            
            # Load the model
            model = tf.saved_model.load(model_path)
            
            # Get original model size
            original_size = self._get_directory_size(model_path) / (1024 * 1024)  # Convert to MB
            
            # Create converter
            converter = tf.lite.TFLiteConverter.from_saved_model(model_path)
            
            # Apply optimizations (must be a list according to TF Lite docs)
            converter.optimizations = [tf.lite.Optimize.DEFAULT]  # type: ignore[assignment]
            
            # Convert to TFLite
            tflite_model: bytes = converter.convert()  # type: ignore[assignment]
            
            # Save optimized model
            optimized_path = f"{model_path}_optimized"
            os.makedirs(optimized_path, exist_ok=True)
            with open(os.path.join(optimized_path, 'model.tflite'), 'wb') as f:
                f.write(tflite_model)
            
            # Get optimized model size
            optimized_size = len(tflite_model) / (1024 * 1024)  # Convert to MB
            
            # Measure inference latency
            original_latency = self._measure_inference_latency(model_path)
            optimized_latency = self._measure_inference_latency(optimized_path, is_tflite=True)
            
            return {
                'original_model': {
                    'path': model_path,
                    'size_mb': original_size,
                    'latency_ms': original_latency if original_latency is not None else 0
                },
                'optimized_model': {
                    'path': optimized_path,
                    'size_mb': optimized_size,
                    'latency_ms': optimized_latency if optimized_latency is not None else 0
                },
                'size_reduction': (1 - (optimized_size / original_size)) * 100 if original_size and optimized_size else 0,
                'latency_reduction': (1 - (optimized_latency / original_latency)) * 100 if original_latency and optimized_latency and original_latency != 0 else 0,
                'meets_target_size': target_size_mb is None or optimized_size <= target_size_mb,
                'meets_target_latency': target_latency_ms is None or (optimized_latency is not None and optimized_latency <= target_latency_ms)
            }
            
        except Exception as e:
            logger.error(f"Error optimizing model: {e}")
            return {
                'error': str(e),
                'status': 'failed'
            }
    
    def _get_directory_size(self, path):
        """Get directory size in bytes.
        
        Args:
            path: Directory path
            
        Returns:
            Directory size in bytes
        """
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        return total_size
    
    def _measure_inference_latency(self, model_path, is_tflite=False, num_runs=100):
        """Measure inference latency.
        
        Args:
            model_path: Path to the model
            is_tflite: Whether the model is TFLite
            num_runs: Number of inference runs
            
        Returns:
            Average inference latency in ms
        """
        try:
            import tensorflow as tf
            import numpy as np
            
            # Create random input data (assuming time series data with 60 steps)
            input_data = np.random.random((1, 60, 1)).astype(np.float32)
            
            if is_tflite:
                # Load TFLite model
                interpreter = tf.lite.Interpreter(model_path=os.path.join(model_path, 'model.tflite'))
                interpreter.allocate_tensors()
                
                # Get input and output tensors
                input_details = interpreter.get_input_details()
                output_details = interpreter.get_output_details()
                
                # Measure inference time
                start_time = time.time()
                for _ in range(num_runs):
                    interpreter.set_tensor(input_details[0]['index'], input_data)
                    interpreter.invoke()
                    _ = interpreter.get_tensor(output_details[0]['index'])
                end_time = time.time()
            else:
                # Load SavedModel with type checking and error handling
                try:
                    model: Optional[Any] = tf.saved_model.load(model_path)
                    if model is None:
                        logger.error(f"Failed to load model (returned None): {model_path}")
                        return None
                    
                    signatures: Optional[Dict[str, Any]] = getattr(model, 'signatures', None)
                    if not signatures:
                        logger.error(f"Model has no signatures attribute: {model_path}")
                        return None
                    
                    if 'serving_default' not in signatures:
                        logger.error(f"Model missing serving_default signature: {model_path}")
                        return None
                        
                    infer = signatures['serving_default']
                    
                    # Measure inference time
                    start_time = time.time()
                except Exception as e:
                    logger.error(f"Error loading model {model_path}: {str(e)}", exc_info=True)
                    return None
                for _ in range(num_runs):
                    _ = infer(tf.constant(input_data))
                end_time = time.time()
            
            # Calculate average latency
            avg_latency = ((end_time - start_time) / num_runs) * 1000  # Convert to ms
            
            return avg_latency
            
        except Exception as e:
            logger.error(f"Error measuring inference latency: {e}")
            return None
