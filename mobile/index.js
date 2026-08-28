import './location/backgroundLocationDrainTask';
import './location/backgroundLocation';
import './mentraCapture/backgroundTask';
import { ensureExecutorchInitialized } from './image-understanding/executorchRuntime';

ensureExecutorchInitialized();
import 'expo-router/entry';
