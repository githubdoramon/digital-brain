# Native module methods are discovered through Expo's module registry.

# The native Whisper JNI entry points and options access Java methods by name.
-keep class com.rnwhisper.WhisperContext { *; }
-keep class com.facebook.react.bridge.JavaOnlyMap { *; }
