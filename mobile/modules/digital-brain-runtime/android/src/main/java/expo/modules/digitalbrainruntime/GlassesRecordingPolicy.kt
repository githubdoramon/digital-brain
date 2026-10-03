package expo.modules.digitalbrainruntime

object GlassesRecordingPolicy {
  const val SAMPLE_RATE = 16000
  const val BYTES_PER_FRAME = 2
  fun callInterrupts(incoming: Boolean, callType: Int, categoryCall: Boolean, ongoing: Boolean) =
    incoming || callType in 2..3 || (categoryCall && ongoing)
  fun presentationUs(bytes: Long) = bytes / BYTES_PER_FRAME * 1_000_000L / SAMPLE_RATE
  fun validPcm(rate: Int, bits: Int, channels: Int, encoding: String, bytes: Int) =
    rate == SAMPLE_RATE && bits == 16 && channels == 1 && encoding == "pcm_s16le" && bytes in 2..65536 && bytes % 2 == 0
}
enum class GlassesRecordingState { IDLE, RECORDING, SAVING, SAVE_FAILED }
enum class GlassesRecordingStop(val description: String) {
  USER("Recording saved"), DISCONNECTED("Glasses disconnected; recording stopped"),
  CALL("Call interrupted recording"), RUNTIME_STOPPED("Glasses disabled; recording stopped"),
  AUDIO_LOST("Microphone stream stopped"), FORMAT("Unsupported microphone format"),
  STORAGE("Recording storage failed"), BACKPRESSURE("Audio writer could not keep up"),
  RECOVERED("Interrupted recording recovered"), START_FAILED("Could not start glasses microphone")
}
