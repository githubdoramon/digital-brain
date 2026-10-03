package expo.modules.digitalbrainruntime

/** The health endpoint has its own envelope, unlike the gallery API. */
object GlassesMediaProtocol {
  /** Inspect only known platform error markers; never return arbitrary exception text. */
  fun networkFailure(error: java.io.IOException): GlassesGalleryError {
    val causes = generateSequence<Throwable>(error) { it.cause }.take(8).toList()
    if (causes.any { it is java.net.UnknownServiceException &&
        it.message.orEmpty().contains("CLEARTEXT", ignoreCase = true) }) return GlassesGalleryError.CLEARTEXT_BLOCKED
    if (causes.any { it is java.net.NoRouteToHostException ||
        it.message.orEmpty().contains("EHOSTUNREACH") || it.message.orEmpty().contains("ENETUNREACH") }) return GlassesGalleryError.NO_ROUTE
    if (causes.any { it.message.orEmpty().contains("ECONNREFUSED") ||
        (it is java.net.ConnectException && it.message.orEmpty().contains("Connection refused", ignoreCase = true)) }) return GlassesGalleryError.CONNECTION_REFUSED
    return GlassesGalleryError.NETWORK
  }

  fun safeMetadataReason(message: String?): String {
    val field = Regex("^No value for (captures|photos|files|capture_id|timestamp|name|size|mime_type)$")
      .matchEntire(message.orEmpty())?.groupValues?.get(1)
    return if (field != null) "missing gallery field $field" else "incompatible media metadata"
  }
  fun acceptsStatus(path: String, status: String?): Boolean =
    if (path.substringBefore('?') == "/api/health") status == "healthy" || status == "success"
    else status == null || status == "success"
}

enum class GlassesMediaTransport(val label: String) {
  NONE("Not transferring"), WIFI("Wi-Fi"), HOTSPOT("Glasses hotspot")
}

enum class GlassesMediaPhase(val failure: String) {
  WIFI("Could not reach the glasses over Wi-Fi"),
  HOTSPOT_START("Could not start the glasses hotspot"),
  HOTSPOT_JOIN("Could not connect to the glasses hotspot; open this screen to approve Android's connection prompt"),
  GALLERY("Could not read the glasses gallery"),
  DOWNLOAD("Could not download the original from glasses"),
  UPLOAD("Could not upload to Digital Brain"),
  CLEANUP("Upload saved; glasses cleanup will retry")
}

enum class GlassesGalleryError {
  HTTP, TIMEOUT, NETWORK, DISCONNECTED, INVALID_RESPONSE, REJECTED, MALFORMED_MEDIA,
  CONNECTION_REFUSED, NO_ROUTE, CLEARTEXT_BLOCKED
}

class GlassesGalleryException(
  val kind: GlassesGalleryError,
  path: String,
  val httpStatus: Int? = null,
  cause: Throwable? = null,
) : java.io.IOException(reason(kind, path, httpStatus), cause) {
  companion object {
    fun reason(kind: GlassesGalleryError, path: String, status: Int?): String {
      // Endpoints are app-owned literals; never include queries, file names, IPs or response text.
      val endpoint = path.substringBefore('?').takeIf { it in setOf(
        "/api/health", "/api/gallery", "/api/v3/capabilities", "/api/v3/manifest",
        "/api/v3/hash", "/api/v3/ack", "/api/delete-files", "/api/download"
      ) } ?: "gallery request"
      return when (kind) {
        GlassesGalleryError.HTTP -> "$endpoint returned HTTP ${status ?: 0}"
        GlassesGalleryError.TIMEOUT -> "$endpoint timed out"
        GlassesGalleryError.NETWORK -> "Could not reach $endpoint on the glasses"
        GlassesGalleryError.CONNECTION_REFUSED -> "$endpoint connection refused by glasses; gallery server unavailable"
        GlassesGalleryError.NO_ROUTE -> "No Wi-Fi route to $endpoint on the glasses"
        GlassesGalleryError.CLEARTEXT_BLOCKED -> "Android blocked HTTP access to $endpoint"
        GlassesGalleryError.DISCONNECTED -> "Glasses Wi-Fi disconnected"
        GlassesGalleryError.INVALID_RESPONSE -> "$endpoint returned an unrecognised response"
        GlassesGalleryError.REJECTED -> "$endpoint was rejected by the glasses"
        GlassesGalleryError.MALFORMED_MEDIA -> "$endpoint returned incompatible media metadata"
      }
    }
  }
}
