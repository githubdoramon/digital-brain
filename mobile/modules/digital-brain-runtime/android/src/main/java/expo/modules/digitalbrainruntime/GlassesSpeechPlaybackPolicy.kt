package expo.modules.digitalbrainruntime

import kotlin.math.abs

/** Content-free evidence of received PCM and actual Android playback progress. */
internal object GlassesSpeechPlaybackPolicy {
  fun complete(frames: Int, playedFrames: Long, routeVerified: Boolean) =
    routeVerified && frames > 0 && playedFrames >= frames.toLong()

  fun peak(pcm: ByteArray): Int {
    var peak = 0
    for (i in 0 until pcm.size - 1 step 2) {
      val sample = ((pcm[i].toInt() and 0xff) or (pcm[i + 1].toInt() shl 8)).toShort().toInt()
      peak = maxOf(peak, abs(sample))
    }
    return peak
  }
}
