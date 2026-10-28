package expo.modules.digitalbrainruntime

import org.json.JSONObject

import org.junit.Assert.*
import org.junit.Test

class SpeechEvalServerCandidateTest {
  @Test fun remoteUsesSeparateProviderWithoutCpuOrWarmClaims() {
    val remote = SpeechEvalServerCandidate.parse("remote:large-v3-turbo")!!
    assertTrue(remote.remote)
    assertNull(remote.threads)
    assertEquals("model=large-v3-turbo&provider=remote", remote.query)
    assertEquals(listOf("first", "repeat"), remote.modes)
    remote.validateResponse(JSONObject().put("provider", "remote").put("model", "large-v3-turbo").put("engine", "openai-compatible"))
    try {
      remote.validateResponse(JSONObject().put("cpuThreads", 2))
      fail("Old servers must not be scored as GPU runs")
    } catch (error: SpeechEvalFailure) {
      assertEquals("server_update_required", error.reason)
    }
    listOf("remote:large-v3-turbo@threads=4", "remote:small.en", "remote:large-v3-turbo&provider=cpu").forEach {
      assertNull(SpeechEvalServerCandidate.parse(it))
    }
  }
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
