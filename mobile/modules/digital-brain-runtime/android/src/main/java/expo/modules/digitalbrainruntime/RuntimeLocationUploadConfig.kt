package expo.modules.digitalbrainruntime

import android.content.Context
import java.net.URI

data class RuntimeLocationUploadConfig(val apiBaseUrl: String, val googleWebClientId: String)

object RuntimeLocationUploadConfigStore {
  private const val PREFS = "digital_brain_runtime_upload"
  private const val API_BASE_URL = "api_base_url"
  private const val GOOGLE_WEB_CLIENT_ID = "google_web_client_id"

  fun save(context: Context, apiBaseUrl: String, googleWebClientId: String) {
    val normalizedUrl = apiBaseUrl.trim().trimEnd('/')
    val uri = URI(normalizedUrl)
    require(uri.scheme == "https" || uri.scheme == "http") { "Upload URL must use HTTP or HTTPS" }
    require(!uri.host.isNullOrBlank()) { "Upload URL must have a host" }
    require(uri.userInfo == null) { "Upload URL must not contain user information" }
    require(googleWebClientId.isNotBlank()) { "Google web client ID is required for native uploads" }

    check(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
      .putString(API_BASE_URL, normalizedUrl)
      .putString(GOOGLE_WEB_CLIENT_ID, googleWebClientId.trim())
      .commit()) { "Could not persist native location upload configuration" }
  }

  fun load(context: Context): RuntimeLocationUploadConfig? {
    val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    val apiBaseUrl = prefs.getString(API_BASE_URL, null)?.takeIf(String::isNotBlank) ?: return null
    val googleWebClientId = prefs.getString(GOOGLE_WEB_CLIENT_ID, null)?.takeIf(String::isNotBlank) ?: return null
    return RuntimeLocationUploadConfig(apiBaseUrl, googleWebClientId)
  }
}
