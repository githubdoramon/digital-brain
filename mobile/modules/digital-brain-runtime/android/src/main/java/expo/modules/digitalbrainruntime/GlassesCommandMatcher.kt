package expo.modules.digitalbrainruntime

import java.text.Normalizer
import java.util.Locale

internal enum class GlassesCommand(val key: String) {
  OPEN_FRONT_GATE("open_front_gate"), TOGGLE_CAR_GATE("toggle_car_gate"), TAKE_PICTURE("take_picture")
}
internal enum class GlassesCommandMatch { EXACT, FUZZY, MISSING_WAKE, UNKNOWN, AMBIGUOUS }
internal enum class GlassesWakeMatch { EXACT, FUZZY, MISSING }
internal data class GlassesCommandDecision(val match: GlassesCommandMatch, val command: GlassesCommand? = null,
  val wakeMatch: GlassesWakeMatch = if (match == GlassesCommandMatch.MISSING_WAKE) GlassesWakeMatch.MISSING else GlassesWakeMatch.EXACT,
  val stage: String = "rejected", val cost: Double? = null, val margin: Double? = null,
  val reason: String? = null) {
  val wakeVerified: Boolean get() = match != GlassesCommandMatch.MISSING_WAKE
}

/** Ordered, whole-utterance grammar. Distances are costs, never confidence probabilities. */
internal object GlassesCommandMatcher {
  const val VERSION = 3
  private const val MAX_COST = 1.0
  private const val MIN_MARGIN = 0.4
  private data class Phrase(val command: GlassesCommand, val words: List<String>, val protected: Set<Int>)
  private val wakeNames = setOf("brain", "brane", "bring", "brian")
  private val articles = setOf("the", "a")
  private val blocked = setOf("not", "no", "never", "don't", "dont", "cannot", "can't", "cant", "stop", "cancel", "later", "should", "could", "would", "can")
  private val phrases = listOf(
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("open", "front", "gate"), setOf(0, 1)),
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("front", "gate"), setOf(0)),
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("front", "door"), setOf(0)),
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("from", "dore"), setOf(0, 1)),
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("open", "front", "door"), setOf(0, 1)),
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("open", "door"), setOf(0, 1)),
    Phrase(GlassesCommand.TOGGLE_CAR_GATE, listOf("toggle", "car", "gate"), setOf(1)),
    Phrase(GlassesCommand.TOGGLE_CAR_GATE, listOf("toggle", "car", "door"), setOf(1)),
    Phrase(GlassesCommand.TOGGLE_CAR_GATE, listOf("open", "car", "gate"), setOf(0, 1)),
    Phrase(GlassesCommand.TOGGLE_CAR_GATE, listOf("car", "gate"), setOf(0)),
    Phrase(GlassesCommand.TAKE_PICTURE, listOf("take", "picture"), setOf(0)),
    Phrase(GlassesCommand.TAKE_PICTURE, listOf("take", "pic"), setOf(0)),
    Phrase(GlassesCommand.TAKE_PICTURE, listOf("take", "peek"), setOf(0)),
    Phrase(GlassesCommand.TAKE_PICTURE, listOf("take", "photo"), setOf(0)),
  )
  // Only known adjacent target/noun pairs may be fused by ASR. No arbitrary token deletion.
  private val fused = mapOf("cargate" to listOf("car", "gate"), "cardoor" to listOf("car", "door"),
    "frontgate" to listOf("front", "gate"), "frontdoor" to listOf("front", "door"))
  fun match(text: String): GlassesCommandDecision = decide(text, true, true)
  fun grammar(text: String): GlassesCommandDecision = decide(text, true, false)
  fun literal(text: String): GlassesCommandDecision = decide(text, false, false)

  private fun decide(text: String, boundaries: Boolean, phonetic: Boolean): GlassesCommandDecision {
    if (text.length > 512) return GlassesCommandDecision(GlassesCommandMatch.MISSING_WAKE, reason = "input_too_long")
    val words = Normalizer.normalize(text.lowercase(Locale.ROOT), Normalizer.Form.NFKD)
      .replace(Regex("\\p{M}+"), "").replace('’', '\'')
      .replace(Regex("[^a-z']+"), " ").trim().split(Regex("\\s+"))
    if (words.size < 2 || words[0] !in setOf("hey", "okay", "ok") || words[1] !in wakeNames)
      return GlassesCommandDecision(GlassesCommandMatch.MISSING_WAKE, reason = "missing_wake")
    val wake = if (words[1] == "brain") GlassesWakeMatch.EXACT else GlassesWakeMatch.FUZZY
    val body = words.drop(2).toMutableList()
    if (body.any { it in blocked }) return GlassesCommandDecision(GlassesCommandMatch.UNKNOWN, wakeMatch = wake, reason = "negated_or_not_immediate")
    if (body.any { it in setOf("hey", "okay", "ok") }) return GlassesCommandDecision(GlassesCommandMatch.UNKNOWN, wakeMatch = wake, reason = "repeated_wake")
    if (body.firstOrNull() == "please") body.removeAt(0)
    if (body.lastOrNull() == "please") body.removeAt(body.lastIndex)
    val canonical = body.filter { it !in articles }
    if (canonical.isEmpty()) return GlassesCommandDecision(GlassesCommandMatch.UNKNOWN, wakeMatch = wake, reason = "missing_command")
    val repairs = if (boundaries) canonical.count { it in fused } else 0
    if (repairs > 1) return GlassesCommandDecision(GlassesCommandMatch.UNKNOWN, wakeMatch = wake, reason = "too_many_repairs")
    val tokens = if (boundaries) canonical.flatMap { fused[it] ?: listOf(it) } else canonical
    val ranked = phrases.mapNotNull { phrase ->
      if (tokens.size != phrase.words.size) return@mapNotNull null
      var cost = repairs * 0.2
      for (i in tokens.indices) {
        val actual = tokens[i]; val expected = phrase.words[i]
        cost += when {
          actual == expected -> 0.0
          i in phrase.protected || actual.length < 4 || expected.length < 4 -> 100.0
          actual == "gait" && expected == "gate" -> 1.0
          distance(actual, expected) == 1 -> 1.0
          phonetic && transposition(actual, expected) -> 0.8
          phonetic && actual.length >= 4 && expected.length >= 4 && sound(actual) == sound(expected) -> 0.8
          else -> 100.0
        }
      }
      if (cost >= 100) null else phrase.command to cost
    }.groupBy({ it.first }, { it.second }).map { (intent, costs) -> intent to costs.min() }.sortedBy { it.second }
    val best = ranked.firstOrNull()
      ?: return GlassesCommandDecision(GlassesCommandMatch.UNKNOWN, wakeMatch = wake, reason = "outside_grammar")
    val margin = ranked.getOrNull(1)?.let { it.second - best.second }
    if (best.second > MAX_COST) return GlassesCommandDecision(GlassesCommandMatch.UNKNOWN, wakeMatch = wake, cost = best.second, margin = margin, reason = "cost_too_high")
    if (margin != null && margin < MIN_MARGIN) return GlassesCommandDecision(GlassesCommandMatch.AMBIGUOUS, wakeMatch = wake, cost = best.second, margin = margin, reason = "intent_margin_too_small")
    return GlassesCommandDecision(if (best.second == 0.0) GlassesCommandMatch.EXACT else GlassesCommandMatch.FUZZY,
      best.first, wake, if (best.second == 0.0) "exact" else if (repairs > 0) "boundary" else "phonetic_or_spelling", best.second, margin)
  }
  // Vowel-preserving spelling-to-sound rules: do not collapse goat/gate, card/car, or front/frond.
  private fun sound(word: String): String = word.replace("ph", "f").replace("ck", "k")
    .replace("qu", "kw").replace("wh", "w").replace("ee", "ea")
    .replace(Regex("([bcdfghjklmnpqrstvwxyz])\\1+"), "$1").removeSuffix("e")
  private fun transposition(a: String, b: String): Boolean {
    if (a.length != b.length) return false
    val different = a.indices.filter { a[it] != b[it] }
    return different.size == 2 && different[1] == different[0] + 1 &&
      a[different[0]] == b[different[1]] && a[different[1]] == b[different[0]]
  }
  private fun distance(a: String, b: String): Int {
    var previous = IntArray(b.length + 1) { it }
    for (i in a.indices) {
      val current = IntArray(b.length + 1); current[0] = i + 1
      for (j in b.indices) current[j + 1] = minOf(current[j] + 1, previous[j + 1] + 1,
        previous[j] + if (a[i] == b[j]) 0 else 1)
      previous = current
    }
    return previous[b.length]
  }
}
