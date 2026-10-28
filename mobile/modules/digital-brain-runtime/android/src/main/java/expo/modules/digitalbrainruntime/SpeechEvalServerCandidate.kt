package expo.modules.digitalbrainruntime

import org.json.JSONObject

/** Thread counts form part of candidate identity, keeping comparisons and resumes distinct. */
internal data class SpeechEvalServerCandidate(val model: String, val threads: Int?, val remote: Boolean = false) {
  val label get() = model + (if (remote) " (GPU server)" else threads?.let { " ($it threads)" } ?: "")
  val query get() = "model=$model" + (if (remote) "&provider=remote" else threads?.let { "&threads=$it" } ?: "")
  val modes get() = if (remote) listOf("first", "repeat") else listOf("cold", "warm")
  fun validateResponse(response: JSONObject) {
    if (remote && (response.optString("provider") != "remote" || response.optString("model") != model || response.optString("engine") != "openai-compatible")) {
      throw SpeechEvalFailure("server_update_required", "request", 200)
    }
    SpeechEvalFailures.validateCpuResponse(response, threads)
  }
  companion object {
    private val pattern = Regex("^server:(base\\.en|small\\.en|medium\\.en|large-v3(?:-turbo)?)(?:@threads=(2|4|8))?$")
    fun parse(value: String): SpeechEvalServerCandidate? = if (value == "remote:large-v3-turbo") {
      SpeechEvalServerCandidate("large-v3-turbo", null, true)
    } else pattern.matchEntire(value)?.let {
      SpeechEvalServerCandidate(it.groupValues[1], it.groupValues[2].toIntOrNull())
    }
  }
}
