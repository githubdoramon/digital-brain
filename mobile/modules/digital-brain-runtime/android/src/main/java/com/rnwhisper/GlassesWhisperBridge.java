package com.rnwhisper;

import com.facebook.react.bridge.JavaOnlyMap;

/** JNI-only use of the installed Whisper runtime; no React context or JavaScript callbacks. */
public final class GlassesWhisperBridge {
  private final int contextId;
  private final int jobId;
  public GlassesWhisperBridge() { this(1700000001); }
  public GlassesWhisperBridge(int id) { contextId = id; jobId = id + 1; }
  private long context;
  private volatile boolean running;
  public void load(String path) {
    if (context != 0) return;
    if (!WhisperContext.isNativeLibraryLoaded()) throw new IllegalStateException("Speech engine unavailable");
    context = WhisperContext.initContext(contextId, path);
    if (context == 0) throw new IllegalStateException("Speech model unavailable");
  }
  public String transcribe(float[] pcm) {
    JavaOnlyMap options = JavaOnlyMap.of("language", "en", "maxThreads", 2, "nProcessors", 1,
      "translate", false, "tokenTimestamps", false, "temperature", 0.0);
    running = true;
    try {
      int code = WhisperContext.fullWithNewJob(jobId, context, pcm, pcm.length, options, null);
      if (code != 0) throw new IllegalStateException("Speech transcription failed");
      StringBuilder text = new StringBuilder();
      int count = WhisperContext.getTextSegmentCount(context);
      for (int i = 0; i < count; i++) text.append(WhisperContext.getTextSegment(context, i));
      return text.toString().trim();
    } finally { running = false; }
  }
  public void abort() { if (running) WhisperContext.abortTranscribe(jobId); }
  public void release() {
    if (context != 0) { WhisperContext.freeContext(contextId, context); context = 0; }
  }
}
