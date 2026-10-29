package expo.modules.digitalbrainruntime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SpeechEvalServerCandidateTest {
  @Test fun retiredOrDifferentMatcherRunsRemainExportOnly() {
    fun run(candidate: String, version: Int) = JSONObject().put("candidates", org.json.JSONArray(listOf(candidate))).put("matcherVersion", version)
    assertTrue(SpeechEvalServerCandidate.supportsRun(run(SpeechEvalServerCandidate.ID, GlassesCommandMatcher.VERSION)))
    assertFalse(SpeechEvalServerCandidate.supportsRun(run("local:base.en", GlassesCommandMatcher.VERSION)))
    assertFalse(SpeechEvalServerCandidate.supportsRun(run("server:small.en", GlassesCommandMatcher.VERSION)))
    assertFalse(SpeechEvalServerCandidate.supportsRun(run(SpeechEvalServerCandidate.ID, 2)))
  }
  @Test fun onlyTheGpuCandidateCanRun() {
    val remote = SpeechEvalServerCandidate.parse(SpeechEvalServerCandidate.ID)!!
    assertEquals(listOf("first", "repeat"), remote.modes)
    remote.validateResponse(JSONObject().put("provider", "remote").put("model", "large-v3-turbo").put("upstreamModel", "whisper-large-v3-turbo").put("engine", "openai-compatible"))
    for (old in listOf("local:base.en", "server:small.en", "server:large-v3-turbo@threads=4", "remote:large-v3-turbo&provider=cpu"))
      assertNull(SpeechEvalServerCandidate.parse(old))
  }
  @Test fun parakeetIsDistinctAndCannotBeScoredAsWhisper() {
    val parakeet = SpeechEvalServerCandidate.parse(SpeechEvalServerCandidate.PARAKEET_ID)!!
    assertEquals("parakeet-tdt-0.6b-v3", parakeet.model)
    assertTrue(SpeechEvalServerCandidate.supportsCandidates(listOf(SpeechEvalServerCandidate.ID, SpeechEvalServerCandidate.PARAKEET_ID)))
    assertFalse(SpeechEvalServerCandidate.supportsCandidates(listOf(SpeechEvalServerCandidate.ID, SpeechEvalServerCandidate.ID)))
    assertFalse(SpeechEvalServerCandidate.supportsCandidates(emptyList()))
    parakeet.validateResponse(JSONObject().put("provider", "remote").put("model", parakeet.model).put("upstreamModel", parakeet.model).put("engine", "openai-compatible"))
    try {
      parakeet.validateResponse(JSONObject().put("provider", "remote").put("model", "large-v3-turbo").put("upstreamModel", "whisper-large-v3-turbo").put("engine", "openai-compatible"))
      fail("A different model must not be scored as Parakeet")
    } catch (error: SpeechEvalFailure) { assertEquals("server_update_required", error.reason) }
  }
  @Test fun responsesMustIdentifyTheGpuProvider() {
    val remote = SpeechEvalServerCandidate.parse(SpeechEvalServerCandidate.ID)!!
    for (body in listOf(JSONObject(), JSONObject().put("provider", "cpu").put("model", "large-v3-turbo"))) {
      try {
        remote.validateResponse(body)
        fail("Wrong provider must not be scored")
      } catch (error: SpeechEvalFailure) {
        assertEquals("server_update_required", error.reason)
      }
    }
  }
}
