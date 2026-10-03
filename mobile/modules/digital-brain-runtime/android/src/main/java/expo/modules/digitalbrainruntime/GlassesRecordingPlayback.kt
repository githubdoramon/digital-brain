package expo.modules.digitalbrainruntime

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaPlayer
import android.net.Uri

/** Interactive local-file playback; caller releases when the recording screen leaves the foreground. */
object GlassesRecordingPlayback {
  private var player: MediaPlayer? = null
  private var focus: AudioFocusRequest? = null
  private var audio: AudioManager? = null
  private var uri: String? = null
  private var ready = false
  private var error: String? = null
  fun play(c: Context, value: String) {
    check(!GlassesRecording.busy) { "Stop recording before playback" }
    if (value == uri && ready && player != null) {
      if (player!!.isPlaying) { player!!.pause(); releaseFocus() } else { acquire(c); player!!.start() }
      return
    }
    stop(); error = null; acquire(c)
    uri = value
    val created = MediaPlayer(); player = created
    try {
      created.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
      created.setDataSource(c, Uri.parse(value))
      created.setOnPreparedListener { if (player === it) { ready = true; it.start() } }
      created.setOnCompletionListener { it.pause(); it.seekTo(0); releaseFocus() }
      created.setOnErrorListener { _, _, _ -> stop(); error = "Could not play this audio file"; true }
      created.prepareAsync()
    } catch (e: Exception) { stop(); throw IllegalStateException("Could not open this audio file", e) }
  }
  private fun acquire(c: Context) {
    if (focus != null) return
    audio = c.getSystemService(AudioManager::class.java)
    val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
      .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).build())
      .setOnAudioFocusChangeListener { value -> if (value != AudioManager.AUDIOFOCUS_GAIN) stop() }.build()
    check(audio!!.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED) { "Audio playback is unavailable during another audio session" }
    focus = request
  }
  private fun releaseFocus() { focus?.let { audio?.abandonAudioFocusRequest(it) }; focus = null; audio = null }
  fun seek(ms: Int) { if (ready) player?.let { it.seekTo(ms.coerceIn(0, it.duration)) } }
  fun stop() { ready = false; player?.release(); player = null; uri = null; releaseFocus() }
  fun snapshot(): Map<String, Any?> = mapOf("uri" to uri, "playing" to (ready && player?.isPlaying == true),
    "durationMs" to (if (ready) player?.duration ?: 0 else 0), "positionMs" to (if (ready) player?.currentPosition ?: 0 else 0), "error" to error)
}
