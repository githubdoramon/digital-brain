package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class SpeechEvalServerCandidateTest {
  @Test fun acceptsLegacyAndExplicitThreadSelections() {
    val legacy = SpeechEvalServerCandidate.parse("server:small.en")!!
    assertNull(legacy.threads)
    assertEquals("model=small.en", legacy.query)
    for (threads in listOf(2, 4, 8)) {
      val candidate = SpeechEvalServerCandidate.parse("server:large-v3-turbo@threads=$threads")!!
      assertEquals(threads, candidate.threads)
      assertEquals("model=large-v3-turbo&threads=$threads", candidate.query)
    }
  }
  @Test fun rejectsUnknownModelsCountsAndQueryInjection() {
    listOf("local:small.en", "server:unknown", "server:small.en@threads=3", "server:small.en@threads=4&cold=false", "server:small.en@threads=8/private").forEach {
      assertNull(SpeechEvalServerCandidate.parse(it))
    }
  }
}
