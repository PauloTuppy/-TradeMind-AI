import os
import logging
import tensorflow as tf
import numpy as np
from tensorflow.python.framework.convert_to_constants import convert_variables_to_constants_v2

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ModelOptimizer:
    """Optimizes TensorFlow models for production deployment."""
    
    def __init__(self, model_dir=None, output_dir=None):
        """Initialize the model optimizer.
        
        Args:
            model_dir: Directory containing the saved model
            output_dir: Directory to save optimized models
        """
        self.model_dir = model_dir or os.environ.get('MODEL_DIR', '/models/crypto-lstm')
        self.output_dir = output_dir or os.environ.get('OUTPUT_DIR', '/models/optimized')
        
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
    
    def load_model(self):
        """Load the TensorFlow model."""
        try:
            logger.info(f"Loading model from {self.model_dir}")
            self.model = tf.saved_model.load(self.model_dir)
            logger.info("Model loaded successfully")
            return True
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            return False
    
    def quantize_model(self, quantization_type='default'):
        """Quantize the model for faster inference.
        
        Args:
            quantization_type: Type of quantization to apply
                - 'default': Default quantization
                - 'float16': Float16 quantization
                - 'int8': Int8 quantization (requires calibration data)
                
        Returns:
            Path to the quantized model
        """
        try:
            logger.info(f"Quantizing model with {quantization_type} quantization")
            
            # Create converter
            converter = tf.lite.TFLiteConverter.from_saved_model(self.model_dir)
            
            # Set optimization options
            if quantization_type == 'default':
                converter.optimizations = [tf.lite.Optimize.DEFAULT]
            elif quantization_type == 'float16':
                converter.optimizations = [tf.lite.Optimize.DEFAULT]
                converter.target_spec.supported_types = [tf.float16]
            elif quantization_type == 'int8':
                converter.optimizations = [tf.lite.Optimize.DEFAULT]
                converter.representative_dataset = self._representative_dataset
                converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
                converter.inference_input_type = tf.int8
                converter.inference_output_type = tf.int8
            
            # Convert model
            tflite_model = converter.convert()
            
            # Save model
            output_path = os.path.join(self.output_dir, f'model_quantized_{quantization_type}.tflite')
            with open(output_path, 'wb') as f:
                f.write(tflite_model)
            
            logger.info(f"Quantized model saved to {output_path}")
            
            # Calculate size reduction
            original_size = self._get_directory_size(self.model_dir)
            quantized_size = os.path.getsize(output_path)
            reduction = (1 - quantized_size / original_size) * 100
            
            logger.info(f"Size reduction: {reduction:.2f}% (Original: {original_size/1024/1024:.2f} MB, "
                       f"Quantized: {quantized_size/1024/1024:.2f} MB)")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Error quantizing model: {e}")
            return None
    
    def optimize_for_inference(self):
        """Optimize the model for inference by freezing the graph.
        
        Returns:
            Path to the optimized model
        """
        try:
            logger.info("Optimizing model for inference")
            
            # Load model
            model = tf.keras.models.load_model(self.model_dir)
            
            # Get concrete function
            run_model = tf.function(lambda x: model(x))
            concrete_func = run_model.get_concrete_function(
                tf.TensorSpec([1, 60, 1], tf.float32))
            
            # Freeze model
            frozen_func = convert_variables_to_constants_v2(concrete_func)
            frozen_func.graph.as_graph_def()
            
            # Get number of operations
            logger.info(f"Frozen model operations: {len(frozen_func.graph.as_graph_def().node)}")
            
            # Save optimized model
            output_path = os.path.join(self.output_dir, 'model_optimized')
            tf.io.write_graph(graph_or_graph_def=frozen_func.graph,
                             logdir=output_path,
                             name="frozen_graph.pb",
                             as_text=False)
            
            logger.info(f"Optimized model saved to {output_path}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Error optimizing model for inference: {e}")
            return None
    
    def export_for_tensorrt(self):
        """Export the model for TensorRT acceleration.
        
        Returns:
            Path to the TensorRT model
        """
        try:
            logger.info("Exporting model for TensorRT")
            
            # Check if TensorRT is available
            if not tf.config.list_physical_devices('GPU'):
                logger.warning("No GPU available, skipping TensorRT export")
                return None
            
            # Load model
            model = tf.keras.models.load_model(self.model_dir)
            
            # Create SavedModel for TensorRT
            output_path = os.path.join(self.output_dir, 'tensorrt_model')
            
            # Get concrete function
            run_model = tf.function(lambda x: model(x))
            concrete_func = run_model.get_concrete_function(
                tf.TensorSpec([1, 60, 1], tf.float32))
            
            # Save model
            tf.saved_model.save(
                model,
                output_path,
                signatures=concrete_func
            )
            
            # Convert to TensorRT
            from tensorflow.python.compiler.tensorrt import trt_convert as trt
            
            converter = trt.TrtGraphConverterV2(
                input_saved_model_dir=output_path,
                precision_mode=trt.TrtPrecisionMode.FP16
            )
            converter.convert()
            
            # Save converted model
            trt_output_path = os.path.join(self.output_dir, 'tensorrt_optimized')
            converter.save(trt_output_path)
            
            logger.info(f"TensorRT model saved to {trt_output_path}")
            
            return trt_output_path
            
        except Exception as e:
            logger.error(f"Error exporting model for TensorRT: {e}")
            return None
    
    def benchmark_model(self, input_shape=(1, 60, 1), num_iterations=100):
        """Benchmark the model performance.
        
        Args:
            input_shape: Shape of the input tensor
            num_iterations: Number of iterations for benchmarking
            
        Returns:
            Dictionary with benchmark results
        """
        try:
            logger.info(f"Benchmarking model with input shape {input_shape}")
            
            # Load model
            model = tf.keras.models.load_model(self.model_dir)
            
            # Create random input data
            input_data = np.random.random(input_shape).astype(np.float32)
            
            # Warm-up
            for _ in range(10):
                _ = model.predict(input_data)
            
            # Benchmark
            import time
            start_time = time.time()
            
            for _ in range(num_iterations):
                _ = model.predict(input_data)
            
            end_time = time.time()
            
            # Calculate metrics
            elapsed_time = end_time - start_time
            avg_time_ms = (elapsed_time / num_iterations) * 1000
            throughput = num_iterations / elapsed_time
            
            results = {
                'avg_inference_time_ms': avg_time_ms,
                'throughput_inferences_per_second': throughput,
                'total_time_seconds': elapsed_time,
                'num_iterations': num_iterations
            }
            
            logger.info(f"Benchmark results: {results}")
            
            return results
            
        except Exception as e:
            logger.error(f"Error benchmarking model: {e}")
            return None
    
    def _representative_dataset(self):
        """Generate representative dataset for quantization calibration."""
        # In a real implementation, you would load actual data here
        for _ in range(100):
            data = np.random.random((1, 60, 1)).astype(np.float32)
            yield [data]
    
    def _get_directory_size(self, path):
        """Get the size of a directory in bytes."""
        total_size = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        return total_size


def optimize_production_model():
    """Optimize the model for production deployment."""
    # Get environment variables
    model_dir = os.environ.get('MODEL_DIR', '/models/crypto-lstm')
    output_dir = os.environ.get('OUTPUT_DIR', '/models/optimized')
    
    # Create optimizer
    optimizer = ModelOptimizer(model_dir, output_dir)
    
    # Optimize model
    optimizer.load_model()
    optimizer.optimize_for_inference()
    optimizer.quantize_model(quantization_type='float16')
    
    # If GPU is available, export for TensorRT
    if tf.config.list_physical_devices('GPU'):
        optimizer.export_for_tensorrt()
    
    # Benchmark model
    optimizer.benchmark_model()


if __name__ == '__main__':
    optimize_production_model()