package expo.modules.digitalbrainruntime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SpeechEvalFailureTest {
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
