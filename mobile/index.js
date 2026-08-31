import './location/backgroundLocationDrainTask';
import './location/backgroundLocation';
import './mentraCapture/backgroundTask';
import { initializeImageEnhancement } from './mentraCapture/imageEnhancement';
import { ensureExecutorchInitialized } from './image-understanding/executorchRuntime';

ensureExecutorchInitialized();
void initializeImageEnhancement();
import 'expo-router/entry';
