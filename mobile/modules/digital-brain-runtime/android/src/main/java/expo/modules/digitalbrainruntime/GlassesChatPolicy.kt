package expo.modules.digitalbrainruntime

import org.json.JSONObject
import java.util.Base64
import java.text.Normalizer
import java.util.Locale

internal data class GlassesReplyAudio(val pcm: ByteArray, val sampleRate: Int)

/** Chat is eligible only after an anchored wake, independently of the short command grammar. */
internal object GlassesChatPolicy {
  private val wake = Regex("^[^\\p{L}\\p{N}]*([\\p{L}]+)[^\\p{L}\\p{N}]+([\\p{L}]+)\\b[\\s,.:;!?-]*(.*)$", RegexOption.DOT_MATCHES_ALL)
  private fun normalized(text: String) = Normalizer.normalize(text, Normalizer.Form.NFKD)
    .replace(Regex("\\p{M}+"), "").lowercase(Locale.ROOT)
  fun phrase(text: String): String? {
    if (text.length > 8192) return null
    val match = wake.matchEntire(text.trim()) ?: return null
    if (normalized(match.groupValues[1]) !in setOf("hey", "okay", "ok") ||
      normalized(match.groupValues[2]) !in setOf("brain", "brane", "bring", "brian")) return null
    return match.groupValues[3].trim().takeIf { phrase -> phrase.any { it.isLetterOrDigit() } }
  }
  fun shouldChat(text: String, decision: GlassesCommandDecision) = decision.command == null && phrase(text) != null
  fun audio(value: JSONObject): GlassesReplyAudio {
    check(value.getString("encoding") == "pcm16")
    val rate = value.getInt("sample_rate")
    check(rate in setOf(16000, 22050, 24000, 44100, 48000))
    val encoded = value.getString("data")
    check(encoded.length <= 5_592_408)
    val pcm = Base64.getDecoder().decode(encoded)
    check(pcm.isNotEmpty() && pcm.size % 2 == 0 && pcm.size <= 4 * 1024 * 1024 && pcm.size <= rate * 2 * 45)
    return GlassesReplyAudio(pcm, rate)
  }
}
