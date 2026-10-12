package expo.modules.digitalbrainruntime

import java.io.ByteArrayOutputStream

/** Hardware VAD controls boundaries; buffers remain bounded even if an end event never arrives. */
internal class GlassesSpeechSegmenter(private val emit: (SpeechChunk) -> Unit) {
  companion object {
    const val MAX_BYTES = 20 * 32000
    const val SILENCE_MS = 900L
    const val MIN_BYTES = 32000 / 3
  }
  private var speech = false
  private var started = 0L
  private var lastAudio = 0L
  private var silenceAt: Long? = null
  private var bytes = ByteArrayOutputStream()
  val collecting get() = speech || bytes.size() > 0
  fun vad(speaking: Boolean, elapsed: Long) {
    speech = speaking
    silenceAt = if (speaking) null else elapsed
  }
  fun pcm(data: ByteArray, wall: Long, elapsed: Long) {
    tick(elapsed)
    if (!speech && silenceAt == null) return
    if (!speech && bytes.size() == 0) return
    if (bytes.size() == 0) started = wall
    lastAudio = elapsed
    var offset = 0
    while (offset < data.size) {
      val count = minOf(MAX_BYTES - bytes.size(), data.size - offset)
      bytes.write(data, offset, count); offset += count
      if (bytes.size() == MAX_BYTES) {
        flush()
        started = wall + offset * 1000L / 32000
      }
    }
  }
  fun tick(elapsed: Long) {
    if ((silenceAt?.let { elapsed - it >= SILENCE_MS } == true) ||
      (bytes.size() > 0 && elapsed - lastAudio >= 2000)) {
      flush(); speech = false; silenceAt = null
    }
  }
  fun finish() { flush(); speech = false; silenceAt = null }
  private fun flush() {
    if (bytes.size() >= MIN_BYTES) {
      val pcm = bytes.toByteArray()
      if (pcm.any { it != 0.toByte() }) emit(SpeechChunk(started, pcm))
    }
    bytes = ByteArrayOutputStream()
  }
}
internal data class SpeechChunk(val startedAtMs: Long, val pcm: ByteArray)
