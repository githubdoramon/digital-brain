package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
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
  private const val SPEECH_ROUTE_TIMEOUT_MS = 1_500L

  data class SpeechPlaybackTelemetry(
    val expectedDeviceId: Int?,
    val expectedDeviceName: String?,
    val expectedDeviceType: Int?,
    val routedDeviceId: Int?,
    val routedDeviceName: String?,
    val routedDeviceType: Int?,
    val routeVerified: Boolean,
    val audioFocusResult: Int?,
    val audioFocusGranted: Boolean?,
    val runtimeForegroundTypes: Int,
    val activityVisible: Boolean,
    val outputStreamVolume: Int?,
    val outputStreamVolumeMax: Int?,
    val outputStreamMuted: Boolean?,
    val playerDurationMs: Int?,
    val playerPositionMs: Int?,
    val playerIsPlaying: Boolean?,
    val playerAudioSessionId: Int?,
    val playerGain: Float,
  )

  data class SpeechPlaybackResult(
    val status: String,
    val durationMs: Long? = null,
    val error: String? = null,
    val telemetry: SpeechPlaybackTelemetry,
  )

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
  private var speechExpectedDeviceId: Int? = null
  private var speechExpectedDeviceName: String? = null
  private var speechExpectedDeviceType: Int? = null
  private var speechRoutedDeviceId: Int? = null
  private var speechRoutedDeviceName: String? = null
  private var speechRoutedDeviceType: Int? = null
  private var speechRouteVerified = false
  private var speechAudioFocusResult: Int? = null
  private var speechRouteListener: AudioRouting.OnRoutingChangedListener? = null
  private var speechRouteTimeout: Runnable? = null
  private var speechProgressRunnable: Runnable? = null
  private var speechPlayerGain = 0.0f
  private var speechFinishedCallback: ((SpeechPlaybackResult) -> Unit)? = null
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
    onStarted: (SpeechPlaybackTelemetry) -> Unit,
    onProgress: (SpeechPlaybackTelemetry) -> Unit,
    onFinished: (SpeechPlaybackResult) -> Unit,
  ): Map<String, Any> {
    stopSpeechAudio(null)
    synchronized(this) {
      if (cancelledSpeechCommands.remove(commandId)) {
        return mapOf("started" to false, "reason" to "command_cancelled_before_playback")
      }
    }
    val configuredDeviceName = GlassesAlertSettings.config(context).expectedAudioDeviceName
    val device = GlassesAlertSettings.findGlassesAudioDevice(context)
      ?: run {
        val availableOutputs = availableBluetoothOutputs(context)
        Log.w(
          SPEECH_TAG,
          "expected_device_unavailable command_id=$commandId expected_device_name=${configuredDeviceName ?: "none"} available_outputs=$availableOutputs runtime_foreground_types=${DigitalBrainRuntime.service?.foregroundTypes ?: 0} activity_visible=${DigitalBrainRuntime.activityVisible}",
        )
        return mapOf(
          "started" to false,
          "reason" to "expected_glasses_audio_device_unavailable",
          "expectedDeviceName" to configuredDeviceName.orEmpty(),
          "availableOutputs" to availableOutputs,
        )
      }
    val parsedUri = Uri.parse(fileUri)
    if (parsedUri.scheme !in setOf("file", "content")) {
      return mapOf("started" to false, "reason" to "unsupported_audio_file_uri")
    }
    val player = MediaPlayer()
    try {
      player.apply {
        setAudioAttributes(
          AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build(),
        )
        setDataSource(context, parsedUri)
        if (!setPreferredDevice(device)) {
          release()
          return mapOf(
            "started" to false,
            "reason" to "glasses_audio_route_preference_rejected",
            "expectedDeviceId" to device.id,
            "expectedDeviceName" to device.productName?.toString().orEmpty(),
            "expectedDeviceType" to device.type,
          )
        }
        setVolume(0.0f, 0.0f)
        setOnPreparedListener { prepared ->
          synchronized(this@GlassesAlertPlayback) {
            if (speechPlayer !== prepared || speechCommandId != commandId) return@synchronized
            val focusResult = requestSpeechFocus(context)
            speechAudioFocusResult = focusResult
            if (focusResult != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
              Log.w(SPEECH_TAG, "audio_focus_not_granted command_id=$commandId result=$focusResult expected_device_id=${device.id} expected_device_name=${device.productName} expected_device_type=${device.type} activity_visible=${DigitalBrainRuntime.activityVisible} foreground_types=${DigitalBrainRuntime.service?.foregroundTypes ?: 0}")
              finishSpeechPlayback(
                prepared,
                commandId,
                "error",
                null,
                "Audio focus was not granted (request result $focusResult).",
                onFinished,
              )
              return@synchronized
            }
            speechStartedAt = SystemClock.elapsedRealtime()
            val listener = AudioRouting.OnRoutingChangedListener { _ ->
              synchronized(this@GlassesAlertPlayback) {
                if (speechPlayer !== prepared || speechCommandId != commandId) return@OnRoutingChangedListener
                handleSpeechRouteChange(prepared, device, commandId, onStarted, onProgress, onFinished)
              }
            }
            speechRouteListener = listener
            addOnRoutingChangedListener(listener, handler)
            try {
              prepared.start()
            } catch (error: Exception) {
              finishSpeechPlayback(
                prepared,
                commandId,
                "error",
                null,
                error.message ?: "MediaPlayer could not start.",
                onFinished,
              )
              return@synchronized
            }
            handleSpeechRouteChange(prepared, device, commandId, onStarted, onProgress, onFinished)
            if (!speechRouteVerified) {
              val timeout = Runnable {
                synchronized(this@GlassesAlertPlayback) {
                  if (speechPlayer === prepared && speechCommandId == commandId && !speechRouteVerified) {
                    val routed = currentRoutedDevice(prepared)
                    val routedName = routed?.productName?.toString() ?: "none"
                    val routedId = routed?.id?.toString() ?: "none"
                    Log.w(SPEECH_TAG, "route_verification_timeout command_id=$commandId expected_device_id=${device.id} expected_device_name=${device.productName} expected_device_type=${device.type} routed_device_id=$routedId routed_device_name=$routedName routed_device_type=${routed?.type ?: "none"} audio_focus_result=$speechAudioFocusResult")
                    finishSpeechPlayback(
                      prepared,
                      commandId,
                      "error",
                      null,
                      "Glasses audio route was not confirmed within ${SPEECH_ROUTE_TIMEOUT_MS} ms.",
                      onFinished,
                    )
                  }
                }
              }
              speechRouteTimeout = timeout
              handler.postDelayed(timeout, SPEECH_ROUTE_TIMEOUT_MS)
            }
          }
        }
        setOnCompletionListener { completed ->
          synchronized(this@GlassesAlertPlayback) {
            if (!speechRouteVerified) {
              finishSpeechPlayback(
                completed,
                commandId,
                "error",
                null,
                "Speech playback completed before the glasses audio route was confirmed.",
                onFinished,
              )
            } else {
              val duration = SystemClock.elapsedRealtime() - speechStartedAt
              finishSpeechPlayback(completed, commandId, "completed", duration, null, onFinished)
            }
          }
        }
        setOnErrorListener { failed, what, extra ->
          synchronized(this@GlassesAlertPlayback) {
            finishSpeechPlayback(
              failed,
              commandId,
              "error",
              null,
              "MediaPlayer error ($what/$extra)",
              onFinished,
            )
          }
          true
        }
      }
      speechPlayer = player
      speechCommandId = commandId
      speechExpectedDeviceId = device.id
      speechExpectedDeviceName = device.productName?.toString()
      speechExpectedDeviceType = device.type
      speechRoutedDeviceId = null
      speechRoutedDeviceName = null
      speechRoutedDeviceType = null
      speechRouteVerified = false
      speechAudioFocusResult = null
      speechPlayerGain = 0.0f
      speechFinishedCallback = onFinished
      player.prepareAsync()
    } catch (error: Exception) {
      Log.w(SPEECH_TAG, "Speech playback setup failed", error)
      if (speechPlayer === player) {
        clearSpeechPlaybackState(player)
      }
      runCatching { player.release() }
      releaseSpeechFocus()
      return mapOf(
        "started" to false,
        "reason" to (error.message ?: "speech_playback_setup_failed"),
        "expectedDeviceId" to device.id,
        "expectedDeviceName" to device.productName?.toString().orEmpty(),
        "expectedDeviceType" to device.type,
      )
    }
    return mapOf(
      "started" to true,
      "expectedDeviceId" to device.id,
      "expectedDeviceName" to device.productName?.toString().orEmpty(),
      "expectedDeviceType" to device.type,
    )
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
    val result = speechPlaybackResult(player, "stopped", error = "Speech playback stopped.")
    clearSpeechPlaybackState(player)
    try {
      if (player.isPlaying) player.stop()
    } catch (_: IllegalStateException) {
      // A prepareAsync callback can race with an explicit stop.
    }
    player.release()
    releaseSpeechFocus()
    if (stoppedCommandId != null) callback?.invoke(result)
    return stoppedCommandId != null
  }

  private fun finishSpeechPlayback(
    player: MediaPlayer,
    commandId: String,
    status: String,
    durationMs: Long?,
    error: String?,
    onFinished: (SpeechPlaybackResult) -> Unit,
  ) {
    synchronized(this) {
      if (speechPlayer !== player || speechCommandId != commandId) return
      val telemetry = speechPlaybackTelemetry(player)
      Log.i(
        SPEECH_TAG,
        "playback_terminal command_id=$commandId status=$status duration_ms=${durationMs ?: "unknown"} player_duration_ms=${telemetry.playerDurationMs ?: "unknown"} player_position_ms=${telemetry.playerPositionMs ?: "unknown"} player_is_playing=${telemetry.playerIsPlaying ?: "unknown"} player_audio_session_id=${telemetry.playerAudioSessionId ?: "unknown"} player_gain=${telemetry.playerGain} output_stream_volume=${telemetry.outputStreamVolume ?: "unknown"} output_stream_volume_max=${telemetry.outputStreamVolumeMax ?: "unknown"} output_stream_muted=${telemetry.outputStreamMuted ?: "unknown"} route_verified=${telemetry.routeVerified} audio_focus_result=${telemetry.audioFocusResult ?: "unknown"} routed_device_id=${telemetry.routedDeviceId ?: "unknown"} routed_device_type=${telemetry.routedDeviceType ?: "unknown"} runtime_foreground_types=${telemetry.runtimeForegroundTypes} activity_visible=${telemetry.activityVisible} error=${error ?: "none"}",
      )
      val result = speechPlaybackResult(player, status, durationMs, error)
      clearSpeechPlaybackState(player)
      try {
        if (player.isPlaying) player.stop()
        player.release()
      } catch (_: Exception) {
        // Release is best effort after a terminal callback.
      }
      releaseSpeechFocus()
      onFinished(result)
    }
  }

  private fun requestSpeechFocus(context: Context): Int {
    val manager = context.getSystemService(AudioManager::class.java)
      ?: return AudioManager.AUDIOFOCUS_REQUEST_FAILED
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
      return manager.requestAudioFocus(request)
    } else {
      @Suppress("DEPRECATION")
      return manager.requestAudioFocus(null, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
    }
  }

  private fun verifySpeechRoute(
    player: MediaPlayer,
    expectedDevice: AudioDeviceInfo,
    commandId: String,
    onStarted: (SpeechPlaybackTelemetry) -> Unit,
    onProgress: (SpeechPlaybackTelemetry) -> Unit,
    onFinished: (SpeechPlaybackResult) -> Unit,
  ) {
    if (speechPlayer !== player || speechCommandId != commandId || speechRouteVerified) return
    val routed = currentRoutedDevice(player)
    if (routed?.id != expectedDevice.id) return
    rememberSpeechRoutedDevice(routed)
    speechRouteVerified = true
    speechRouteTimeout?.let(handler::removeCallbacks)
    speechRouteTimeout = null
    try {
      player.setVolume(1.0f, 1.0f)
      speechPlayerGain = 1.0f
    } catch (error: Exception) {
      finishSpeechPlayback(
        player,
        commandId,
        "error",
        null,
        error.message ?: "Could not unmute glasses audio after route verification.",
        onFinished,
      )
      return
    }
    val telemetry = speechPlaybackTelemetry(player)
    Log.i(
      SPEECH_TAG,
      "route_verified command_id=$commandId expected_device_id=${telemetry.expectedDeviceId} expected_device_name=${telemetry.expectedDeviceName} expected_device_type=${telemetry.expectedDeviceType} routed_device_id=${telemetry.routedDeviceId} routed_device_name=${telemetry.routedDeviceName} routed_device_type=${telemetry.routedDeviceType} audio_focus_result=${telemetry.audioFocusResult} output_stream_volume=${telemetry.outputStreamVolume ?: "unknown"} output_stream_volume_max=${telemetry.outputStreamVolumeMax ?: "unknown"} output_stream_muted=${telemetry.outputStreamMuted ?: "unknown"} player_duration_ms=${telemetry.playerDurationMs ?: "unknown"} player_position_ms=${telemetry.playerPositionMs ?: "unknown"} player_is_playing=${telemetry.playerIsPlaying ?: "unknown"} player_audio_session_id=${telemetry.playerAudioSessionId ?: "unknown"} player_gain=${telemetry.playerGain} runtime_foreground_types=${telemetry.runtimeForegroundTypes} activity_visible=${telemetry.activityVisible}",
    )
    onStarted(telemetry)
    scheduleSpeechProgress(player, commandId, onProgress)
  }

  private fun handleSpeechRouteChange(
    player: MediaPlayer,
    expectedDevice: AudioDeviceInfo,
    commandId: String,
    onStarted: (SpeechPlaybackTelemetry) -> Unit,
    onProgress: (SpeechPlaybackTelemetry) -> Unit,
    onFinished: (SpeechPlaybackResult) -> Unit,
  ) {
    if (speechPlayer !== player || speechCommandId != commandId) return
    val routed = currentRoutedDevice(player)
    if (routed != null) rememberSpeechRoutedDevice(routed)
    if (routed?.id == expectedDevice.id) {
      verifySpeechRoute(player, expectedDevice, commandId, onStarted, onProgress, onFinished)
    } else if (speechRouteVerified) {
      // A later Bluetooth route change must not leak speech to the handset.
      speechRouteVerified = false
      runCatching { player.setVolume(0.0f, 0.0f) }
      speechPlayerGain = 0.0f
      Log.w(
        SPEECH_TAG,
        "route_lost command_id=$commandId expected_device_id=${expectedDevice.id} expected_device_name=${expectedDevice.productName} expected_device_type=${expectedDevice.type} routed_device_id=${routed?.id ?: "none"} routed_device_name=${routed?.productName ?: "none"} routed_device_type=${routed?.type ?: "none"}",
      )
      finishSpeechPlayback(
        player,
        commandId,
        "error",
        null,
        "Glasses audio route changed during playback.",
        onFinished,
      )
    }
  }

  private fun currentRoutedDevice(player: MediaPlayer): AudioDeviceInfo? =
    try { player.routedDevice } catch (_: IllegalStateException) { null }

  private fun speechPlaybackTelemetry(player: MediaPlayer?): SpeechPlaybackTelemetry {
    val routed = player?.let(::currentRoutedDevice)
    val manager = speechFocusManager
    return SpeechPlaybackTelemetry(
      expectedDeviceId = speechExpectedDeviceId,
      expectedDeviceName = speechExpectedDeviceName,
      expectedDeviceType = speechExpectedDeviceType,
      routedDeviceId = routed?.id ?: speechRoutedDeviceId,
      routedDeviceName = routed?.productName?.toString() ?: speechRoutedDeviceName,
      routedDeviceType = routed?.type ?: speechRoutedDeviceType,
      routeVerified = speechRouteVerified,
      audioFocusResult = speechAudioFocusResult,
      audioFocusGranted = speechAudioFocusResult?.let { it == AudioManager.AUDIOFOCUS_REQUEST_GRANTED },
      runtimeForegroundTypes = DigitalBrainRuntime.service?.foregroundTypes ?: 0,
      activityVisible = DigitalBrainRuntime.activityVisible,
      outputStreamVolume = runCatching { manager?.getStreamVolume(AudioManager.STREAM_MUSIC) }.getOrNull(),
      outputStreamVolumeMax = runCatching { manager?.getStreamMaxVolume(AudioManager.STREAM_MUSIC) }.getOrNull(),
      outputStreamMuted = runCatching { manager?.isStreamMute(AudioManager.STREAM_MUSIC) }.getOrNull(),
      playerDurationMs = runCatching { player?.duration }.getOrNull(),
      playerPositionMs = runCatching { player?.currentPosition }.getOrNull(),
      playerIsPlaying = runCatching { player?.isPlaying }.getOrNull(),
      playerAudioSessionId = runCatching { player?.audioSessionId }.getOrNull(),
      playerGain = speechPlayerGain,
    )
  }

  private fun scheduleSpeechProgress(
    player: MediaPlayer,
    commandId: String,
    onProgress: (SpeechPlaybackTelemetry) -> Unit,
  ) {
    val progress = object : Runnable {
      override fun run() {
        synchronized(this@GlassesAlertPlayback) {
          if (speechPlayer !== player || speechCommandId != commandId) return
          onProgress(speechPlaybackTelemetry(player))
          handler.postDelayed(this, 1_000L)
        }
      }
    }
    speechProgressRunnable = progress
    handler.postDelayed(progress, 1_000L)
  }

  private fun rememberSpeechRoutedDevice(device: AudioDeviceInfo) {
    speechRoutedDeviceId = device.id
    speechRoutedDeviceName = device.productName?.toString()
    speechRoutedDeviceType = device.type
  }

  private fun availableBluetoothOutputs(context: Context): List<Map<String, Any>> {
    val manager = context.getSystemService(AudioManager::class.java) ?: return emptyList()
    val bluetoothTypes = setOf(
      AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
      AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
      AudioDeviceInfo.TYPE_BLE_HEADSET,
      AudioDeviceInfo.TYPE_BLE_SPEAKER,
    )
    return manager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
      .filter { it.type in bluetoothTypes }
      .map { device ->
        mapOf(
          "id" to device.id,
          "name" to device.productName?.toString().orEmpty(),
          "type" to device.type,
        )
      }
  }

  private fun speechPlaybackResult(
    player: MediaPlayer?,
    status: String,
    durationMs: Long? = null,
    error: String? = null,
  ) = SpeechPlaybackResult(
    status = status,
    durationMs = durationMs,
    error = error,
    telemetry = speechPlaybackTelemetry(player),
  )

  private fun clearSpeechPlaybackState(player: MediaPlayer) {
    speechRouteTimeout?.let(handler::removeCallbacks)
    speechRouteTimeout = null
    speechProgressRunnable?.let(handler::removeCallbacks)
    speechProgressRunnable = null
    speechRouteListener?.let(player::removeOnRoutingChangedListener)
    speechRouteListener = null
    speechPlayer = null
    speechCommandId = null
    speechStartedAt = 0L
    speechExpectedDeviceId = null
    speechExpectedDeviceName = null
    speechExpectedDeviceType = null
    speechRoutedDeviceId = null
    speechRoutedDeviceName = null
    speechRoutedDeviceType = null
    speechRouteVerified = false
    speechAudioFocusResult = null
    speechPlayerGain = 0.0f
    speechFinishedCallback = null
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
