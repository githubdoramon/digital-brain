package expo.modules.digitalbrainruntime

import android.content.Context
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext

/** Explicit foreground test using the exact chat transport, decoder and player. */
internal object GlassesReplyTest {
  private val main = Handler(Looper.getMainLooper())
  @Volatile var running = false
    private set
  private var generation = 0L
  @Volatile private var client: GlassesChatClient? = null
  private var lastResult: Map<String, Any?> = emptyMap()
  fun diagnostics() = lastResult
  private fun <T> onMain(action: () -> T): T = runBlocking { withContext(Dispatchers.Main) { action() } }
  fun cancel() {
    if (!running) return
    generation++
    client?.cancel()
    GlassesAlertPlayback.stopSpeech()
  }
  fun run(c: Context, text: String): Map<String, Any?> {
    require(text.trim().length in 1..400) { "Enter up to 400 characters" }
    val owner = checkNotNull(GlassesWakeStore.account(c)) { "Sign in first" }
    val token = onMain {
      check(!running) { "Wait for the current test to finish" }
      running = true
      lastResult = mapOf("startedAtMs" to System.currentTimeMillis(), "status" to "preparing")
      ++generation
    }
    fun eligible() = generation == token && DigitalBrainRuntime.activityVisible &&
      RuntimeGlasses.enabled(c) && RuntimeGlasses.signedIn(c) && RuntimeGlasses.alertsReady() &&
      GlassesWakeStore.account(c) == owner && !GlassesRecording.busy &&
      c.getSystemService(AudioManager::class.java).mode == AudioManager.MODE_NORMAL
    val guard = object : Runnable {
      override fun run() {
        if (generation != token || !running) return
        if (!eligible()) cancel() else main.postDelayed(this, 250)
      }
    }
    main.post(guard)
    var acquired = false
    try {
      val result = GlassesChatTurn.test(c, text, ::eligible, { client = it },
        begin = { GlassesListening.beginReplyTest().also { acquired = it } }, end = {})
      onMain { lastResult = lastResult + mapOf("finishedAtMs" to System.currentTimeMillis(),
        "status" to result.status, "audioError" to result.audioError) }
      return mapOf("status" to result.status, "audioError" to result.audioError)
    } finally {
      onMain {
        main.removeCallbacks(guard)
        client = null
        if (lastResult["status"] == "preparing") lastResult = lastResult + mapOf(
          "finishedAtMs" to System.currentTimeMillis(), "status" to "failed")
        if (acquired) GlassesListening.endReplyTest(c)
        running = false
      }
    }
  }
}
