package expo.modules.digitalbrainruntime

import java.io.ByteArrayOutputStream

internal interface GlassesWakeDetector {
  fun accept(pcm: ByteArray): Boolean
  fun reset()
  fun release()
}
internal enum class WakeEnd { SILENCE, LIMIT }
internal data class WakeCapture(val chunk: SpeechChunk, val wakeAtMs: Long, val endAtMs: Long, val reason: WakeEnd, val completedElapsedMs: Long = 0,
  val endpoint: Map<String, Any> = emptyMap())

/** Serial-worker state machine. No audio leaves memory before an accepted wake completes. */
internal class GlassesWakePolicy(
  private val detector: GlassesWakeDetector,
  private val onWake: (Long) -> Unit,
  private val onCapture: (WakeCapture) -> Unit,
  private val onQuiet: () -> Unit,
  initialWake: Long = -6000L,
) {
  companion object { const val WAKE_SILENCE_MS = 4000L; const val COMMAND_START_BYTES = 300 * 32; const val SILENCE_MS = 1500L; const val COOLDOWN_MS = 6000L; const val MAX_BYTES = 30 * 32000 }
  private var commandStarted = false
  private var commandOnsetBytes = 0
  private var speech = false
  private var silenceAt: Long? = null
  private var lastPcm = 0L
  private var lastAudioWall = 0L
  private var lastWake = initialWake
  private var wakeWall = 0L
  private var startWall = 0L
  private var capture: ByteArrayOutputStream? = null
  private val history = PcmRing(4 * 32000)
  private val gate = ByteArrayOutputStream()
  private var gateOpen = false
  private var quiet = false
  private var flushedSilence = false
  val capturing get() = capture != null
  fun vad(value: Boolean, elapsed: Long) {
    // New speech after expected suppressed silence gets a fresh transport deadline for its first PCM.
    if (value && silenceAt != null && capture != null) lastPcm = elapsed
    speech = value
    if (value) silenceAt = null else {
      commandOnsetBytes = 0
      if (silenceAt == null) silenceAt = elapsed
    }
  }
  fun pcm(bytes: ByteArray, wall: Long, elapsed: Long, minimum: Int) {
    val wasSpeech = speech
    tick(wall, elapsed)
    if (wasSpeech) speech = true
    // A late PCM callback after firmware silence cannot create a new utterance.
    if (!speech && capture == null) return
    lastPcm = elapsed; lastAudioWall = wall
    if (capture != null) {
      appendCapture(bytes)
      // The user level gate only screens wake candidates. Quiet command speech must not start the silence timer.
      if (speech) {
        silenceAt = null
        commandOnsetBytes = minOf(COMMAND_START_BYTES, commandOnsetBytes + bytes.size)
        if (commandOnsetBytes >= COMMAND_START_BYTES) commandStarted = true
      } else {
        commandOnsetBytes = 0
        if (silenceAt == null) silenceAt = elapsed
      }
      if (capture!!.size() >= MAX_BYTES) finish(wall, elapsed, WakeEnd.LIMIT)
      return
    }
    if (elapsed - lastWake < COOLDOWN_MS) return
    history.write(bytes)
    if (!gateOpen) {
      gate.write(bytes)
      val pending = gate.toByteArray()
      if (!GlassesAudioLevel.accepts(pending, minimum)) {
        quiet = true
        if (gate.size() > 3200) { gate.reset(); gate.write(pending.copyOfRange(pending.size - 3200, pending.size)) }
        return
      }
      gateOpen = true; gate.reset()
      // Replay bounded onset audio so the volume gate does not cut the wake phrase.
      if (detector.accept(history.toByteArray())) begin(wall, elapsed)
    } else if (detector.accept(bytes)) begin(wall, elapsed)
  }
  private fun begin(wall: Long, elapsed: Long) {
    val seed = history.toByteArray()
    capture = ByteArrayOutputStream().apply { write(seed) }
    startWall = lastAudioWall - seed.size * 1000L / 32000
    wakeWall = wall; lastWake = elapsed
    commandStarted = false; commandOnsetBytes = 0
    onWake(elapsed)
    history.reset(); gate.reset(); gateOpen = false; quiet = false
  }
  private fun appendCapture(bytes: ByteArray) { capture?.let { it.write(bytes, 0, minOf(bytes.size, MAX_BYTES - it.size())) } }
  private fun silenceLimit() = if (capture != null && !commandStarted) WAKE_SILENCE_MS else SILENCE_MS
  // Firmware VAD can lag incoming audio. Once capturing, received PCM must
  // postpone endpointing even while the firmware still reports silence.
  // This never applies the user's wake-volume threshold to their command.
  private fun silenceStart(): Long? = silenceAt?.let { if (capture != null) maxOf(it, lastPcm) else it }
  fun waitMs(elapsed: Long): Long {
    val deadlines = mutableListOf<Long>()
    silenceAt?.let {
      deadlines.add(checkNotNull(silenceStart()) + silenceLimit())
      if (capture == null && gateOpen && !flushedSilence) deadlines.add(it + 100)
    }
    if (capture != null) deadlines.add(lastWake + 30_000)
    if (lastPcm > 0 && silenceAt == null) deadlines.add(lastPcm + 3000)
    return ((deadlines.minOrNull() ?: (elapsed + 1000)) - elapsed).coerceIn(1, 1000)
  }
  fun tick(wall: Long, elapsed: Long) {
    if (capture == null && gateOpen && !flushedSilence && silenceAt?.let { elapsed - it >= 100 } == true) {
      flushedSilence = true
      // Supply trailing decoder blanks when firmware suppresses quiet PCM; never save these as received audio.
      if (detector.accept(ByteArray(9600))) begin(wall, elapsed)
    }
    if (capture != null && elapsed - lastWake >= 30_000) { finish(wall, elapsed, WakeEnd.LIMIT); return }
    if (silenceStart()?.let { elapsed - it >= silenceLimit() } == true) {
      if (capture != null) finish(wall, elapsed, WakeEnd.SILENCE) else resetUtterance()
      speech = false; silenceAt = null
    } else if (lastPcm > 0 && silenceAt == null && elapsed - lastPcm >= 3000) {
      // Unknown transport loss is not proof of silence: discard rather than save partial audio.
      discard(); speech = false; silenceAt = null
    }
  }
  private fun finish(wall: Long, elapsed: Long, reason: WakeEnd) {
    val bytes = capture?.toByteArray() ?: return
    val endpoint = mapOf("firmwareSpeech" to speech, "commandStarted" to commandStarted,
      "lastPcmAgeMs" to (elapsed - lastPcm).coerceAtLeast(0),
      "firmwareSilenceAgeMs" to (silenceAt?.let { elapsed - it } ?: 0L).coerceAtLeast(0),
      "silenceLimitMs" to silenceLimit())
    capture = null
    onCapture(WakeCapture(SpeechChunk(startWall, bytes), wakeWall, wall, reason, elapsed, endpoint))
    resetUtterance()
  }
  private fun resetUtterance() {
    if (quiet && !gateOpen) onQuiet()
    commandStarted = false; commandOnsetBytes = 0
    history.reset(); gate.reset(); gateOpen = false; quiet = false; flushedSilence = false; lastPcm = 0L; detector.reset()
  }
  fun discard() { capture = null; resetUtterance() }
}

/** Fixed PCM onset ring with no per-frame history copies or boxed samples. */
internal class PcmRing(private val capacity: Int) {
  private val data = ByteArray(capacity)
  private var next = 0
  private var size = 0
  fun write(bytes: ByteArray) {
    var offset = 0
    while (offset < bytes.size) {
      val n = minOf(capacity - next, bytes.size - offset)
      bytes.copyInto(data, next, offset, offset + n)
      next = (next + n) % capacity; size = minOf(capacity, size + n); offset += n
    }
  }
  fun toByteArray(): ByteArray {
    val output = ByteArray(size)
    val start = (next - size + capacity) % capacity
    val first = minOf(size, capacity - start)
    data.copyInto(output, 0, start, start + first)
    if (first < size) data.copyInto(output, first, 0, size - first)
    return output
  }
  fun reset() { size = 0; next = 0 }
}
