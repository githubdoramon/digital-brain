package expo.modules.digitalbrainruntime

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRouting
import android.media.AudioTrack
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock

/** One event-driven output. Never falls back to the phone speaker or modifies stream volume. */
internal object GlassesAlertPlayback {
  private val handler = Handler(Looper.getMainLooper())
  private var track: AudioTrack? = null
  private var manager: AudioManager? = null
  private var focus: AudioFocusRequest? = null
  private var routeListener: AudioRouting.OnRoutingChangedListener? = null
  private var devices: AudioDeviceCallback? = null
  private var cleanup: Runnable? = null
  private var routeDeadline: Runnable? = null
  private var kind: String? = null
  var lastError: String? = null
    private set
  private var lastChime: Long? = null
  fun playing() = kind
  fun chimeAllowed(c: Context) = GlassesAlertPolicy.shouldChime(true, true, false, false,
    GlassesAlertSettings.phoneInUse(c), kind == "call", SystemClock.elapsedRealtime(), lastChime)

  fun chime(c: Context): Boolean {
    if (!chimeAllowed(c)) return false
    if (!play(c, false, false)) return false
    lastChime = SystemClock.elapsedRealtime()
    return true
  }
  fun call(c: Context): Boolean = if (kind == "call") true else play(c, true, false)
  fun stopCall() { if (kind == "call") stop() }
  fun stopPreview() { if (kind == "preview") stop() }
  fun preview(c: Context, call: Boolean): Boolean {
    check(kind != "call") { "Wait until the incoming call ends" }
    return play(c, call, true)
  }

  private fun play(c: Context, call: Boolean, preview: Boolean): Boolean {
    stop()
    if (GlassesAlertSettings.dndBlocksMedia(c)) return failed("Do Not Disturb is blocking media alerts")
    val device = GlassesAlertSettings.audioDevice(c) ?: return failed("Connect the glasses Bluetooth audio output first")
    val audio = c.getSystemService(AudioManager::class.java)
    if (audio.isStreamMute(AudioManager.STREAM_MUSIC) || audio.getStreamVolume(AudioManager.STREAM_MUSIC) == 0) {
      return failed("Android media audio is muted. Check Bluetooth volume or Do Not Disturb")
    }
    manager = audio
    val attributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
      .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
    lateinit var request: AudioFocusRequest
    request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
      .setAudioAttributes(attributes).setOnAudioFocusChangeListener({ change ->
        if (focus === request && change < 0) { lastError = "Audio focus was taken by another app"; stop() }
      }, handler).build()
    focus = request
    val focusResult = try { audio.requestAudioFocus(request) } catch (_: RuntimeException) { AudioManager.AUDIOFOCUS_REQUEST_FAILED }
    if (focusResult != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
      stop(); return failed("Android did not grant alert audio focus")
    }
    try {
      val pcm = if (call) GlassesAlertTone.call() else GlassesAlertTone.notification()
      val config = GlassesAlertSettings.config(c)
      val gain = (if (call) config.callVolume else config.chimeVolume) / 100f
      val created = AudioTrack.Builder().setAudioAttributes(attributes)
        .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
          .setSampleRate(GlassesAlertTone.SAMPLE_RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
        .setBufferSizeInBytes(pcm.size).setTransferMode(AudioTrack.MODE_STATIC).build()
      track = created
      kind = if (preview) "preview" else if (call) "call" else "chime"
      if (!created.setPreferredDevice(device)) { stop(); return failed("Glasses audio routing was rejected") }
      check(created.write(pcm, 0, pcm.size, AudioTrack.WRITE_BLOCKING) == pcm.size)
      check(created.setLoopPoints(0, pcm.size / 2, -1) == AudioTrack.SUCCESS)
      created.setVolume(0f)
      var verified = false
      // Preferred routing is only a request. Start muted and unmute only on the exact output.
      fun checkRoute() {
        if (track !== created) return
        val outputs = if (Build.VERSION.SDK_INT >= 36) created.routedDevices else listOfNotNull(created.routedDevice)
        if (outputs.size == 1 && outputs.single().id == device.id) {
          if (!verified) {
            verified = true
            routeDeadline?.let(handler::removeCallbacks); routeDeadline = null
            // The muted primer loops until routing is established. Restart the entire tone.
            created.pause()
            created.setPlaybackHeadPosition(0)
            if (!call) created.setLoopPoints(0, 0, 0)
            created.setVolume(gain)
            created.play()
            val duration = if (!call) GlassesAlertTone.NOTIFICATION_MS.toLong() + 250 else
              if (preview) GlassesAlertTone.CALL_CYCLE_MS * 3L else GlassesAlertPolicy.CALL_TIMEOUT_MS
            cleanup = Runnable { if (track === created) stop() }.also { handler.postDelayed(it, duration) }
          }
        } else if (verified || outputs.isNotEmpty()) {
          created.setVolume(0f)
          lastError = "Glasses audio route was lost"; stop()
        }
      }
      val routing = AudioRouting.OnRoutingChangedListener { checkRoute() }
      routeListener = routing
      created.addOnRoutingChangedListener(routing, handler)
      devices = object : AudioDeviceCallback() {
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>) {
          if (removed.any { it.id == device.id } && track === created) {
            created.setVolume(0f); lastError = "Glasses audio disconnected"; stop()
          }
        }
      }.also { audio.registerAudioDeviceCallback(it, handler) }
      lastError = null
      created.play()
      checkRoute()
      if (track !== created) return false
      if (!verified) {
        routeDeadline = Runnable {
          if (track === created && !verified) { lastError = "Could not verify the glasses audio route"; stop() }
        }.also { handler.postDelayed(it, 1_500) }
      }
      return true
    } catch (_: RuntimeException) {
      stop(); return failed("Could not start glasses alert audio")
    }
  }

  private fun failed(message: String): Boolean { lastError = message; return false }
  fun stop() {
    cleanup?.let(handler::removeCallbacks); cleanup = null
    routeDeadline?.let(handler::removeCallbacks); routeDeadline = null
    val old = track; track = null; kind = null
    if (old != null) {
      routeListener?.let(old::removeOnRoutingChangedListener)
      try { old.setVolume(0f); old.pause(); old.flush() } catch (_: IllegalStateException) { }
      old.release()
    }
    routeListener = null
    val audio = manager
    devices?.let { audio?.unregisterAudioDeviceCallback(it) }; devices = null
    focus?.let { audio?.abandonAudioFocusRequest(it) }; focus = null; manager = null
  }
}
