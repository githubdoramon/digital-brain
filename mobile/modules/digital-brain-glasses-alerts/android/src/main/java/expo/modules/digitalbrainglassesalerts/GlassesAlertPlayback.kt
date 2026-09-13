package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRouting
import android.media.AudioTrack
import android.media.MediaPlayer
import android.net.Uri
import android.util.Log
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock

/**
 * Produces short, app-owned PCM tones. Notification content never reaches this
 * class: it only receives an alert kind after the native listener has filtered
 * by package name.
 */
internal object GlassesAlertPlayback {
  private const val NOTIFICATION_COOLDOWN_MS = 2_000L

  private val handler = Handler(Looper.getMainLooper())
  private var lastNotificationAlertAt = 0L
  private var callAlertActive = false
  private var audioFocusRequest: AudioFocusRequest? = null
  private var audioFocusManager: AudioManager? = null
  private var speechFocusRequest: AudioFocusRequest? = null
  private var speechFocusManager: AudioManager? = null
  private val routeListeners = mutableMapOf<AudioTrack, AudioRouting.OnRoutingChangedListener>()
  private var callContext: Context? = null
  private val callGate = object : Runnable {
    override fun run() {
      synchronized(this@GlassesAlertPlayback) {
        val context = callContext ?: return
        if (!GlassesAlertSettings.config(context).enabled || GlassesAlertSettings.isPhoneActivelyInUse(context)) {
          stopCallAlert()
        } else if (callAlertActive) handler.postDelayed(this, 300L)
      }
    }
  }
  private var callTrack: AudioTrack? = null
  private var notificationTrack: AudioTrack? = null
  private var previewTrack: AudioTrack? = null
  private var speechPlayer: MediaPlayer? = null
  private var speechCommandId: String? = null
  private var speechStartedAt = 0L
  private var speechFinishedCallback: ((String, Long?, String?) -> Unit)? = null
  private val cancelledSpeechCommands = LinkedHashSet<String>()
  private const val MAX_CANCELLED_SPEECH_COMMANDS = 32

  private const val SPEECH_TAG = "DigitalBrainSpeech"

  @Synchronized
  fun playNotificationAlert(context: Context): Boolean {
    val now = SystemClock.elapsedRealtime()
    if (
      GlassesAlertSettings.isPhoneActivelyInUse(context) ||
      callAlertActive ||
      now - lastNotificationAlertAt < NOTIFICATION_COOLDOWN_MS
    ) return false
    if (!playNotificationPreview(context)) return false
    lastNotificationAlertAt = now
    return true
  }

  @Synchronized
  fun startCallAlert(context: Context): Boolean {
    if (GlassesAlertSettings.isPhoneActivelyInUse(context) || callAlertActive) return false
    releaseTrack(notificationTrack)
    notificationTrack = null
    releaseTrack(previewTrack)
    previewTrack = null
    requestTransientFocus(context)
    val track = playTone(context, GlassesAlertTone.call(), loop = true) {
      stopCallAlert()
    }
    if (track == null) {
      releaseAudioFocus()
      return false
    }
    callTrack = track
    callAlertActive = true
    callContext = context.applicationContext
    handler.postDelayed(callGate, 300L)
    return true
  }

  @Synchronized
  fun stopCallAlert() {
    callAlertActive = false
    callContext?.let { DigitalBrainRuntime.setFeature(it, RuntimeFeature.CALL.key, false) }
    callContext = null
    handler.removeCallbacks(callGate)
    releaseTrack(callTrack)
    callTrack = null
    releaseAudioFocus()
  }

  @Synchronized
  fun isCallAlertActive(): Boolean = callAlertActive

  @Synchronized
  fun playCallPreview(context: Context): Boolean {
    if (callAlertActive) return false
    releaseTrack(previewTrack)
    requestTransientFocus(context)
    val track = playTone(context, GlassesAlertTone.call(), loop = true) ?: run {
      releaseAudioFocus()
      return false
    }
    previewTrack = track
    // Three complete cycles let the user hear the real ringing cadence.
    handler.postDelayed({
      synchronized(this@GlassesAlertPlayback) {
        if (previewTrack === track) {
          releaseTrack(track)
          previewTrack = null
          if (!callAlertActive && speechPlayer == null && notificationTrack == null) releaseAudioFocus()
        }
      }
    }, GlassesAlertTone.CALL_CYCLE_MS * 3L)
    return true
  }

  /** Explicit settings tests bypass the automatic unlocked-phone suppression. */
  @Synchronized
  fun playNotificationPreview(context: Context): Boolean {
    if (callAlertActive) return false
    releaseTrack(notificationTrack)
    requestTransientFocus(context)
    val track = playTone(context, GlassesAlertTone.notification()) ?: run {
      releaseAudioFocus()
      return false
    }
    notificationTrack = track
    handler.postDelayed({
      synchronized(this@GlassesAlertPlayback) {
        if (notificationTrack === track) {
          releaseTrack(track)
          notificationTrack = null
          if (!callAlertActive && speechPlayer == null && previewTrack == null) releaseAudioFocus()
        }
      }
    }, GlassesAlertTone.NOTIFICATION_MS + 180L)
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
    speechFocusManager = manager
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
        .setAudioAttributes(
          AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build(),
        )
        .build()
      speechFocusRequest = request
      manager.requestAudioFocus(request)
    } else {
      @Suppress("DEPRECATION")
      manager.requestAudioFocus(null, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
    }
  }

  private fun releaseSpeechFocus() {
    val manager = speechFocusManager ?: return
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      speechFocusRequest?.let(manager::abandonAudioFocusRequest)
    } else {
      @Suppress("DEPRECATION")
      manager.abandonAudioFocus(null)
    }
    speechFocusRequest = null
    speechFocusManager = null
  }

  private fun releaseTrack(track: AudioTrack?) {
    if (track == null) return
    routeListeners.remove(track)?.let(track::removeOnRoutingChangedListener)
    try {
      track.pause()
      track.flush()
    } catch (_: IllegalStateException) {
      // A route-loss callback may race terminal cleanup.
    }
    track.release()
  }

  private fun playTone(
    context: Context,
    pcm: ByteArray,
    loop: Boolean = false,
    onRouteLost: () -> Unit = {},
  ): AudioTrack? {
    val device = GlassesAlertSettings.findGlassesAudioDevice(context) ?: return null
    var track: AudioTrack? = null
    try {
      val created = AudioTrack.Builder()
        .setAudioAttributes(
          AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build(),
        )
        .setAudioFormat(
          AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(GlassesAlertTone.SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build(),
        )
        .setBufferSizeInBytes(pcm.size)
        .setTransferMode(AudioTrack.MODE_STATIC)
        .build()
      track = created
      if (!created.setPreferredDevice(device)) {
        releaseTrack(created)
        return null
      }
      if (created.write(pcm, 0, pcm.size, AudioTrack.WRITE_BLOCKING) != pcm.size) {
        releaseTrack(created)
        return null
      }
      if (loop && created.setLoopPoints(0, pcm.size / 2, -1) != AudioTrack.SUCCESS) {
        releaseTrack(created)
        return null
      }
      // Begin muted: a preferred device is not a routing guarantee. Unmute
      // only after Android reports that the actual route is these glasses.
      created.setVolume(0.0f)
      var routeVerified = false
      val routingListener = AudioRouting.OnRoutingChangedListener { routed ->
        synchronized(this@GlassesAlertPlayback) {
          if (!routeListeners.containsKey(created)) return@OnRoutingChangedListener
          if (routed.routedDevice?.id == device.id) {
            routeVerified = true
            created.setVolume(1.0f)
          } else if (routeVerified || routed.routedDevice != null) {
            try { created.pause() } catch (_: IllegalStateException) { }
            onRouteLost()
          }
        }
      }
      routeListeners[created] = routingListener
      created.addOnRoutingChangedListener(routingListener, handler)
      created.play()
      if (created.routedDevice?.id == device.id) {
        routeVerified = true
        created.setVolume(1.0f)
      }
      return created
    } catch (error: Exception) {
      releaseTrack(track)
      Log.w("GlassesAlerts", "Could not play alert on the glasses audio route", error)
      return null
    }
  }

  private fun requestTransientFocus(context: Context) {
    releaseAudioFocus()
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
