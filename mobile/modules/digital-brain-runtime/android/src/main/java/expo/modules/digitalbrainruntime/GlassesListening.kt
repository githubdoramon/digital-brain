package expo.modules.digitalbrainruntime

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioManager
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import com.mentra.bluetoothsdk.MicPcmEvent
import com.rnwhisper.GlassesWhisperBridge
import java.io.File
import java.util.concurrent.ArrayBlockingQueue

/** Explicitly armed microphone session. React only configures it and reads counts while interactive. */
object GlassesListening {
  private val main = Handler(Looper.getMainLooper())
  private var context: Context? = null
  private var account: String? = null
  private var model: String? = null
  private var enabled = false
  private var armed = false
  private var active = false
  private var transitioning = false
  private val callSources = linkedMapOf<String, Long>()
  private var blockedUntil = 0L
  private var wakeSession: GlassesWakeSession? = null
  private var sessionGeneration = 0L
  private var lastWake = -6000L
  private var baseUri: String? = null
  private var capturing = false
  private var wakes = 0L
  private var ledFailures = 0L
  private var ledRequests = 0L
  private var legacyCleanupStarted = false
  private var status = "Listening is off"
  private var minimumVolume = 0
  private var audioLevel = 0
  private var levelAt = 0L
  private var levelWindowAt = 0L
  private var quietSegments = 0L
  private var speechEvents = 0L
  private var dropped = 0L
  private var failures = 0L
  private var completed = 0L
  private var processedMs = 0L
  private var inferenceMs = 0L
  private var lastPcm = 0L
  private var vadEnabled: Boolean? = null
  private var speechGeneration = 0
  private var captureWake: PowerManager.WakeLock? = null
  private data class Job(val account: String, val model: String, val baseUri: String, val capture: WakeCapture)
  private val queue = ArrayBlockingQueue<Job>(4)
  private var worker: Thread? = null
  @Volatile private var keepWorker = false
  @Volatile private var engine: GlassesWhisperBridge? = null
  @Volatile private var busy = false
  // Retain the foreground microphone type while waiting for reconnect/call end within this session.
  val foregroundOwned get() = armed && enabled

  private fun load(c: Context) {
    context = c.applicationContext
    val next = GlassesWakeStore.account(c)
    if (next == account) return
    shutdown(discard = true); account = next
    val prefs = next?.let { GlassesWakeStore.prefs(c, it) }
    if (prefs != null && prefs.getInt("wakeModeVersion", 0) != 2) {
      check(prefs.edit().putBoolean("enabled", false).putInt("wakeModeVersion", 2).remove("count").remove("lastTranscriptAtMs").commit())
    }
    enabled = prefs?.getBoolean("enabled", false) == true
    baseUri = prefs?.getString("wakeBaseUri", null)
    if (!legacyCleanupStarted) {
      legacyCleanupStarted = true
      Thread({ runCatching { GlassesWakeStore.deleteLegacy(c) }.onFailure { main.post { failures++; legacyCleanupStarted = false } } }, "glasses-log-cleanup").start()
    }
    model = prefs?.getString("model", null)
    minimumVolume = (prefs?.getInt("minimumVolume", 0) ?: 0).coerceIn(0, 100)
    audioLevel = 0; levelAt = 0L
    status = if (enabled) "Open the app to resume listening" else "Listening is off"
  }
  fun snapshot(c: Context): Map<String, Any?> {
    load(c)
    val prefs = account?.let { GlassesWakeStore.prefs(c, it) }
    return mapOf("enabled" to enabled, "active" to active, "status" to status,
      "count" to (prefs?.getLong("wakeSavedCount", 0) ?: 0), "lastTranscriptAtMs" to prefs?.getLong("wakeLastSavedAtMs", 0),
      "pending" to (queue.size + if (busy) 1 else 0), "minimumVolume" to minimumVolume,
      "audioLevel" to if (active && SystemClock.elapsedRealtime() - levelAt < 600) audioLevel else 0,
      "capturing" to capturing, "savePending" to (account?.let { GlassesWakeStore.pendingCount(c, it) } ?: 0))
  }
  fun diagnostics(): Map<String, Any?> = mapOf("enabled" to enabled, "active" to active,
    "minimumVolume" to minimumVolume, "quietSegments" to quietSegments, "wakes" to wakes, "ledFailures" to ledFailures, "ledRequests" to ledRequests, "capturing" to capturing,
    "speechEvents" to speechEvents, "vadEnabled" to vadEnabled, "dropped" to dropped,
    "failures" to failures, "completed" to completed, "speechDurationMs" to processedMs,
    "transcriptionDurationMs" to inferenceMs, "pending" to queue.size + if (busy) 1 else 0)

  fun configure(c: Context, value: Boolean, modelUri: String?, storageUri: String? = null) {
    load(c); val who = checkNotNull(account) { "Sign in first" }
    if (value) {
      check(RuntimeGlasses.enabled(c) && RuntimeGlasses.signedIn(c)) { "Enable your glasses first" }
      check(permitted(c)) { "Allow microphone and Phone access to listen" }
      val path = checkNotNull(Uri.parse(modelUri).path)
      val file = File(path).canonicalFile
      check(file.name == "ggml-base.en.bin" && file.length() > 100_000_000 &&
        (file.path.startsWith(c.filesDir.canonicalPath + "/") || file.path.startsWith(c.cacheDir.canonicalPath + "/"))) {
        "Prepare the speech model before listening"
      }
      model = file.path
      baseUri = checkNotNull(storageUri) { "Choose your Digital Brain folder first" }
    }
    check(GlassesWakeStore.prefs(c, who).edit().putBoolean("enabled", value).putString("model", model).putString("wakeBaseUri", baseUri).commit())
    enabled = value
    if (value) resumeForeground(c) else { shutdown(discard = false); status = "Listening is off" }
    DigitalBrainRuntime.service?.refresh(forceNotification = true)
  }
  fun setMinimumVolume(c: Context, value: Int) {
    require(value in 0..100) { "Choose a volume between 0 and 100" }
    load(c)
    val who = checkNotNull(account) { "Sign in first" }
    check(GlassesWakeStore.prefs(c, who).edit().putInt("minimumVolume", value).commit())
    minimumVolume = value
  }
  private fun permitted(c: Context) = listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.READ_PHONE_STATE)
    .all { ContextCompat.checkSelfPermission(c, it) == PackageManager.PERMISSION_GRANTED }
  fun resumeForeground(c: Context) {
    load(c)
    if (!enabled || !RuntimeGlasses.enabled(c) || !RuntimeGlasses.signedIn(c) || !permitted(c) || model == null || baseUri == null) return
    if (model?.let { File(it).length() > 100_000_000 } != true) {
      shutdown(discard = true); status = "Turn listening on again to prepare speech recognition"; return
    }
    armed = true
    DigitalBrainRuntime.service?.refresh(forceNotification = true)
    reconcile(c)
    main.removeCallbacks(watchdog); main.postDelayed(watchdog, 1000)
  }
  fun reconcile(c: Context) {
    load(c)
    if (!enabled || !armed || transitioning) return
    if (!RuntimeGlasses.signedIn(c) || !RuntimeGlasses.enabled(c) || !permitted(c)) {
      shutdown(discard = true); status = "Open the app to resume listening"; return
    }
    if (File(c.filesDir.path).usableSpace < 16 * 1024 * 1024) {
      shutdown(discard = true); status = "Free some phone storage to resume listening"
      DigitalBrainRuntime.service?.refresh(forceNotification = true); return
    }
    val who = account
    if (who != null && GlassesWakeStore.pendingCount(c, who) >= 4 && !busy) {
      pause(); status = "Retry saving captures to resume listening"; return
    }
    val service = DigitalBrainRuntime.service
    if (service == null || service.foregroundTypes and ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE == 0) {
      pause(); status = "Open the app to resume listening"; return
    }
    val audio = c.getSystemService(AudioManager::class.java)
    @Suppress("DEPRECATION")
    val inCall = runCatching { c.getSystemService(TelephonyManager::class.java).callState != TelephonyManager.CALL_STATE_IDLE }.getOrDefault(true)
    callSources.entries.removeAll { SystemClock.elapsedRealtime() - it.value > 120_000 }
    val eligible = callSources.isEmpty() && RuntimeGlasses.alertsReady() && !GlassesRecording.busy && !inCall &&
      audio.mode == AudioManager.MODE_NORMAL && SystemClock.elapsedRealtime() >= blockedUntil
    if (!eligible) {
      pause()
      status = when {
        GlassesRecording.busy -> "Paused while recording"
        inCall || callSources.isNotEmpty() || audio.mode != AudioManager.MODE_NORMAL || SystemClock.elapsedRealtime() < blockedUntil -> "Paused for a call"
        else -> "Waiting for glasses"
      }
      return
    }
    if (active) return
    check(!GlassesRecording.capturing)
    val generation = ++sessionGeneration
    wakeSession = GlassesWakeSession(c, lastWake,
      { at -> main.post {
        if (active && generation == sessionGeneration) {
          lastWake = at; wakes++; capturing = true; status = "Capturing command"
          if (captureWake?.isHeld == true) captureWake?.release()
          captureWake = c.getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DigitalBrain:WakeCapture").apply { acquire(35_000L) }
          RuntimeGlasses.blinkWake(
            { if (generation == sessionGeneration) ledRequests++ },
            { if (generation == sessionGeneration) ledFailures++ })
        }
      } },
      { capture -> main.post {
        if (active && generation == sessionGeneration) {
          capturing = false; submit(capture); status = "Listening for Hey Brain"
          if (captureWake?.isHeld == true) captureWake?.release(); captureWake = null
        }
      } },
      { main.post { if (generation == sessionGeneration) quietSegments++ } },
      { main.post { if (active && generation == sessionGeneration) {
        shutdown(discard = true); failures++; status = "Turn listening off and on to try again"
        DigitalBrainRuntime.service?.refresh(forceNotification = true)
      } } })
    active = true; lastPcm = SystemClock.elapsedRealtime(); vadEnabled = null
    transitioning = true
    try {
      RuntimeGlasses.setListeningMic(true)
      status = "Listening for Hey Brain"
    } catch (_: Exception) { pause(); failures++; status = "Could not start listening" }
    finally { transitioning = false }
  }
  private val watchdog = object : Runnable {
    override fun run() {
      val c = context ?: return
      if (!foregroundOwned) return
      reconcile(c)
      // Missing PCM is expected during VAD silence; never restart the mic for silence.
      if (active && speechEvents == 0L && SystemClock.elapsedRealtime() - lastPcm > 60_000) status = "Listening for Hey Brain"
      main.postDelayed(this, 1000)
    }
  }
  fun speaking(value: Boolean) {
    if (!active) return
    speechEvents++
    val generation = ++speechGeneration
    wakeSession?.vad(value)
    if (value) {
      if (!capturing) status = "Listening for Hey Brain"
      if (captureWake?.isHeld != true) captureWake = context?.getSystemService(PowerManager::class.java)
        ?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DigitalBrain:SpeechCapture")?.apply { acquire(25_000L) }
    } else {
      if (!capturing) status = "Listening for Hey Brain"
      main.postDelayed({ if (!capturing && generation == speechGeneration) { if (captureWake?.isHeld == true) captureWake?.release(); captureWake = null } }, 1500)
    }
  }
  fun vadStatus(value: Boolean) { if (active) vadEnabled = value }
  fun pcm(event: MicPcmEvent) {
    if (!active) return
    if (!GlassesRecordingPolicy.validPcm(event.sampleRate, event.bitsPerSample, event.channels, event.encoding, event.pcm.size)) {
      pause(); failures++; status = "Could not read glasses audio"; return
    }
    lastPcm = SystemClock.elapsedRealtime()
    val level = GlassesAudioLevel.display(GlassesAudioLevel.db(event.pcm))
    if (lastPcm - levelWindowAt >= 300) { audioLevel = level; levelWindowAt = lastPcm }
    else audioLevel = maxOf(audioLevel, level)
    levelAt = lastPcm
    if (captureWake?.isHeld != true) captureWake = context?.getSystemService(PowerManager::class.java)
      ?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DigitalBrain:SpeechCapture")?.apply { acquire(25_000L) }
    wakeSession?.pcm(event.pcm, minimumVolume)
  }
  fun callNotification(key: String, interrupted: Boolean) {
    if (interrupted && armed && callSources.size < 32) {
      callSources[key] = SystemClock.elapsedRealtime(); callInterrupted()
    } else callSources.remove(key)
  }
  fun callInterrupted() {
    if (!armed) return
    blockedUntil = SystemClock.elapsedRealtime() + 5000
    pause(); status = "Paused for a call"
  }
  fun pause() {
    if (!active) return
    active = false; sessionGeneration++; capturing = false; wakeSession?.close(); wakeSession = null
    val wasTransitioning = transitioning; transitioning = true
    runCatching { RuntimeGlasses.setListeningMic(false) }
    transitioning = wasTransitioning
    if (captureWake?.isHeld == true) captureWake?.release(); captureWake = null
  }
  fun runtimeStopped() { shutdown(discard = true); status = if (enabled) "Open the app to resume listening" else "Listening is off" }
  private fun shutdown(discard: Boolean) {
    armed = false; pause(); keepWorker = false; callSources.clear(); main.removeCallbacks(watchdog)
    if (discard) { queue.clear(); engine?.abort() }
  }
  private fun submit(capture: WakeCapture) {
    val who = account ?: return; val path = model ?: return
    val folder = baseUri ?: return
    if (!queue.offer(Job(who, path, folder, capture))) { dropped++; status = "Captures are arriving faster than they can be saved"; return }
    keepWorker = true
    // The main-looper completion owns restart, even if the old thread has just exited.
    if (worker != null) return
    worker = Thread({ consume() }, "glasses-transcription").apply { start() }
  }
  private fun consume() {
    val c = context ?: return
    val whisper = GlassesWhisperBridge(); engine = whisper
    var loadedModel: String? = null
    var lastUsed = SystemClock.elapsedRealtime()
    try {
      while (keepWorker || queue.isNotEmpty()) {
        val job = queue.poll(1, java.util.concurrent.TimeUnit.SECONDS)
        if (job == null) {
          if (SystemClock.elapsedRealtime() - lastUsed > 60_000) { whisper.release(); loadedModel = null }
          continue
        }
        busy = true
        val wake = c.getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DigitalBrain:Transcription")
        wake.acquire(90_000L)
        val timeout = Runnable { whisper.abort() }
        var staged: File? = null
        try {
          if (!RuntimeGlasses.signedIn(c) || GlassesWakeStore.account(c) != job.account) continue
          staged = GlassesWakeStore.stage(c, job.account, job.capture, job.baseUri)
          if (loadedModel != job.model) { whisper.release(); whisper.load(job.model); loadedModel = job.model }
          val samples = FloatArray(job.capture.chunk.pcm.size / 2) { i ->
            val v = (job.capture.chunk.pcm[i * 2].toInt() and 255) or (job.capture.chunk.pcm[i * 2 + 1].toInt() shl 8)
            v.toShort().toFloat() / 32768f
          }
          main.postDelayed(timeout, 60_000)
          val started = SystemClock.elapsedRealtime()
          val text = whisper.transcribe(samples)
          val duration = SystemClock.elapsedRealtime() - started
          if (RuntimeGlasses.signedIn(c) && GlassesWakeStore.account(c) == job.account) {
            GlassesWakeStore.transcript(staged, text, duration, true)
            GlassesWakeStore.publish(c, job.account, staged)
          } else staged.deleteRecursively()
          main.post { completed++; processedMs += samples.size * 1000L / 16000; inferenceMs += duration }
        } catch (_: Exception) {
          staged?.let { if (!RuntimeGlasses.signedIn(c) || GlassesWakeStore.account(c) != job.account) it.deleteRecursively()
            else runCatching {
              val metadata = org.json.JSONObject(File(it, "capture.json").readText())
              if (metadata.getString("transcriptionStatus") == "pending") GlassesWakeStore.transcript(it, "", 0, false)
            }
          }
          main.post { failures++; status = "Could not finish a capture. Retry saving below." }
        } catch (_: LinkageError) {
          staged?.let { runCatching { GlassesWakeStore.transcript(it, "", 0, false) } }
          main.post { failures++; status = "Could not transcribe a capture. Retry saving below." }
        }
        finally {
          staged?.let { GlassesWakeStore.endProcessing(it) }
          lastUsed = SystemClock.elapsedRealtime(); busy = false; main.removeCallbacks(timeout)
          if (wake.isHeld) wake.release()
        }
      }
    } finally {
      whisper.release(); engine = null
      main.post { worker = null; if (queue.isNotEmpty()) { keepWorker = true; worker = Thread({ consume() }, "glasses-transcription").apply { start() } } }
    }
  }
  fun retrySaves(c: Context, baseUri: String) {
    check(RuntimeGlasses.signedIn(c)) { "Sign in first" }
    GlassesWakeStore.retry(c, checkNotNull(GlassesWakeStore.account(c)), baseUri)
  }
}
