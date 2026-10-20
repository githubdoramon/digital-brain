package expo.modules.digitalbrainruntime

/** Thread counts form part of candidate identity, keeping comparisons and resumes distinct. */
internal data class SpeechEvalServerCandidate(val model: String, val threads: Int?) {
  val label get() = model + (threads?.let { " ($it threads)" } ?: "")
  val query get() = "model=$model" + (threads?.let { "&threads=$it" } ?: "")
  companion object {
    private val pattern = Regex("^server:(base\\.en|small\\.en|medium\\.en|large-v3(?:-turbo)?)(?:@threads=(2|4|8))?$")
    fun parse(value: String): SpeechEvalServerCandidate? = pattern.matchEntire(value)?.let {
      SpeechEvalServerCandidate(it.groupValues[1], it.groupValues[2].toIntOrNull())
    }
  }
}
