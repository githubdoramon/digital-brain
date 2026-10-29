package expo.modules.digitalbrainruntime

import org.json.JSONObject

/** Parse only static speech timing names with bounded numeric durations. */
internal object SpeechServerTiming {
  private val fields = mapOf("speech_auth" to "backendAuthMs", "speech_upload" to "backendUploadMs",
    "speech_backend" to "backendRouteMs", "speech_proxy_auth" to "proxyAuthMs",
    "speech_proxy_backend" to "proxyBackendMs", "speech_proxy" to "proxyTotalMs")
  fun apply(header: String?, result: JSONObject) {
    if (header == null || header.length > 4096) return
    for (item in header.split(',')) {
      val parts = item.trim().split(';')
      val field = fields[parts.first()] ?: continue
      val value = parts.drop(1).firstOrNull { it.trim().startsWith("dur=") }
        ?.trim()?.removePrefix("dur=")?.toDoubleOrNull() ?: continue
      if (value.isFinite() && value in 0.0..120000.0) result.put(field, value)
    }
  }
}
