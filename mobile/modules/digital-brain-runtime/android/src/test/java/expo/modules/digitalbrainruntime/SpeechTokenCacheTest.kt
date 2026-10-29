package expo.modules.digitalbrainruntime

import java.util.Base64
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SpeechTokenCacheTest {
  private fun token(account: String = "account-a", client: String = "client-a", expiry: Long = 2000, issued: Long = 900): String {
    val claims = JSONObject().put("sub", account).put("aud", client).put("exp", expiry)
      .put("iat", issued).put("iss", "https://accounts.google.com")
    return "header." + Base64.getUrlEncoder().withoutPadding().encodeToString(claims.toString().toByteArray()) + ".signature"
  }
  @Test fun speechReusesEligibleTokensAndRefreshesBeforeExpiry() {
    val cache = SpeechTokenCache()
    val first = token()
    var refreshes = 0
    assertEquals("cached", cache.get("account-a", "client-a", first, 1000) { error("No refresh") }!!.source)
    assertEquals(first, cache.get("account-a", "client-a", null, 1001) { error("No refresh") }!!.value)
    val refreshed = token(expiry = 3000, issued = 1800)
    val result = cache.get("account-a", "client-a", first, 1880) { refreshes++; refreshed }!!
    assertEquals(1, refreshes)
    assertEquals("silent_sign_in", result.source)
    assertEquals(refreshed, result.value)
  }
  @Test fun identityAudienceClockAndSignoutCannotReuseAnOldToken() {
    val cache = SpeechTokenCache()
    cache.get("account-a", "client-a", token(), 1000) { null }
    assertNull(cache.get("account-b", "client-a", token(), 1000) { null })
    assertNull(cache.get("account-a", "client-b", token(), 1000) { null })
    assertNull(cache.get(null, "client-a", token(), 1000) { error("No sign-in without account") })
    assertNull(cache.get("account-a", "client-a", null, 1000) { null })
    for (bad in listOf("invalid", "a.e30.c", token(issued = 1100), token(expiry = 1000)))
      assertNull(cache.get("account-a", "client-a", bad, 1000) { null })
  }
  @Test fun rejectedTokenIsNotRetriedOrReused() {
    val cache = SpeechTokenCache()
    val rejected = token()
    cache.get("account-a", "client-a", rejected, 1000) { null }
    cache.reject(rejected)
    assertNull(cache.get("account-a", "client-a", rejected, 1000) { rejected })
    val refreshed = token(expiry = 3000)
    assertEquals(refreshed, cache.get("account-a", "client-a", rejected, 1000) { refreshed }!!.value)
  }
}
