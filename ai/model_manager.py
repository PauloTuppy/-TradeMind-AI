import os
import shutil
import json
import logging
import tensorflow as tf
import numpy as np
from datetime import datetime
import subprocess
import requests
import time

logger = logging.getLogger(__name__)

class ModelManager:
    def __init__(self, base_path='/models'):
        """
        Initialize model manager
        
        Args:
            base_path: Base path for model storage
        """
        self.base_path = base_path
        self.model_name = os.environ.get('MODEL_NAME', 'crypto-lstm')
        self.tf_serving_url = os.environ.get('TF_SERVING_URL', 'http://localhost:8501')
        
        # Create base directory if it doesn't exist
        os.makedirs(os.path.join(self.base_path, self.model_name), exist_ok=True)
    
    def save_model(self, model, version=None, metadata=None):
        """
        Save model to disk with versioning
        
        Args:
            model: TensorFlow model to save
            version: Optional version number (defaults to timestamp)
            metadata: Optional metadata to save with model
            
        Returns:
            Path where model was saved
        """
        try:
            # Generate version if not provided
            if version is None:
                version = int(datetime.now().timestamp())
            
            # Create version directory
            version_dir = os.path.join(self.base_path, self.model_name, str(version))
            os.makedirs(version_dir, exist_ok=True)
            
            # Save model
            model.save(version_dir)
            logger.info(f"Model saved to {version_dir}")
            
            # Save metadata if provided
            if metadata:
                with open(os.path.join(version_dir, 'metadata.json'), 'w') as f:
                    json.dump(metadata, f)
            
            return version_dir
            
        except Exception as e:
            logger.error(f"Error saving model: {e}")
            raise
    
    def load_model(self, version='latest'):
        """
        Load model from disk
        
        Args:
            version: Version to load (number or 'latest')
            
        Returns:
            Loaded TensorFlow model
        """
        try:
            # Get version directory
            if version == 'latest':
                version = self._get_latest_version()
                
            version_dir = os.path.join(self.base_path, self.model_name, str(version))
            
            # Check if directory exists
            if not os.path.exists(version_dir):
                raise FileNotFoundError(f"Model version {version} not found")
            
            # Load model
            model = tf.keras.models.load_model(version_dir)
            logger.info(f"Model loaded from {version_dir}")
            
            return model
            
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            raise
    
    def _get_latest_version(self):
        """Get latest model version"""
        model_dir = os.path.join(self.base_path, self.model_name)
        versions = [int(v) for v in os.listdir(model_dir) if v.isdigit()]
        
        if not versions:
            raise FileNotFoundError("No model versions found")
            
        return max(versions)
    
    def get_model_metadata(self, version='latest'):
        """
        Get model metadata
        
        Args:
            version: Version to get metadata for
            
        Returns:
            Model metadata as dictionary
        """
        try:
            # Get version directory
            if version == 'latest':
                version = self._get_latest_version()
                
            metadata_path = os.path.join(self.base_path, self.model_name, str(version), 'metadata.json')
            
            # Check if metadata exists
            if not os.path.exists(metadata_path):
                return {}
            
            # Load metadata
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
            
            return metadata
            
        except Exception as e:
            logger.error(f"Error getting model metadata: {e}")
            return {}
    
    def list_versions(self):
        """
        List available model versions
        
        Returns:
            List of available versions with metadata
        """
        try:
            model_dir = os.path.join(self.base_path, self.model_name)
            versions = [v for v in os.listdir(model_dir) if v.isdigit()]
            
            result = []
            for version in versions:
                metadata = self.get_model_metadata(version)
                result.append({
                    'version': version,
                    'path': os.path.join(model_dir, version),
                    'metadata': metadata
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Error listing model versions: {e}")
            return []
    
    def delete_version(self, version):
        """
        Delete model version
        
        Args:
            version: Version to delete
            
        Returns:
            True if successful, False otherwise
        """
        try:
            version_dir = os.path.join(self.base_path, self.model_name, str(version))
            
            # Check if directory exists
            if not os.path.exists(version_dir):
                logger.warning(f"Model version {version} not found")
                return False
            
            # Delete directory
            shutil.rmtree(version_dir)
            logger.info(f"Model version {version} deleted")
            
            return True
            
        except Exception as e:
            logger.error(f"Error deleting model version {version}: {e}")
            return False
    
    def reload_serving(self):
        """
        Reload TensorFlow Serving to pick up new models
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Send request to TensorFlow Serving to reload models
            url = f"{self.tf_serving_url}/v1/models/{self.model_name}:reload"
            response = requests.post(url)
            
            if response.status_code == 200:
                logger.info("TensorFlow Serving reloaded successfully")
                return True
            else:
                logger.error(f"Error reloading TensorFlow Serving: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error reloading TensorFlow Serving: {e}")
            return False
    
    def wait_for_serving_ready(self, timeout=60):
        """
        Wait for TensorFlow Serving to be ready
        
        Args:
            timeout: Timeout in seconds
            
        Returns:
            True if ready, False otherwise
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                url = f"{self.tf_serving_url}/v1/models/{self.model_name}"
                response = requests.get(url)
                
                if response.status_code == 200:
                    logger.info("TensorFlow Serving is ready")
                    return True
                    
                time.sleep(1)
            except:
                time.sleep(1)
                
        logger.error(f"TensorFlow Serving not ready after {timeout} seconds")
        return False