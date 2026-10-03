package expo.modules.digitalbrainruntime

object GlassesMediaDiagnosticPolicy {
  fun endpoint(path: String): String {
    val route = path.substringBefore('?')
    if (route in setOf("/api/health", "/api/gallery", "/api/v3/capabilities", "/api/v3/manifest",
        "/api/v3/hash", "/api/v3/ack", "/api/delete-files", "/api/download", "/sessions")) return route
    if (Regex("^/receipts/[^/]+$").matches(route)) return "/receipts/{key}"
    if (Regex("^/sessions/[^/]+/complete$").matches(route)) return "/sessions/{id}/complete"
    if (Regex("^/sessions/[^/]+$").matches(route)) return "/sessions/{id}"
    return "unknown"
  }
  fun failure(error: Exception): GlassesMediaFailure = when (error) {
    is GlassesGalleryException -> GlassesMediaFailure.valueOf(error.kind.name)
    is java.net.SocketTimeoutException -> GlassesMediaFailure.TIMEOUT
    is java.io.IOException -> GlassesMediaFailure.NETWORK
    is org.json.JSONException -> GlassesMediaFailure.MALFORMED_MEDIA
    is SecurityException -> GlassesMediaFailure.PERMISSION
    else -> GlassesMediaFailure.OTHER
  }
}
