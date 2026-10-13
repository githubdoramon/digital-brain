package expo.modules.digitalbrainruntime

import android.content.Context
import android.os.SystemClock
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/** One serial native detector lane. Overflow invalidates the session, never catches up with stale audio. */
internal class GlassesWakeSession(
  c: Context, initialWake: Long,
  private val onWake: (Long) -> Unit, private val onCapture: (WakeCapture) -> Unit,
  private val onQuiet: () -> Unit, private val onFailure: () -> Unit,
) {
  companion object {
    private val executor = java.util.concurrent.Executors.newSingleThreadExecutor { r -> Thread(r, "glasses-wake-detector") }
  }
  private data class Input(val bytes: ByteArray?, val speech: Boolean?, val wall: Long, val elapsed: Long, val minimum: Int)
  private val queue = ArrayBlockingQueue<Input>(64)
  @Volatile private var running = true
  init { executor.execute {
    var detector: GlassesWakeDetector? = null
    try {
      detector = GlassesSherpaDetector(c)
      val policy = GlassesWakePolicy(detector, { if (running) onWake(it) },
        { if (running) onCapture(it) }, { if (running) onQuiet() }, initialWake)
      while (running) {
        val event = queue.poll(policy.waitMs(SystemClock.elapsedRealtime()), TimeUnit.MILLISECONDS)
        if (!running) break
        if (event != null) {
          if (SystemClock.elapsedRealtime() - event.elapsed > 1000) { running = false; onFailure(); break }
          event.speech?.let { policy.vad(it, event.elapsed) }
          event.bytes?.let { policy.pcm(it, event.wall, event.elapsed, event.minimum) }
        } else policy.tick(System.currentTimeMillis(), SystemClock.elapsedRealtime())
      }
      policy.discard()
    } catch (_: Exception) { if (running) onFailure() }
    catch (_: LinkageError) { if (running) onFailure() }
    finally { running = false; queue.clear(); detector?.release() }
  } }
  private fun offer(event: Input) {
    if (!running) return
    if (!queue.offer(event)) { running = false; queue.clear(); onFailure() }
  }
  fun pcm(bytes: ByteArray, minimum: Int) = offer(Input(bytes.copyOf(), null, System.currentTimeMillis(), SystemClock.elapsedRealtime(), minimum))
  fun vad(speech: Boolean) = offer(Input(null, speech, System.currentTimeMillis(), SystemClock.elapsedRealtime(), 0))
  fun close() { running = false; queue.clear() }
}
