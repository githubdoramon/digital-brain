package expo.modules.digitalbrainruntime

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.telephony.PhoneStateListener
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import com.mentra.bluetoothsdk.MicPcmEvent
import kotlinx.coroutines.*
import java.io.File

/** Main-looper lifecycle; only the bounded writer/encoder touches recording bytes. No JS or network work. */
object GlassesRecording {
  private val main = Handler(Looper.getMainLooper())
  @Volatile private var state = GlassesRecordingState.IDLE
  @Volatile private var writer: GlassesPcmWriter? = null
  private var context: Context? = null
  private var startedAt = 0L
  @Volatile private var lastPcm = 0L
  private var reason = GlassesRecordingStop.USER
  @Volatile private var message: String? = null
  private var savedUri: String? = null
  private var initialized = false
  private var captureJob: RecordingSaveJob? = null
  private var captureGeneration = 0L
  private var wake: PowerManager.WakeLock? = null
  private var phone: TelephonyManager? = null
  private var modern: TelephonyCallback? = null
  private var modeListener: AudioManager.OnModeChangedListener? = null
  @Suppress("DEPRECATION") private val legacy = object : PhoneStateListener() {
    override fun onCallStateChanged(value: Int, ignored: String?) { if (value != TelephonyManager.CALL_STATE_IDLE) stop(GlassesRecordingStop.CALL) }
  }
  private fun directory(c: Context) = File(c.filesDir, "glasses-audio").apply { mkdirs() }
  fun initialize(c: Context) {
    context = c.applicationContext
    if (initialized) return
    initialized = true
    GlassesRecordingSaves.initialize(c)
  }
  val capturing get() = state == GlassesRecordingState.RECORDING
  val busy get() = capturing || state == GlassesRecordingState.STARTING
  fun snapshot(c: Context): Map<String, Any?> {
    initialize(c)
    return mapOf("state" to state.name, "startedAtMs" to startedAt,
      "durationMs" to GlassesRecordingPolicy.presentationUs(writer?.bytes ?: 0) / 1000,
      "message" to message, "savedUri" to savedUri, "ready" to RuntimeGlasses.alertsReady(),
      "canStart" to (!busy && GlassesRecordingSaves.hasRoom), "processing" to GlassesRecordingSaves.snapshot(),
      "libraryRevision" to GlassesRecordingSaves.revision)
  }
  fun diagnostics(): Map<String, Any?> = mapOf("state" to state.name, "stopReason" to reason.name,
    "capturedBytes" to (writer?.bytes ?: 0), "saves" to GlassesRecordingSaves.diagnostics())
  suspend fun start(c: Context, baseUri: String) {
    initialize(c)
    check(state == GlassesRecordingState.IDLE) { "Stop the current recording before starting another" }
    check(RuntimeGlasses.signedIn(c) && RuntimeGlasses.enabled(c) && RuntimeGlasses.alertsReady()) { "Connect your glasses before recording" }
    check(DigitalBrainRuntime.service != null) { "Glasses runtime is unavailable" }
    for (permission in listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.READ_PHONE_STATE)) {
      check(ContextCompat.checkSelfPermission(c, permission) == PackageManager.PERMISSION_GRANTED) { "Allow microphone and Phone access to record and detect call interruptions" }
    }
    val audio = c.getSystemService(AudioManager::class.java)
    check(audio.mode == AudioManager.MODE_NORMAL) { "End the call before recording" }
    @Suppress("DEPRECATION")
    check(c.getSystemService(TelephonyManager::class.java).callState == TelephonyManager.CALL_STATE_IDLE) { "End the call before recording" }
    state = GlassesRecordingState.STARTING
    val generation = ++captureGeneration
    GlassesListening.pause()
    val job = try { GlassesRecordingSaves.prepare(c, baseUri) }
      catch (e: Exception) {
        if (generation == captureGeneration) { state = GlassesRecordingState.IDLE; GlassesListening.reconcile(c) }
        throw e
      }
    if (generation != captureGeneration || !RuntimeGlasses.signedIn(c) || !RuntimeGlasses.alertsReady() || !DigitalBrainRuntime.activityVisible) {
      GlassesRecordingSaves.finishing(job, GlassesRecordingStop.START_FAILED)
      GlassesRecordingSaves.drained(job, 0)
      if (generation == captureGeneration) state = GlassesRecordingState.IDLE
      throw IllegalStateException("Open Recordings and connect your glasses to start")
    }
    GlassesRecordingPlayback.stop()
    captureJob = job; startedAt = job.startedAt; savedUri = null; message = null; reason = GlassesRecordingStop.USER
    lateinit var localWriter: GlassesPcmWriter
    localWriter = GlassesPcmWriter(File(job.dir, "capture.pcm"),
      { main.post { if (captureJob === job) stop(GlassesRecordingStop.STORAGE) } },
      { main.post {
        if (captureJob === job) stop(GlassesRecordingStop.STORAGE)
        GlassesRecordingSaves.drained(job, localWriter.bytes)
      } })
    writer = localWriter
    state = GlassesRecordingState.RECORDING
    GlassesListening.pause()
    lastPcm = SystemClock.elapsedRealtime()
    var writerStarted = false
    try {
      writer!!.start(); writerStarted = true
      // Promotion happens while the interactive Activity is visible, before enabling microphone capture.
      DigitalBrainRuntime.service!!.refresh(forceNotification = true)
      check(DigitalBrainRuntime.service!!.foregroundTypes and android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE != 0)
      wake = c.getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DigitalBrain:GlassesRecording").apply { acquire() }
      watchCalls(c)
      check(capturing && audio.mode == AudioManager.MODE_NORMAL) { "Call interrupted recording" }
      RuntimeGlasses.setRecordingMic(true)
      main.postDelayed(watchdog, 1000)
    } catch (e: Exception) {
      stop(GlassesRecordingStop.START_FAILED)
      // A failure before the writer starts still requires native save/recovery.
      if (!writerStarted) GlassesRecordingSaves.drained(job, 0)
      throw IllegalStateException("Could not start glasses recording", e)
    }
  }
  fun pcm(event: MicPcmEvent) {
    if (!capturing) return
    val job = captureJob ?: return
    if (!GlassesRecordingPolicy.validPcm(event.sampleRate, event.bitsPerSample, event.channels, event.encoding, event.pcm.size)) {
      main.post { if (captureJob === job) stop(GlassesRecordingStop.FORMAT) }; return
    }
    lastPcm = SystemClock.elapsedRealtime()
    if (writer?.offer(event.pcm) != true) main.post { if (captureJob === job) stop(GlassesRecordingStop.BACKPRESSURE) }
  }
  private val watchdog = object : Runnable {
    override fun run() {
      if (!capturing) return
      val c = context ?: return
      val mode = c.getSystemService(AudioManager::class.java).mode
      if (mode == AudioManager.MODE_IN_CALL || mode == AudioManager.MODE_IN_COMMUNICATION) stop(GlassesRecordingStop.CALL)
      else if (directory(c).usableSpace < 8 * 1024 * 1024) stop(GlassesRecordingStop.STORAGE)
      else if (SystemClock.elapsedRealtime() - lastPcm > 8000) stop(GlassesRecordingStop.AUDIO_LOST)
      else main.postDelayed(this, 1000)
    }
  }
  fun stop(value: GlassesRecordingStop = GlassesRecordingStop.USER) {
    if (state == GlassesRecordingState.STARTING) {
      captureGeneration++; state = GlassesRecordingState.IDLE
      return
    }
    if (!capturing) return
    val job = captureJob ?: return
    val completedWriter = writer
    // Detach capture first. A prior writer's callbacks cannot touch a subsequent recording.
    state = GlassesRecordingState.STARTING; captureGeneration++; captureJob = null; writer = null
    reason = value; startedAt = 0; message = value.description
    GlassesRecordingSaves.finishing(job, value, completedWriter?.bytes ?: 0)
    runCatching { RuntimeGlasses.setRecordingMic(false) }
    state = GlassesRecordingState.IDLE
    main.removeCallbacks(watchdog); unwatchCalls()
    completedWriter?.finish()
    wake?.let { if (it.isHeld) it.release() }; wake = null
    DigitalBrainRuntime.service?.refresh(forceNotification = true)
    context?.let { GlassesListening.reconcile(it) }
  }
  suspend fun retry(c: Context, baseUri: String, id: String?) {
    initialize(c)
    GlassesRecordingSaves.retry(c, baseUri, id)
  }
  private fun watchCalls(c: Context) {
    val manager = c.getSystemService(TelephonyManager::class.java); phone = manager
    val generation = captureGeneration
    if (Build.VERSION.SDK_INT >= 31) {
      val callback = object : TelephonyCallback(), TelephonyCallback.CallStateListener {
        override fun onCallStateChanged(value: Int) { if (generation == captureGeneration && value != TelephonyManager.CALL_STATE_IDLE) stop(GlassesRecordingStop.CALL) }
      }
      modern = callback; manager.registerTelephonyCallback(ContextCompat.getMainExecutor(c), callback)
      val listener = AudioManager.OnModeChangedListener { mode -> if (generation == captureGeneration && (mode == AudioManager.MODE_IN_CALL || mode == AudioManager.MODE_IN_COMMUNICATION)) stop(GlassesRecordingStop.CALL) }
      modeListener = listener; c.getSystemService(AudioManager::class.java).addOnModeChangedListener(ContextCompat.getMainExecutor(c), listener)
    } else {
      @Suppress("DEPRECATION") manager.listen(legacy, PhoneStateListener.LISTEN_CALL_STATE)
    }
  }
  private fun unwatchCalls() {
    if (Build.VERSION.SDK_INT >= 31) {
      runCatching { modern?.let { phone?.unregisterTelephonyCallback(it) } }
      runCatching { modeListener?.let { context?.getSystemService(AudioManager::class.java)?.removeOnModeChangedListener(it) } }
    } else {
      @Suppress("DEPRECATION")
      runCatching { phone?.listen(legacy, PhoneStateListener.LISTEN_NONE) }
    }
    phone = null; modern = null; modeListener = null
  }
}
