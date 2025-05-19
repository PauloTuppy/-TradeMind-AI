import os
import logging
import datetime
import tempfile
import tensorflow as tf
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.io.gcp.bigquery import ReadFromBigQuery

from tfx import v1 as tfx
from tfx.components import CsvExampleGen, StatisticsGen, SchemaGen
from tfx.components import ExampleValidator, Transform, Trainer, Evaluator, Pusher
from tfx.proto import trainer_pb2, pusher_pb2
from tfx.orchestration import metadata, pipeline
from tfx.orchestration.beam.beam_dag_runner import BeamDagRunner
from tfx.dsl.components.common.resolver import Resolver
from tfx.dsl.experimental.latest_blessed_model_resolver import LatestBlessedModelResolver

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ModelRetrainingPipeline:
    """TFX pipeline for retraining crypto price prediction models."""
    
    def __init__(self, 
                 pipeline_name='crypto_price_prediction',
                 pipeline_root=None,
                 data_root=None,
                 module_file=None,
                 serving_model_dir=None,
                 metadata_path=None,
                 beam_pipeline_args=None):
        """Initialize the retraining pipeline.
        
        Args:
            pipeline_name: Name of the pipeline
            pipeline_root: Root directory for pipeline artifacts
            data_root: Root directory for data
            module_file: Path to the module file containing model code
            serving_model_dir: Directory where the model will be pushed
            metadata_path: Path to the metadata DB
            beam_pipeline_args: Arguments for the Beam pipeline
        """
        self.pipeline_name = pipeline_name
        self.pipeline_root = pipeline_root or os.path.join('/tmp', pipeline_name)
        self.data_root = data_root or os.path.join(self.pipeline_root, 'data')
        self.module_file = module_file or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
            'trainer', 
            'model.py'
        )
        self.serving_model_dir = serving_model_dir or os.path.join(
            self.pipeline_root, 'serving_model'
        )
        self.metadata_path = metadata_path or os.path.join(
            self.pipeline_root, 'metadata', 'metadata.db'
        )
        self.beam_pipeline_args = beam_pipeline_args or [
            '--direct_running_mode=multi_processing',
            '--direct_num_workers=4',
        ]
        
        # Ensure directories exist
        os.makedirs(self.pipeline_root, exist_ok=True)
        os.makedirs(self.data_root, exist_ok=True)
        os.makedirs(os.path.dirname(self.metadata_path), exist_ok=True)
        os.makedirs(self.serving_model_dir, exist_ok=True)
        
        # Model hyperparameters
        self.hyperparameters = {
            'lstm_units': 128,
            'dense_units': 64,
            'dropout_rate': 0.2,
            'learning_rate': 0.001,
            'batch_size': 32,
            'epochs': 50,
            'window_size': 60,
            'prediction_steps': 24
        }
    
    def _create_pipeline(self):
        """Create the TFX pipeline."""
        # Components for the pipeline
        components = []
        
        # 1. Ingest data from Snowflake/Redis
        example_gen = self._create_example_gen()
        components.append(example_gen)
        
        # 2. Generate statistics
        statistics_gen = StatisticsGen(examples=example_gen.outputs['examples'])
        components.append(statistics_gen)
        
        # 3. Create schema
        schema_gen = SchemaGen(
            statistics=statistics_gen.outputs['statistics'],
            infer_feature_shape=True
        )
        components.append(schema_gen)
        
        # 4. Validate examples
        example_validator = ExampleValidator(
            statistics=statistics_gen.outputs['statistics'],
            schema=schema_gen.outputs['schema']
        )
        components.append(example_validator)
        
        # 5. Transform features
        transform = Transform(
            examples=example_gen.outputs['examples'],
            schema=schema_gen.outputs['schema'],
            module_file=self.module_file
        )
        components.append(transform)
        
        # 6. Train model
        trainer = Trainer(
            module_file=self.module_file,
            examples=transform.outputs['transformed_examples'],
            transform_graph=transform.outputs['transform_graph'],
            schema=schema_gen.outputs['schema'],
            train_args=trainer_pb2.TrainArgs(num_steps=10000),
            eval_args=trainer_pb2.EvalArgs(num_steps=1000),
            custom_config={'hyperparameters': self.hyperparameters}
        )
        components.append(trainer)
        
        # 7. Get the latest blessed model for evaluation
        model_resolver = Resolver(
            instance_name='latest_blessed_model_resolver',
            resolver_class=LatestBlessedModelResolver,
            model=tfx.dsl.Channel(type=tfx.types.standard_artifacts.Model),
            model_blessing=tfx.dsl.Channel(
                type=tfx.types.standard_artifacts.ModelBlessing)
        )
        components.append(model_resolver)
        
        # 8. Evaluate model
        evaluator = Evaluator(
            examples=example_gen.outputs['examples'],
            model=trainer.outputs['model'],
            baseline_model=model_resolver.outputs['model'],
            eval_config=tfx.proto.eval_pb2.EvalConfig(
                model_specs=[
                    tfx.proto.eval_pb2.ModelSpec(
                        signature_name='serving_default',
                        label_key='price_next_day',
                    )
                ],
                metrics_specs=[
                    tfx.proto.eval_pb2.MetricsSpec(
                        metrics=[
                            tfx.proto.eval_pb2.MetricConfig(
                                class_name='MeanSquaredError',
                                threshold=tfx.proto.eval_pb2.MetricThreshold(
                                    value_threshold=tfx.proto.eval_pb2.GenericValueThreshold(
                                        upper_bound={'value': 0.05}
                                    ),
                                    # Require improvement from baseline
                                    change_threshold=tfx.proto.eval_pb2.GenericChangeThreshold(
                                        direction=tfx.proto.eval_pb2.MetricDirection.LOWER_IS_BETTER,
                                        absolute={'value': -0.001}
                                    )
                                )
                            ),
                            tfx.proto.eval_pb2.MetricConfig(
                                class_name='MeanAbsoluteError',
                                threshold=tfx.proto.eval_pb2.MetricThreshold(
                                    value_threshold=tfx.proto.eval_pb2.GenericValueThreshold(
                                        upper_bound={'value': 0.1}
                                    )
                                )
                            )
                        ]
                    )
                ],
                slicing_specs=[
                    # Overall slice
                    tfx.proto.eval_pb2.SlicingSpec(),
                ]
            )
        )
        components.append(evaluator)
        
        # 9. Push model to serving if it passes evaluation
        pusher = Pusher(
            model=trainer.outputs['model'],
            model_blessing=evaluator.outputs['blessing'],
            push_destination=pusher_pb2.PushDestination(
                filesystem=pusher_pb2.PushDestination.Filesystem(
                    base_directory=self.serving_model_dir
                )
            )
        )
        components.append(pusher)
        
        # Create the pipeline
        return pipeline.Pipeline(
            pipeline_name=self.pipeline_name,
            pipeline_root=self.pipeline_root,
            components=components,
            enable_cache=True,
            metadata_connection_config=metadata.sqlite_metadata_connection_config(
                self.metadata_path
            ),
            beam_pipeline_args=self.beam_pipeline_args
        )
    
    def _create_example_gen(self):
        """Create the example generator component."""
        # For this implementation, we'll use a CSV-based example generator
        # In a production system, you might want to use a custom ExampleGen
        # that pulls data from Snowflake, Redis, or other sources
        
        # Create a Beam pipeline to extract data from sources
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, 'data.csv')
            
            # Define the Beam pipeline for data extraction
            def run_extraction_pipeline():
                pipeline_options = PipelineOptions(self.beam_pipeline_args)
                with beam.Pipeline(options=pipeline_options) as p:
                    # Extract data from Snowflake (simulated with BigQuery for this example)
                    historical_data = (
                        p 
                        | "ReadHistoricalData" >> ReadFromBigQuery(
                            query="""
                            SELECT * FROM `project.dataset.crypto_prices`
                            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
                            ORDER BY timestamp ASC
                            """,
                            use_standard_sql=True
                        )
                    )
                    
                    # Extract features
                    features = (
                        historical_data
                        | "ExtractFeatures" >> beam.ParDo(ExtractFeaturesFn())
                        | "WindowData" >> beam.WindowInto(beam.window.FixedWindows(3600))  # 1-hour windows
                        | "CreateSequences" >> beam.ParDo(CreateSequencesFn(
                            window_size=self.hyperparameters['window_size'],
                            prediction_steps=self.hyperparameters['prediction_steps']
                        ))
                    )
                    
                    # Write to CSV
                    _ = (
                        features
                        | "WriteToCSV" >> beam.io.WriteToText(
                            output_path, 
                            file_name_suffix='.csv',
                            shard_name_template='',
                            header=','.join([
                                'timestamp', 'symbol', 
                                *[f'price_t-{i}' for i in range(self.hyperparameters['window_size'], 0, -1)],
                                'price_next_day'
                            ])
                        )
                    )
            
            # Run the extraction pipeline
            run_extraction_pipeline()
            
            # Create the example gen component
            return CsvExampleGen(input_base=temp_dir)
    
    def run(self):
        """Run the pipeline."""
        logger.info(f"Running pipeline: {self.pipeline_name}")
        logger.info(f"Pipeline root: {self.pipeline_root}")
        logger.info(f"Module file: {self.module_file}")
        logger.info(f"Serving model dir: {self.serving_model_dir}")
        
        # Create and run the pipeline
        pipeline = self._create_pipeline()
        BeamDagRunner().run(pipeline)
        
        logger.info("Pipeline run completed")


class ExtractFeaturesFn(beam.DoFn):
    """Beam DoFn to extract features from raw data."""
    
    def process(self, element):
        """Process a single element.
        
        Args:
            element: A dictionary with raw data
            
        Returns:
            A dictionary with extracted features
        """
        try:
            # Extract basic features
            timestamp = element.get('timestamp')
            symbol = element.get('symbol')
            price = float(element.get('price', 0))
            volume = float(element.get('volume', 0))
            
            # Calculate technical indicators
            # (In a real implementation, you would add more sophisticated indicators)
            features = {
                'timestamp': timestamp,
                'symbol': symbol,
                'price': price,
                'volume': volume,
                'log_price': np.log1p(price) if price > 0 else 0,
                'log_volume': np.log1p(volume) if volume > 0 else 0,
            }
            
            yield features
            
        except Exception as e:
            logger.error(f"Error extracting features: {e}")


class CreateSequencesFn(beam.DoFn):
    """Beam DoFn to create sequences for time series prediction."""
    
    def __init__(self, window_size=60, prediction_steps=24):
        """Initialize the DoFn.
        
        Args:
            window_size: Size of the input window
            prediction_steps: Number of steps to predict
        """
        self.window_size = window_size
        self.prediction_steps = prediction_steps
        self.buffer = {}
    
    def process(self, element, window=beam.DoFn.WindowParam):
        """Process a single element.
        
        Args:
            element: A dictionary with features
            window: The window the element belongs to
            
        Returns:
            A dictionary with sequences
        """
        try:
            # Get symbol and add to buffer
            symbol = element.get('symbol')
            if symbol not in self.buffer:
                self.buffer[symbol] = []
            
            # Add to buffer
            self.buffer[symbol].append(element)
            
            # Sort buffer by timestamp
            self.buffer[symbol] = sorted(
                self.buffer[symbol], 
                key=lambda x: x.get('timestamp')
            )
            
            # If we have enough data, create sequences
            if len(self.buffer[symbol]) >= self.window_size + self.prediction_steps:
                # Create sequences
                for i in range(len(self.buffer[symbol]) - self.window_size - self.prediction_steps + 1):
                    # Input sequence
                    input_seq = self.buffer[symbol][i:i+self.window_size]
                    
                    # Target (next day's price)
                    target = self.buffer[symbol][i+self.window_size+self.prediction_steps-1]
                    
                    # Create sequence
                    sequence = {
                        'timestamp': input_seq[-1].get('timestamp'),
                        'symbol': symbol,
                    }
                    
                    # Add input features
                    for j, item in enumerate(input_seq):
                        sequence[f'price_t-{self.window_size-j}'] = item.get('price')
                    
                    # Add target
                    sequence['price_next_day'] = target.get('price')
                    
                    yield sequence
                
                # Trim buffer to avoid memory issues
                self.buffer[symbol] = self.buffer[symbol][-self.window_size-self.prediction_steps:]
            
        except Exception as e:
            logger.error(f"Error creating sequences: {e}")


def run_retraining_pipeline():
    """Run the retraining pipeline."""
    # Get environment variables
    pipeline_name = os.environ.get('PIPELINE_NAME', 'crypto_price_prediction')
    pipeline_root = os.environ.get('PIPELINE_ROOT', '/tmp/tfx_pipeline')
    data_root = os.environ.get('DATA_ROOT', '/tmp/tfx_data')
    module_file = os.environ.get('MODULE_FILE', 'ai/trainer/model.py')
    serving_model_dir = os.environ.get('SERVING_MODEL_DIR', '/models/crypto-lstm')
    metadata_path = os.environ.get('METADATA_PATH', '/tmp/tfx_metadata/metadata.db')
    
    # Create and run the pipeline
    pipeline = ModelRetrainingPipeline(
        pipeline_name=pipeline_name,
        pipeline_root=pipeline_root,
        data_root=data_root,
        module_file=module_file,
        serving_model_dir=serving_model_dir,
        metadata_path=metadata_path
    )
    pipeline.run()


if __name__ == '__main__':
    # Import numpy here to avoid issues with beam serialization
    import numpy as np
    run_retraining_pipeline()