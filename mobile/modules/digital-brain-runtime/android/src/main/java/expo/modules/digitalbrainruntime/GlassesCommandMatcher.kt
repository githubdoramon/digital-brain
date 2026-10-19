package expo.modules.digitalbrainruntime

import java.text.Normalizer
import java.util.Locale

internal enum class GlassesCommand(val key: String) {
  OPEN_FRONT_GATE("open_front_gate"), TOGGLE_CAR_GATE("toggle_car_gate"), TAKE_PICTURE("take_picture")
}
internal enum class GlassesCommandMatch { EXACT, FUZZY, MISSING_WAKE, UNKNOWN, AMBIGUOUS }
internal enum class GlassesWakeMatch { EXACT, FUZZY, MISSING }
internal data class GlassesCommandDecision(val match: GlassesCommandMatch, val command: GlassesCommand? = null,
  val wakeMatch: GlassesWakeMatch = if (match == GlassesCommandMatch.MISSING_WAKE) GlassesWakeMatch.MISSING else GlassesWakeMatch.EXACT) {
  val wakeVerified: Boolean get() = match != GlassesCommandMatch.MISSING_WAKE
}

/** Full-utterance matching; transcript contents never enter general diagnostics. */
internal object GlassesCommandMatcher {
  const val VERSION = 1
  private data class Phrase(val command: GlassesCommand, val words: List<String>, val protected: Set<Int>)
  // Narrow phonetic aliases after Sherpa detection; do not match arbitrary similar words like train/rain.
  private val wakeNames = setOf("brain", "brane", "bring", "brian")
  private val phrases = listOf(
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("open", "front", "gate"), setOf(0, 1)),
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("front", "gate"), setOf(0)),
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("front", "door"), setOf(0)),
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("from", "dore"), setOf(0, 1)),
    Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("open", "front", "door"), setOf(0, 1)),
      Phrase(GlassesCommand.OPEN_FRONT_GATE, listOf("open", "the", "door"), setOf(0, 2)),
    Phrase(GlassesCommand.TOGGLE_CAR_GATE, listOf("toggle", "car", "gate"), setOf(1)),
    Phrase(GlassesCommand.TOGGLE_CAR_GATE, listOf("toggle", "car", "door"), setOf(1)),
      Phrase(GlassesCommand.TOGGLE_CAR_GATE, listOf("open", "the", "car", "gate"), setOf(0, 2)),
    Phrase(GlassesCommand.TOGGLE_CAR_GATE, listOf("car", "gate"), setOf(0)),
    Phrase(GlassesCommand.TAKE_PICTURE, listOf("take", "picture"), setOf(0)),
    Phrase(GlassesCommand.TAKE_PICTURE, listOf("take", "pic"), setOf(0)),
    Phrase(GlassesCommand.TAKE_PICTURE, listOf("take", "peek"), setOf(0)),
    Phrase(GlassesCommand.TAKE_PICTURE, listOf("take", "photo"), setOf(0)),
  )
  fun match(text: String): GlassesCommandDecision {
    if (text.length > 512) return GlassesCommandDecision(GlassesCommandMatch.MISSING_WAKE)
    val words = Normalizer.normalize(text.lowercase(Locale.ROOT), Normalizer.Form.NFKD)
      .replace(Regex("\\p{M}+"), "").replace('’', '\'')
      .replace(Regex("[^a-z']+"), " ").trim().split(Regex("\\s+"))
    // No substring search: ordinary conversation containing the phrase must not operate a gate.
    if (words.size < 2 || words[0] !in setOf("hey", "okay", "ok") || words[1] !in wakeNames)
      return GlassesCommandDecision(GlassesCommandMatch.MISSING_WAKE)
    val wakeMatch = if (words[1] == "brain") GlassesWakeMatch.EXACT else GlassesWakeMatch.FUZZY
    val remainder = words.drop(2).toMutableList()
    if (remainder.firstOrNull() == "please") remainder.removeAt(0)
    if (remainder.lastOrNull() == "please") remainder.removeAt(remainder.lastIndex)
    val commandWords = remainder.filter { it !in setOf("the", "a") }
    val exact = phrases.filter { it.words == commandWords }.map { it.command }.distinct()
    if (exact.size == 1) return GlassesCommandDecision(GlassesCommandMatch.EXACT, exact.single(), wakeMatch)
    val fuzzy = phrases.filter { phrase ->
      commandWords.size == phrase.words.size && commandWords.indices.sumOf { i ->
        if (commandWords[i] == phrase.words[i]) 0
        else if (i in phrase.protected || commandWords[i].length < 4 || phrase.words[i].length < 4) 100
        else if (commandWords[i] == "gait" && phrase.words[i] == "gate") 1
        else distance(commandWords[i], phrase.words[i])
      } <= 1
    }.map { it.command }.distinct()
    return when (fuzzy.size) {
      1 -> GlassesCommandDecision(GlassesCommandMatch.FUZZY, fuzzy.single(), wakeMatch)
      0 -> GlassesCommandDecision(GlassesCommandMatch.UNKNOWN, wakeMatch = wakeMatch)
      else -> GlassesCommandDecision(GlassesCommandMatch.AMBIGUOUS, wakeMatch = wakeMatch)
    }
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
