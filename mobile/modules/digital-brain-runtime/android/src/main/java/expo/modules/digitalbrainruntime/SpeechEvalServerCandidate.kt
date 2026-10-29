package expo.modules.digitalbrainruntime

import org.json.JSONObject

/** GPU-only replay choices. Production remains on Whisper; retired local/CPU runs are export-only. */
internal data class SpeechEvalServerCandidate(val model: String = "large-v3-turbo", val vocabulary: String = "none") {
  val label get() = (if (model == "large-v3-turbo") "Whisper Turbo" else "Parakeet v3") +
    (if (vocabulary == "none") "" else " · Command vocabulary")
  val modes get() = listOf("first", "repeat")
  val upstreamModel get() = if (model == "large-v3-turbo") "whisper-large-v3-turbo" else model
  fun validateResponse(response: JSONObject) {
    if (response.optString("provider") != "remote" || response.optString("model") != model ||
        response.optString("engine") != "openai-compatible" ||
        (if (vocabulary != "none" && model != "large-v3-turbo") response.optString("upstreamModel").isBlank()
         else response.optString("upstreamModel") != upstreamModel) ||
        response.optString("vocabularyProfile", "none") != vocabulary ||
        (vocabulary != "none" && response.optString("vocabularyMethod") !=
          (if (model == "large-v3-turbo") "prompt" else "server_alias"))) {
      throw SpeechEvalFailure("server_update_required", "request", 200)
    }
  }
  companion object {
    const val ID = "remote:large-v3-turbo"
    const val PARAKEET_ID = "remote:parakeet-tdt-0.6b-v3"
    const val WHISPER_VOCABULARY_ID = "$ID@commands-v1"
    const val PARAKEET_VOCABULARY_ID = "$PARAKEET_ID@commands-v1"
    private val supported = setOf(ID, PARAKEET_ID, WHISPER_VOCABULARY_ID, PARAKEET_VOCABULARY_ID)
    fun parse(value: String): SpeechEvalServerCandidate? = if (value in supported) SpeechEvalServerCandidate(value.removePrefix("remote:").substringBefore("@"),
      if (value.endsWith("@commands-v1")) "commands-v1" else "none") else null
    fun supportsCandidates(values: List<String>) = values.isNotEmpty() && values.size <= supported.size &&
      values.distinct().size == values.size && values.all { it in supported }
    fun supportsRun(value: JSONObject): Boolean {
      val candidates = value.optJSONArray("candidates") ?: return false
      return supportsCandidates((0 until candidates.length()).map { candidates.optString(it) }) &&
        value.optInt("matcherVersion") == GlassesCommandMatcher.VERSION
    }
  }
}
