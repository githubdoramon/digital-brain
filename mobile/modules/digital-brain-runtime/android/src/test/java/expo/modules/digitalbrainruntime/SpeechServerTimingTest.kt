package expo.modules.digitalbrainruntime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SpeechServerTimingTest {
  @Test fun exportsOnlyKnownBoundedNumericTimings() {
    val result = JSONObject()
    SpeechServerTiming.apply("speech_auth;dur=2.5, speech_proxy_backend;dur=180, secret;dur=9, " +
      "speech_upload;dur=NaN, speech_proxy;dur=-4, speech_backend;dur=Infinity", result)
    assertEquals(2.5, result.getDouble("backendAuthMs"), .001)
    assertEquals(180.0, result.getDouble("proxyBackendMs"), .001)
    assertEquals(2, result.length())
    SpeechServerTiming.apply(null, result)
    SpeechServerTiming.apply("speech_upload;dur=99999999", result)
    assertFalse(result.has("backendUploadMs"))
  }
}
