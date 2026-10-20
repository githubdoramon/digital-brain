package expo.modules.digitalbrainruntime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SpeechEvalFailureTest {
  @Test fun oldServerSuccessIsActionableWithoutScoringWrongCpuConfiguration() {
    try {
      SpeechEvalFailures.validateCpuResponse(JSONObject().put("prepared", true), 4)
      fail("Old backend must not be scored as a four-thread run")
    } catch (error: SpeechEvalFailure) {
      val row = JSONObject().put("status", "failed")
      SpeechEvalFailures.annotate(row, error)
      assertEquals("server_update_required", row.getString("error"))
      assertEquals("request", row.getString("failureStage"))
      assertEquals(200, row.getInt("httpStatus"))
      assertEquals(mapOf("server_update_required" to 1), SpeechEvalFailures.counts(listOf(row)))
    }
    SpeechEvalFailures.validateCpuResponse(JSONObject().put("cpuThreads", 4), 4)
    SpeechEvalFailures.validateCpuResponse(JSONObject(), null)
    try {
      SpeechEvalFailures.validateCpuResponse(JSONObject().put("cpuThreads", 2), 4)
      fail("Wrong configuration must not be scored")
    } catch (error: SpeechEvalFailure) {
      assertEquals("cpu_configuration_mismatch", error.reason)
    }
  }
  @Test fun summaryNeverExposesArbitraryFailureText() {
    val rows = listOf(
      JSONObject().put("status", "failed").put("error", "private transcript"),
      JSONObject().put("status", "complete").put("error", "private URL"),
    )
    assertEquals(mapOf("evaluation_failed" to 1), SpeechEvalFailures.counts(rows))
  }
  @Test fun retainsSafeProviderFailureAndRejectsRawMessages() {
    val body = JSONObject().put("detail", "model_download_http_error").put("failureStage", "model_download").put("upstreamHttpStatus", 403)
    val row = JSONObject()
    SpeechEvalFailures.annotate(row, SpeechEvalFailures.http(503, body))
    assertEquals("model_download_http_error", row.getString("error"))
    assertEquals("model_download", row.getString("failureStage"))
    assertEquals(503, row.getInt("httpStatus"))
    assertEquals(403, row.getInt("upstreamHttpStatus"))
    val unsafe = JSONObject().put("detail", "private transcript").put("failureStage", "private URL")
    val sanitized = JSONObject()
    SpeechEvalFailures.annotate(sanitized, SpeechEvalFailures.http(502, unsafe))
    assertEquals("upstream_unavailable", sanitized.getString("error"))
    assertFalse(sanitized.toString().contains("private"))
  }
  @Test fun distinguishesAuthAndTransportFailures() {
    assertEquals("auth_rejected", SpeechEvalFailures.http(401, null).reason)
    val row = JSONObject()
    SpeechEvalFailures.annotate(row, java.net.SocketTimeoutException("private URL"))
    assertEquals("network_timeout", row.getString("error"))
    assertFalse(row.toString().contains("private"))
  }
}
