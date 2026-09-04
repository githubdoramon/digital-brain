import './location/backgroundLocationDrainTask';
import './location/backgroundLocation';
import './mentraCapture/backgroundTask';
import { initializeImageEnhancement } from './mentraCapture/imageEnhancement';
import { initializeWakeWordRuntime } from './mentraCapture/wakeWord';
import { ensureExecutorchInitialized } from './image-understanding/executorchRuntime';

ensureExecutorchInitialized();
void initializeImageEnhancement();
void initializeWakeWordRuntime();
import 'expo-router/entry';
