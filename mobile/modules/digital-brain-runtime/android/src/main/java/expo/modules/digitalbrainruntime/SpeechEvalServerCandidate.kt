package expo.modules.digitalbrainruntime

import org.json.JSONObject

/** GPU-only replay choices. Production remains on Whisper; retired local/CPU runs are export-only. */
internal data class SpeechEvalServerCandidate(val model: String = "large-v3-turbo") {
  val label get() = if (model == "large-v3-turbo") "Whisper Turbo" else "Parakeet v3"
  val modes get() = listOf("first", "repeat")
  val upstreamModel get() = if (model == "large-v3-turbo") "whisper-large-v3-turbo" else model
  fun validateResponse(response: JSONObject) {
    if (response.optString("provider") != "remote" || response.optString("model") != model ||
        response.optString("engine") != "openai-compatible" || response.optString("upstreamModel") != upstreamModel) {
      throw SpeechEvalFailure("server_update_required", "request", 200)
    }
  }
  companion object {
    const val ID = "remote:large-v3-turbo"
    const val PARAKEET_ID = "remote:parakeet-tdt-0.6b-v3"
    private val supported = setOf(ID, PARAKEET_ID)
    fun parse(value: String): SpeechEvalServerCandidate? = if (value in supported) SpeechEvalServerCandidate(value.removePrefix("remote:")) else null
    fun supportsCandidates(values: List<String>) = values.isNotEmpty() && values.size <= supported.size &&
      values.distinct().size == values.size && values.all { it in supported }
    fun supportsRun(value: JSONObject): Boolean {
      val candidates = value.optJSONArray("candidates") ?: return false
      return supportsCandidates((0 until candidates.length()).map { candidates.optString(it) }) &&
        value.optInt("matcherVersion") == GlassesCommandMatcher.VERSION
    }
  }
}
