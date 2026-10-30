package expo.modules.digitalbrainruntime

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

internal data class GlassesChatResult(val status: String, val threadId: String? = null, val audioError: String? = null)

/** One turn on the existing speech worker, with main-thread ownership and no idle polling. */
internal object GlassesChatTurn {
  private val main = Handler(Looper.getMainLooper())
  private fun <T> onMain(action: () -> T): T = runBlocking { withContext(Dispatchers.Main) { action() } }
  fun run(c: Context, text: String, requestId: String, capturedAt: Long, timezone: String,
          eligible: () -> Boolean, register: (GlassesChatClient?) -> Unit,
          begin: () -> Boolean, end: () -> Unit): GlassesChatResult {
    if (!onMain { eligible() && begin() }) return GlassesChatResult("cancelled")
    val client = GlassesChatClient(c) { check(onMain(eligible)) }
    register(client)
    val timeout = Runnable { client.cancel() }
    main.postDelayed(timeout, 320_000)
    var thread: String? = null
    try {
      val response = client.request(text, requestId, capturedAt, timezone)
      main.removeCallbacks(timeout)
      thread = response.optString("thread_id").takeIf { it.isNotBlank() }
      val encoded = response.optJSONObject("audio")
        ?: return GlassesChatResult("audio_unavailable", thread,
          response.optString("audio_error").takeIf { it in setOf("tts_not_configured", "tts_failed", "tts_invalid_audio", "tts_text_too_long", "tts_audio_too_long", "tts_audio_empty") })
      val audio = GlassesChatPolicy.audio(encoded)
      val finished = CountDownLatch(1)
      var spoken = false
      val started = onMain {
        eligible() && GlassesAlertPlayback.speech(c, audio) { success -> spoken = success; finished.countDown() }
      }
      if (!started) return GlassesChatResult("audio_unavailable", thread, "playback_failed")
      val deadline = SystemClock.elapsedRealtime() + 50_000
      while (!finished.await(250, TimeUnit.MILLISECONDS)) {
        if (!onMain(eligible) || SystemClock.elapsedRealtime() >= deadline) return GlassesChatResult("cancelled", thread)
      }
      return GlassesChatResult(if (spoken) "spoken" else "audio_unavailable", thread)
    } catch (_: Exception) {
      return GlassesChatResult(if (onMain(eligible)) "failed" else "cancelled", thread)
    } finally {
      main.removeCallbacks(timeout); client.cancel(); register(null)
      onMain { GlassesAlertPlayback.stopSpeech(); end() }
    }
  }
}
