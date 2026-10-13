package expo.modules.digitalbrainruntime

import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/** Bounded mailbox. Never blocks the BLE/main callback; overflow stops capture instead of dropping audio. */
class GlassesPcmWriter(file: File, private val failure: () -> Unit, private val finished: () -> Unit, private val clock: () -> Long = { android.os.SystemClock.elapsedRealtime() }) {
  private val queue = ArrayBlockingQueue<ByteArray>(128)
  @Volatile private var accepting = true
  @Volatile var bytes = 0L
    private set
  private val thread = Thread({
    try {
      FileOutputStream(file).use { output ->
        var lastSync = clock()
        while (accepting || queue.isNotEmpty()) {
          val data = queue.poll(200, TimeUnit.MILLISECONDS) ?: continue
          output.write(data); bytes += data.size
          if (clock() - lastSync >= 1000) {
            output.fd.sync(); lastSync = clock()
          }
        }
        output.fd.sync()
      }
    } catch (_: Exception) { accepting = false; failure() }
    finally { finished() }
  }, "glasses-audio-writer")
  fun start() = thread.start()
  @Synchronized fun offer(data: ByteArray): Boolean = accepting && queue.offer(data.copyOf())
  @Synchronized fun finish() { accepting = false }
}
