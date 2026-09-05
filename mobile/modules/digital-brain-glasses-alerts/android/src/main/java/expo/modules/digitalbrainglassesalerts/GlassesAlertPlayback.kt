package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.media.MediaPlayer
import android.net.Uri
import android.util.Log
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import kotlin.math.PI
import kotlin.math.sin

/**
 * Produces short, app-owned PCM tones. Notification content never reaches this
 * class: it only receives an alert kind after the native listener has filtered
 * by package name.
 */
internal object GlassesAlertPlayback {
  private const val SAMPLE_RATE = 48_000
  private const val NOTIFICATION_COOLDOWN_MS = 2_000L
  private const val CALL_CYCLE_MS = 2_400L

  private val handler = Handler(Looper.getMainLooper())
  private var lastNotificationAlertAt = 0L
  private var callAlertActive = false
  private var audioFocusRequest: AudioFocusRequest? = null
  private var audioFocusManager: AudioManager? = null
  private var activeContext: Context? = null
  private var speechPlayer: MediaPlayer? = null
  private var speechCommandId: String? = null
  private var speechStartedAt = 0L
  private var speechFinishedCallback: ((String, Long?, String?) -> Unit)? = null
  private val cancelledSpeechCommands = LinkedHashSet<String>()
  private const val MAX_CANCELLED_SPEECH_COMMANDS = 32

  private const val SPEECH_TAG = "DigitalBrainSpeech"

  private val callCycle = object : Runnable {
    override fun run() {
      if (!callAlertActive) return
      playTone(740, 180, activeContext)
      handler.postDelayed({ if (callAlertActive) playTone(980, 180, activeContext) }, 330)
      handler.postDelayed(this, CALL_CYCLE_MS)
    }
  }

  fun playNotificationAlert(context: Context): Boolean {
    val now = SystemClock.elapsedRealtime()
    if (
      GlassesAlertSettings.isPhoneActivelyInUse(context) ||
      callAlertActive ||
      now - lastNotificationAlertAt < NOTIFICATION_COOLDOWN_MS
    ) return false
    if (GlassesAlertSettings.findGlassesAudioDevice(context) == null) return false
    lastNotificationAlertAt = now
    requestTransientFocus(context)
    playTone(1040, 75, context)
    handler.postDelayed({ playTone(1560, 95, context) }, 145)
    handler.postDelayed({ if (!callAlertActive) releaseAudioFocus() }, 420)
    return true
  }

  fun startCallAlert(context: Context): Boolean {
    if (
      GlassesAlertSettings.isPhoneActivelyInUse(context) ||
      callAlertActive ||
      GlassesAlertSettings.findGlassesAudioDevice(context) == null
    ) return false
    callAlertActive = true
    activeContext = context.applicationContext
    requestTransientFocus(context)
    callCycle.run()
    return true
  }

  fun stopCallAlert() {
    callAlertActive = false
    handler.removeCallbacks(callCycle)
    activeContext = null
    releaseAudioFocus()
  }

  fun isCallAlertActive(): Boolean = callAlertActive

  fun playCallPreview(context: Context): Boolean {
    if (GlassesAlertSettings.findGlassesAudioDevice(context) == null) return false
    requestTransientFocus(context)
    playTone(740, 180, context)
    handler.postDelayed({ playTone(980, 180, context) }, 330)
    handler.postDelayed(::releaseAudioFocus, 750)
    return true
  }

  /**
   * An explicit settings test is intentionally not subject to the normal
   * phone-use suppression. The user is looking at the phone precisely because
   * they asked to verify the route; production notification alerts retain the
   * privacy-preserving unlocked-phone gate above.
   */
  fun playNotificationPreview(context: Context): Boolean {
    if (GlassesAlertSettings.findGlassesAudioDevice(context) == null) return false
    requestTransientFocus(context)
    playTone(1040, 75, context)
    handler.postDelayed({ playTone(1560, 95, context) }, 145)
    handler.postDelayed({ if (!callAlertActive) releaseAudioFocus() }, 420)
    return true
  }

  /**
   * Starts app-private speech playback on the remembered Mentra Bluetooth
   * output. Completion is reported through the Expo event callback so the JS
   * command state machine can resume wake listening only after all audio has
   * drained. No audio bytes cross the React Native bridge.
   */
  @Synchronized
  fun playSpeechAudio(
    context: Context,
    commandId: String,
    fileUri: String,
    onFinished: (String, Long?, String?) -> Unit,
  ): Map<String, Any> {
    stopSpeechAudio(null)
    synchronized(this) {
      if (cancelledSpeechCommands.remove(commandId)) return mapOf("started" to false)
    }
    val device = GlassesAlertSettings.findGlassesAudioDevice(context)
      ?: return mapOf("started" to false)
    val parsedUri = Uri.parse(fileUri)
    if (parsedUri.scheme !in setOf("file", "content")) {
      return mapOf("started" to false)
    }
    val player = try {
      MediaPlayer().apply {
        setAudioAttributes(
          AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build(),
        )
        setDataSource(context, parsedUri)
        if (!setPreferredDevice(device)) {
          release()
          return mapOf("started" to false)
        }
        setOnPreparedListener { prepared ->
          if (speechPlayer !== prepared || speechCommandId != commandId) return@setOnPreparedListener
          requestSpeechFocus(context)
          speechStartedAt = SystemClock.elapsedRealtime()
          prepared.start()
        }
        setOnCompletionListener { completed ->
          val duration = SystemClock.elapsedRealtime() - speechStartedAt
          finishSpeechPlayback(completed, commandId, "completed", duration, null, onFinished)
        }
        setOnErrorListener { failed, what, extra ->
          finishSpeechPlayback(
            failed,
            commandId,
            "error",
            null,
            "MediaPlayer error ($what/$extra)",
            onFinished,
          )
          true
        }
        prepareAsync()
      }
    } catch (error: Exception) {
      Log.w(SPEECH_TAG, "Speech playback setup failed", error)
      releaseSpeechFocus()
      onFinished("error", null, error.message ?: "Speech playback setup failed.")
      return mapOf("started" to false)
    }
    speechPlayer = player
    speechCommandId = commandId
    speechFinishedCallback = onFinished
    return mapOf("started" to true)
  }

  @Synchronized
  fun stopSpeechAudio(commandId: String?): Boolean {
    if (commandId != null && speechCommandId == null) {
      if (cancelledSpeechCommands.size >= MAX_CANCELLED_SPEECH_COMMANDS) {
        cancelledSpeechCommands.iterator().next().let(cancelledSpeechCommands::remove)
      }
      cancelledSpeechCommands.add(commandId)
    }
    if (commandId != null && speechCommandId != null && commandId != speechCommandId) return false
    val player = speechPlayer ?: return false
    val stoppedCommandId = speechCommandId
    val callback = speechFinishedCallback
    try {
      if (player.isPlaying) player.stop()
    } catch (_: IllegalStateException) {
      // A prepareAsync callback can race with an explicit stop.
    }
    player.release()
    speechPlayer = null
    speechCommandId = null
    speechFinishedCallback = null
    speechStartedAt = 0L
    releaseSpeechFocus()
    if (stoppedCommandId != null) callback?.invoke("stopped", null, "Speech playback stopped.")
    return stoppedCommandId != null
  }

  private fun finishSpeechPlayback(
    player: MediaPlayer,
    commandId: String,
    status: String,
    durationMs: Long?,
    error: String?,
    onFinished: (String, Long?, String?) -> Unit,
  ) {
    synchronized(this) {
      if (speechPlayer !== player || speechCommandId != commandId) return
      try {
        player.release()
      } catch (_: Exception) {
        // Release is best effort after a terminal callback.
      }
      speechPlayer = null
      speechCommandId = null
      speechFinishedCallback = null
      speechStartedAt = 0L
      releaseSpeechFocus()
    }
    onFinished(status, durationMs, error)
  }

  private fun requestSpeechFocus(context: Context) {
    val manager = context.getSystemService(AudioManager::class.java) ?: return
    audioFocusManager = manager
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
        .setAudioAttributes(
          AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build(),
        )
        .build()
      audioFocusRequest = request
      manager.requestAudioFocus(request)
    } else {
      @Suppress("DEPRECATION")
      manager.requestAudioFocus(null, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
    }
  }

  private fun releaseSpeechFocus() {
    releaseAudioFocus()
  }

  private fun playTone(frequencyHz: Int, durationMs: Int, suppliedContext: Context? = null) {
    val context = suppliedContext ?: return
    val device = GlassesAlertSettings.findGlassesAudioDevice(context) ?: return
    val sampleCount = SAMPLE_RATE * durationMs / 1_000
    val pcm = ByteArray(sampleCount * 2)
    for (index in 0 until sampleCount) {
      val envelope = when {
        index < SAMPLE_RATE / 250 -> index.toDouble() / (SAMPLE_RATE / 250)
        index > sampleCount - SAMPLE_RATE / 200 -> (sampleCount - index).toDouble() / (SAMPLE_RATE / 200)
        else -> 1.0
      }.coerceIn(0.0, 1.0)
      val value = (sin(2.0 * PI * frequencyHz * index / SAMPLE_RATE) * envelope * Short.MAX_VALUE * 0.22)
        .toInt()
        .toShort()
      pcm[index * 2] = (value.toInt() and 0xff).toByte()
      pcm[index * 2 + 1] = ((value.toInt() shr 8) and 0xff).toByte()
    }

    val track = AudioTrack.Builder()
      .setAudioAttributes(
        AudioAttributes.Builder()
          .setUsage(AudioAttributes.USAGE_MEDIA)
          .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
          .build(),
      )
      .setAudioFormat(
        AudioFormat.Builder()
          .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
          .setSampleRate(SAMPLE_RATE)
          .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
          .build(),
      )
      .setBufferSizeInBytes(pcm.size)
      .setTransferMode(AudioTrack.MODE_STATIC)
      .build()
    track.preferredDevice = device
    track.write(pcm, 0, pcm.size, AudioTrack.WRITE_BLOCKING)
    track.play()
    handler.postDelayed({ track.release() }, durationMs.toLong() + 180L)
  }

  private fun requestTransientFocus(context: Context) {
    val manager = context.getSystemService(AudioManager::class.java) ?: return
    audioFocusManager = manager
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        .setAudioAttributes(
          AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build(),
        )
        .build()
      audioFocusRequest = request
      manager.requestAudioFocus(request)
    } else {
      @Suppress("DEPRECATION")
      manager.requestAudioFocus(null, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
    }
  }

  private fun releaseAudioFocus() {
    val manager = audioFocusManager ?: return
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      audioFocusRequest?.let(manager::abandonAudioFocusRequest)
    } else {
      @Suppress("DEPRECATION")
      manager.abandonAudioFocus(null)
    }
    audioFocusRequest = null
    audioFocusManager = null
  }
}
