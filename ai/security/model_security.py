import os
import logging
import hashlib
import hmac
import time
import json
import base64
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from cryptography.exceptions import InvalidSignature

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ModelSecurity:
    """Security utilities for model serving."""
    
    def __init__(self, keys_dir=None):
        """Initialize the security utilities.
        
        Args:
            keys_dir: Directory containing keys
        """
        self.keys_dir = keys_dir or os.environ.get('KEYS_DIR', '/etc/model-security/keys')
        
        # Load keys if available
        self.private_key = None
        self.public_key = None
        self.hmac_key = None
        
        self._load_keys()
    
    def _load_keys(self):
        """Load keys from files."""
        try:
            # Check if keys directory exists
            if not os.path.exists(self.keys_dir):
                logger.warning(f"Keys directory {self.keys_dir} does not exist")
                return
            
            # Load private key
            private_key_path = os.path.join(self.keys_dir, 'private_key.pem')
            if os.path.exists(private_key_path):
                with open(private_key_path, 'rb') as f:
                    self.private_key = load_pem_private_key(f.read(), password=None)
                logger.info("Private key loaded")
            
            # Load public key
            public_key_path = os.path.join(self.keys_dir, 'public_key.pem')
            if os.path.exists(public_key_path):
                with open(public_key_path, 'rb') as f:
                    self.public_key = load_pem_public_key(f.read())
                logger.info("Public key loaded")
            
            # Load HMAC key
            hmac_key_path = os.path.join(self.keys_dir, 'hmac_key.txt')
            if os.path.exists(hmac_key_path):
                with open(hmac_key_path, 'rb') as f:
                    self.hmac_key = f.read().strip()
                logger.info("HMAC key loaded")
            
        except Exception as e:
            logger.error(f"Error loading keys: {e}")
    
    def generate_keys(self):
        """Generate new keys."""
        try:
            logger.info("Generating new keys")
            
            # Create keys directory if it doesn't exist
            os.makedirs(self.keys_dir, exist_ok=True)
            
            # Generate RSA key pair
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048
            )
            public_key = private_key.public_key()
            
            # Generate HMAC key
            hmac_key = os.urandom(32)
            
            # Save private key
            from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
            private_pem = private_key.private_bytes(
                encoding=Encoding.PEM,
                format=PrivateFormat.PKCS8,
                encryption_algorithm=NoEncryption()
            )
            with open(os.path.join(self.keys_dir, 'private_key.pem'), 'wb') as f:
                f.write(private_pem)
            
            # Save public key
            from cryptography.hazmat.primitives.serialization import PublicFormat
            public_pem = public_key.public_bytes(
                encoding=Encoding.PEM,
                format=PublicFormat.SubjectPublicKeyInfo
            )
            with open(os.path.join(self.keys_dir, 'public_key.pem'), 'wb') as f:
                f.write(public_pem)
            
            # Save HMAC key
            with open(os.path.join(self.keys_dir, 'hmac_key.txt'), 'wb') as f:
                f.write(hmac_key)
            
            # Load the new keys
            self._load_keys()
            
            logger.info("New keys generated and saved")
            
            return True
            
        except Exception as e:
            logger.error(f"Error generating keys: {e}")
            return False
    
    def sign_model(self, model_path):
        """Sign a model file.
        
        Args:
            model_path: Path to the model file
            
        Returns:
            Signature as a base64-encoded string
        """
        try:
            if not self.private_key:
                logger.error("Private key not available")
                return None
            
            logger.info(f"Signing model at {model_path}")
            
            # Calculate model hash
            sha256_hash = hashlib.sha256()
            with open(model_path, 'rb') as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            model_hash = sha256_hash.digest()
            
            # Sign the hash
            signature = self.private_key.sign(
                model_hash,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            
            # Encode signature as base64
            signature_b64 = base64.b64encode(signature).decode('utf-8')
            
            # Save signature
            signature_path = f"{model_path}.sig"
            with open(signature_path, 'w') as f:
                f.write(signature_b64)
            
            logger.info(f"Model signed, signature saved to {signature_path}")
            
            return signature_b64
            
        except Exception as e:
            logger.error(f"Error signing model: {e}")
            return None
    
    def verify_model(self, model_path, signature=None):
        """Verify a model signature.
        
        Args:
            model_path: Path to the model file
            signature: Base64-encoded signature (if None, will try to load from file)
            
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            if not self.public_key:
                logger.error("Public key not available")
                return False
            
            logger.info(f"Verifying model at {model_path}")
            
            # Load signature if not provided
            if signature is None:
                signature_path = f"{model_path}.sig"
                if not os.path.exists(signature_path):
                    logger.error(f"Signature file {signature_path} not found")
                    return False
                
                with open(signature_path, 'r') as f:
                    signature = f.read().strip()
            
            # Decode signature
            signature_bytes = base64.b64decode(signature)
            
            # Calculate model hash
            sha256_hash = hashlib.sha256()
            with open(model_path, 'rb') as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            model_hash = sha256_hash.digest()
            
            # Verify signature
            try:
                self.public_key.verify(
                    signature_bytes,
                    model_hash,
                    padding.PSS(
                        mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH
                    ),
                    hashes.SHA256()
                )
                logger.info("Model signature verified successfully")
                return True
            except InvalidSignature:
                logger.error("Invalid model signature")
                return False
            
        except Exception as e:
            logger.error(f"Error verifying model: {e}")
            return False
    
    def create_request_signature(self, request_data):
        """Create a signature for a prediction request.
        
        Args:
            request_data: Request data as a dictionary
            
        Returns:
            Dictionary with signature information
        """
        try:
            if not self.hmac_key:
                logger.error("HMAC key not available")
                return None
            
            # Create request metadata
            timestamp = int(time.time())
            nonce = base64.b64encode(os.urandom(16)).decode('utf-8')
            
            # Create message to sign
            message = {
                'data': request_data,
                'timestamp': timestamp,
                'nonce': nonce
            }
            message_json = json.dumps(message, sort_keys=True)
            
            # Create HMAC signature
            signature = hmac.new(
                self.hmac_key,
                message_json.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return {
                'message': message,
                'signature': signature
            }
            
        except Exception as e:
            logger.error(f"Error creating request signature: {e}")
            return None
    
    def verify_request_signature(self, message, signature, max_age=300):
        """Verify a request signature.
        
        Args:
            message: Message as a dictionary
            signature: HMAC signature
            max_age: Maximum age of the request in seconds
            
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            if not self.hmac_key:
                logger.error("HMAC key not available")
                return False
            
            # Check timestamp
            timestamp = message.get('timestamp', 0)
            current_time = int(time.time())
            
            if current_time - timestamp > max_age:
                logger.error(f"Request too old: {current_time - timestamp} seconds")
                return False
            
            # Verify signature
            message_json = json.dumps(message, sort_keys=True)
            expected_signature = hmac.new(
                self.hmac_key,
                message_json.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            if signature != expected_signature:
                logger.error("Invalid request signature")
                return False
            
            logger.info("Request signature verified successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error verifying request signature: {e}")
            return False


def setup_model_security():
    """Set up model security."""
    # Get environment variables
    keys_dir = os.environ.get('KEYS_DIR', '/etc/model-security/keys')
    
    # Create security utilities
    security = ModelSecurity(keys_dir)
    
    # Generate keys if they don't exist
    if not (security.private_key and security.public_key and security.hmac_key):
        security.generate_keys()
    
    # Sign models
    model_dir = os.environ.get('MODEL_DIR', '/models')
    for root, _, files in os.walk(model_dir):
        for file in files:
            if file.endswith('.pb') or file.endswith('.tflite'):
                model_path = os.path.join(root, file)
                security.sign_model(model_path)


if __name__ == '__main__':
    setup_model_security()