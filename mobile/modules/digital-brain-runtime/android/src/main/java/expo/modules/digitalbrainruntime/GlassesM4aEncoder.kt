package expo.modules.digitalbrainruntime

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File

/** Bounded AAC-LC encoding of the recoverable private PCM spool; no recording bytes enter JS. */
object GlassesM4aEncoder {
  fun encode(source: File, target: File) {
    check(source.length() > 0 && source.length() % 2 == 0L) { "Recording contains no complete audio frames" }
    val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC)
    var muxer: MediaMuxer? = null
    var started = false
    var muxerStarted = false
    try {
      codec.configure(MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC,
        GlassesRecordingPolicy.SAMPLE_RATE, 1).apply {
        setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC)
        setInteger(MediaFormat.KEY_BIT_RATE, 48000)
        setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 4096)
      }, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
      muxer = MediaMuxer(target.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
      codec.start(); started = true
      var track = -1
      var inputEnded = false
      var outputEnded = false
      var bytes = 0L
      var lastProgress = android.os.SystemClock.elapsedRealtime()
      val info = MediaCodec.BufferInfo()
      source.inputStream().buffered().use { input ->
        while (!outputEnded) {
          check(android.os.SystemClock.elapsedRealtime() - lastProgress < 30_000) { "Audio encoder stalled" }
          if (!inputEnded) {
            val index = codec.dequeueInputBuffer(10_000)
            if (index >= 0) {
              val buffer = checkNotNull(codec.getInputBuffer(index)); buffer.clear()
              val chunk = ByteArray(minOf(4096, buffer.remaining()) / 2 * 2)
              check(chunk.isNotEmpty())
              val count = input.read(chunk)
              val time = GlassesRecordingPolicy.presentationUs(bytes)
              if (count < 0) {
                codec.queueInputBuffer(index, 0, 0, time, MediaCodec.BUFFER_FLAG_END_OF_STREAM); inputEnded = true
              } else {
                check(count % 2 == 0); buffer.put(chunk, 0, count)
                codec.queueInputBuffer(index, 0, count, time, 0); bytes += count
              }
              lastProgress = android.os.SystemClock.elapsedRealtime()
            }
          }
          val index = codec.dequeueOutputBuffer(info, 10_000)
          if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
            check(!muxerStarted); track = muxer.addTrack(codec.outputFormat); muxer.start(); muxerStarted = true
          } else if (index >= 0) {
            try {
              if (info.size > 0 && info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0) {
                check(muxerStarted)
                val buffer = checkNotNull(codec.getOutputBuffer(index))
                buffer.position(info.offset); buffer.limit(info.offset + info.size)
                muxer.writeSampleData(track, buffer, info)
              }
              outputEnded = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
              lastProgress = android.os.SystemClock.elapsedRealtime()
            } finally { codec.releaseOutputBuffer(index, false) }
          }
        }
      }
      check(muxerStarted); muxer.stop(); muxerStarted = false
    } finally {
      if (started) runCatching { codec.stop() }
      codec.release()
      if (muxerStarted) runCatching { muxer?.stop() }
      muxer?.release()
    }
  }
}
