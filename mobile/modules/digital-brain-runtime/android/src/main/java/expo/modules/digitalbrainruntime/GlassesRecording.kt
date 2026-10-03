package expo.modules.digitalbrainruntime

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.telephony.PhoneStateListener
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import android.util.AtomicFile
import androidx.core.content.ContextCompat
import androidx.documentfile.provider.DocumentFile
import com.mentra.bluetoothsdk.MicPcmEvent
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.File

/** Main-looper lifecycle; only the bounded writer/encoder touches recording bytes. No JS or network work. */
object GlassesRecording {
  private val main = Handler(Looper.getMainLooper())
  private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
  @Volatile private var state = GlassesRecordingState.IDLE
  @Volatile private var writer: GlassesPcmWriter? = null
  private var context: Context? = null
  private var startedAt = 0L
  @Volatile private var lastPcm = 0L
  private var reason = GlassesRecordingStop.USER
  private var message: String? = null
  private var savedUri: String? = null
  private var initialized = false
  private var wake: PowerManager.WakeLock? = null
  private var phone: TelephonyManager? = null
  private var modern: TelephonyCallback? = null
  private var modeListener: AudioManager.OnModeChangedListener? = null
  @Suppress("DEPRECATION") private val legacy = object : PhoneStateListener() {
    override fun onCallStateChanged(value: Int, ignored: String?) { if (value != TelephonyManager.CALL_STATE_IDLE) stop(GlassesRecordingStop.CALL) }
  }
  private fun directory(c: Context) = File(c.filesDir, "glasses-audio").apply { mkdirs() }
  private fun journal(c: Context) = AtomicFile(File(directory(c), "current.json"))
  private fun writeJournal(c: Context, value: JSONObject) {
    val file = journal(c); val output = file.startWrite()
    try { output.write(value.toString().toByteArray()); file.finishWrite(output) }
    catch (e: Exception) { file.failWrite(output); throw e }
  }
  fun initialize(c: Context) {
    context = c.applicationContext
    if (initialized) return
    initialized = true
    if (journal(c).baseFile.exists()) {
      reason = GlassesRecordingStop.RECOVERED; state = GlassesRecordingState.SAVING
      save()
    }
  }
  val capturing get() = state == GlassesRecordingState.RECORDING
  val busy get() = state == GlassesRecordingState.RECORDING || state == GlassesRecordingState.SAVING
  fun snapshot(c: Context): Map<String, Any?> {
    initialize(c)
    return mapOf("state" to state.name, "startedAtMs" to startedAt,
      "durationMs" to GlassesRecordingPolicy.presentationUs(writer?.bytes ?: 0) / 1000,
      "message" to message, "savedUri" to savedUri, "ready" to RuntimeGlasses.alertsReady())
  }
  fun diagnostics(): Map<String, Any?> = mapOf("state" to state.name, "stopReason" to reason.name,
    "capturedBytes" to (writer?.bytes ?: 0), "savePending" to (state == GlassesRecordingState.SAVE_FAILED))
  fun start(c: Context, baseUri: String) {
    initialize(c)
    check(state == GlassesRecordingState.IDLE) { "Save the pending recording before starting another" }
    check(RuntimeGlasses.signedIn(c) && RuntimeGlasses.enabled(c) && RuntimeGlasses.alertsReady()) { "Connect your glasses before recording" }
    check(DigitalBrainRuntime.service != null) { "Glasses runtime is unavailable" }
    for (permission in listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.READ_PHONE_STATE)) {
      check(ContextCompat.checkSelfPermission(c, permission) == PackageManager.PERMISSION_GRANTED) { "Allow microphone and Phone access to record and detect call interruptions" }
    }
    val audio = c.getSystemService(AudioManager::class.java)
    check(audio.mode == AudioManager.MODE_NORMAL) { "End the call before recording" }
    @Suppress("DEPRECATION")
    check(c.getSystemService(TelephonyManager::class.java).callState == TelephonyManager.CALL_STATE_IDLE) { "End the call before recording" }
    val base = DocumentFile.fromTreeUri(c, Uri.parse(baseUri))
    check(base?.canWrite() == true) { "Choose a writable Digital Brain folder" }
    val dir = directory(c); check(dir.usableSpace > 16 * 1024 * 1024) { "Not enough space for a recording" }
    GlassesRecordingPlayback.stop()
    startedAt = System.currentTimeMillis(); savedUri = null; message = null; reason = GlassesRecordingStop.USER
    val name = "Glasses-${java.text.SimpleDateFormat("yyyy-MM-dd-HH-mm-ss", java.util.Locale.US).format(java.util.Date(startedAt))}-${java.util.UUID.randomUUID().toString().take(6)}.m4a"
    writeJournal(c, JSONObject().put("baseUri", baseUri).put("name", name))
    File(dir, "capture.pcm").delete(); File(dir, "output.m4a").delete()
    writer = GlassesPcmWriter(File(dir, "capture.pcm"),
      { main.post { stop(GlassesRecordingStop.STORAGE) } },
      { main.post { if (state == GlassesRecordingState.RECORDING) stop(GlassesRecordingStop.STORAGE); save() } })
    state = GlassesRecordingState.RECORDING
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
      if (!writerStarted) save()
      throw IllegalStateException("Could not start glasses recording", e)
    }
  }
  fun pcm(event: MicPcmEvent) {
    if (!capturing) return
    if (!GlassesRecordingPolicy.validPcm(event.sampleRate, event.bitsPerSample, event.channels, event.encoding, event.pcm.size)) {
      main.post { stop(GlassesRecordingStop.FORMAT) }; return
    }
    lastPcm = SystemClock.elapsedRealtime()
    if (writer?.offer(event.pcm) != true) main.post { stop(GlassesRecordingStop.BACKPRESSURE) }
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
    if (!capturing) return
    state = GlassesRecordingState.SAVING; reason = value; message = value.description
    runCatching { RuntimeGlasses.setRecordingMic(false) }
    main.removeCallbacks(watchdog); unwatchCalls()
    writer?.finish()
    DigitalBrainRuntime.service?.refresh(forceNotification = true)
  }
  private var saving = false
  private fun save() {
    if (saving || state != GlassesRecordingState.SAVING) return
    val c = context ?: return
    saving = true
    // Keep the CPU awake until local encoding and the verified SAF copy finish.
    if (wake?.isHeld != true) wake = c.getSystemService(PowerManager::class.java)
      .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DigitalBrain:GlassesSave").apply { acquire(30 * 60_000L) }
    scope.launch {
      var result: String? = null
      var failed = false
      try {
        val metadata = JSONObject(journal(c).openRead().use { String(it.readBytes()) })
        val dir = directory(c); val pcm = File(dir, "capture.pcm"); val output = File(dir, "output.m4a")
        if (pcm.length() % 2 != 0L) java.io.RandomAccessFile(pcm, "rw").use { it.setLength(pcm.length() - 1) }
        if (pcm.length() > 0) {
          if (!output.exists() || output.length() == 0L) {
            val temp = File(dir, "encoding.m4a"); temp.delete()
            GlassesM4aEncoder.encode(pcm, temp)
            check(temp.renameTo(output))
          }
          val base = checkNotNull(DocumentFile.fromTreeUri(c, Uri.parse(metadata.getString("baseUri"))))
          check(base.canWrite()) { "Folder permission unavailable" }
          val folder = base.findFile("Recordings") ?: base.createDirectory("Recordings")
          check(folder?.isDirectory == true && folder.canWrite())
          val prior = metadata.optString("targetUri").takeIf { it.isNotBlank() }?.let { DocumentFile.fromSingleUri(c, Uri.parse(it)) }
          val target = prior?.takeIf { it.exists() } ?: checkNotNull(folder.createFile("audio/mp4", metadata.getString("name")))
          metadata.put("targetUri", target.uri.toString()); writeJournal(c, metadata)
          val copied = output.inputStream().use { input ->
            checkNotNull(c.contentResolver.openOutputStream(target.uri, "wt")).use { out -> input.copyTo(out).also { out.flush() } }
          }
          check(copied == output.length() && target.length() == copied && copied > 0)
          result = target.uri.toString()
        }
        journal(c).delete(); pcm.delete(); output.delete(); File(dir, "encoding.m4a").delete()
      } catch (_: Exception) { failed = true }
      finally {
        main.post {
          saving = false; writer = null
          state = if (failed) GlassesRecordingState.SAVE_FAILED else GlassesRecordingState.IDLE
          savedUri = result
          message = if (failed) "Could not save to Recordings. Audio is retained on this phone; retry saving." else if (result == null) "No audio received; no recording saved" else reason.description
          wake?.let { if (it.isHeld) it.release() }; wake = null
          DigitalBrainRuntime.service?.refresh(forceNotification = true)
        }
      }
    }
  }
  fun retry(c: Context, baseUri: String) {
    initialize(c); check(state == GlassesRecordingState.SAVE_FAILED)
    val metadata = JSONObject(journal(c).openRead().use { String(it.readBytes()) })
    if (metadata.getString("baseUri") != baseUri) metadata.remove("targetUri")
    metadata.put("baseUri", baseUri); writeJournal(c, metadata)
    state = GlassesRecordingState.SAVING; save()
  }
  private fun watchCalls(c: Context) {
    val manager = c.getSystemService(TelephonyManager::class.java); phone = manager
    if (Build.VERSION.SDK_INT >= 31) {
      val callback = object : TelephonyCallback(), TelephonyCallback.CallStateListener {
        override fun onCallStateChanged(value: Int) { if (value != TelephonyManager.CALL_STATE_IDLE) stop(GlassesRecordingStop.CALL) }
      }
      modern = callback; manager.registerTelephonyCallback(ContextCompat.getMainExecutor(c), callback)
      val listener = AudioManager.OnModeChangedListener { mode -> if (mode == AudioManager.MODE_IN_CALL || mode == AudioManager.MODE_IN_COMMUNICATION) stop(GlassesRecordingStop.CALL) }
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
