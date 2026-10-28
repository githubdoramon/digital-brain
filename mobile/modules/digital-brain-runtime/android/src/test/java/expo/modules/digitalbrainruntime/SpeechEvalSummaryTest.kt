package expo.modules.digitalbrainruntime

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SpeechEvalSummaryTest {
  @Test fun remoteSummariesUseFirstAndRepeatAndStaySeparateFromCpu() {
    val rows = JSONArray()
    for ((mode, ms) in listOf("first" to 350, "repeat" to 300)) {
      rows.put(JSONObject().put("candidate", "remote:large-v3-turbo").put("mode", mode)
        .put("status", "complete").put("expectedCommand", "take_picture").put("command", "take_picture")
        .put("correct", true).put("totalMs", ms).put("referenceWords", 5).put("wordErrors", 0))
    }
    val value = JSONObject().put("candidates", JSONArray(listOf("remote:large-v3-turbo", "server:large-v3-turbo@threads=4"))).put("results", rows)
    val summary = SpeechEvalStore.summary(value)
    assertEquals(listOf("first", "repeat", "cold", "warm"), summary.map { it["mode"] })
    assertEquals(350L, summary[0]["medianMs"])
    assertEquals(300L, summary[1]["medianMs"])
    assertEquals(1.0, summary[1]["accuracy"])
    assertEquals(0, summary[2]["count"])
  }
  @Test fun distinguishesRejectionsMissesWrongCommandsAndFailures() {
    val rows = JSONArray()
    fun row(expected: String?, actual: String?, status: String = "complete", ms: Long = 100) {
      rows.put(JSONObject().put("candidate", "local:base.en").put("mode", "cold")
        .put("expectedCommand", expected ?: JSONObject.NULL).put("command", actual ?: JSONObject.NULL)
        .put("status", status).put("correct", expected == actual).put("totalMs", ms)
        .put("referenceWords", 4).put("wordErrors", 1))
    }
    row("open_front_gate", "open_front_gate")
    row(null, null)
    row("open_front_gate", null)
    row("open_front_gate", "toggle_car_gate")
    row(null, "take_picture")
    row("open_front_gate", null, "failed", 50000)
    val value = JSONObject().put("candidates", JSONArray(listOf("local:base.en"))).put("results", rows)
    val summary = SpeechEvalStore.summary(value)
    val cold = summary.first()
    assertEquals(6, cold["count"])
    assertEquals(5, cold["completed"])
    assertEquals(1, cold["failures"])
    assertEquals(2, cold["correct"])
    assertEquals(3, cold["positives"])
    assertEquals(2, cold["negatives"])
    assertEquals(1, cold["missed"])
    assertEquals(1, cold["wrong"])
    assertEquals(1, cold["falseAccepts"])
    assertEquals(.4, cold["accuracy"])
    assertEquals(.25, cold["wer"])
    assertEquals(100L, cold["p95Ms"])
    assertNull(summary[1]["accuracy"])
    assertNull(summary[1]["medianMs"])
  }
}
