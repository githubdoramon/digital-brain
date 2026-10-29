package expo.modules.digitalbrainruntime

import java.util.Base64
import org.json.JSONObject

internal data class SpeechToken(val value: String, val source: String)

/** Expiry parsing schedules refresh only. The backend still verifies every JWT signature and claim. */
internal class SpeechTokenCache {
  private data class Entry(val account: String, val client: String, val token: String)
  private var entry: Entry? = null
  private var rejected: String? = null

  @Synchronized fun reject(token: String) { rejected = token; entry = null }
  @Synchronized fun clear() { entry = null; rejected = null }

  @Synchronized fun get(account: String?, client: String, sdkToken: String?, now: Long,
                        refresh: () -> String?): SpeechToken? {
    if (account.isNullOrBlank() || client.isBlank()) { clear(); return null }
    val cached = entry?.takeIf { it.account == account && it.client == client }?.token
    for (token in listOfNotNull(cached, sdkToken).distinct()) {
      if (token != rejected && reusable(token, account, client, now)) {
        entry = Entry(account, client, token)
        return SpeechToken(token, "cached")
      }
    }
    entry = null
    val fresh = refresh() ?: return null
    if (fresh == rejected || !reusable(fresh, account, client, now)) return null
    entry = Entry(account, client, fresh)
    return SpeechToken(fresh, "silent_sign_in")
  }

  private fun reusable(token: String, account: String, client: String, now: Long): Boolean = runCatching {
    if (token.length > 16384) return@runCatching false
    val parts = token.split('.')
    if (parts.size != 3) return@runCatching false
    val claims = JSONObject(String(Base64.getUrlDecoder().decode(parts[1]), Charsets.UTF_8))
    claims.optString("sub") == account && claims.optString("aud") == client &&
      claims.optString("iss") in setOf("https://accounts.google.com", "accounts.google.com") &&
      claims.optLong("exp", 0) > now + 120 && claims.optLong("iat", Long.MAX_VALUE) <= now + 30
  }.getOrDefault(false)
}
