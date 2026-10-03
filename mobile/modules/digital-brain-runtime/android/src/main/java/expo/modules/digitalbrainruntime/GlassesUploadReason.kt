package expo.modules.digitalbrainruntime

/** Exact server-owned messages only; do not export arbitrary API response content. */
enum class GlassesUploadReason(val detail: String) {
  INCOMPLETE("Incomplete original media"),
  CHECKSUM("Original media checksum mismatch"),
  UNKNOWN_SESSION("Unknown upload session"),
  ALBUM_UNAVAILABLE("Configured glasses album is unavailable"),
  ALBUM_NOT_CONFIGURED("Configure GLASSES_IMMICH_ALBUM_ID for the existing glasses album"),
  ALBUM_AMBIGUOUS("Existing glasses album must resolve uniquely; configure its ID"),
  ASSET_UNREADABLE("Immich asset is not readable"),
  ALBUM_UNCONFIRMED("Immich album membership is not confirmed"),
  OFFSET("Upload offset mismatch"),
  CONFLICTING_CHUNK("Conflicting retry chunk"),
  UNKNOWN("Server rejected the upload; reason unavailable");
  companion object {
    fun fromDetail(detail: String?): GlassesUploadReason = entries.firstOrNull { it.detail == detail } ?: UNKNOWN
  }
}
class GlassesUploadException(val status: Int, val reason: GlassesUploadReason) :
  java.io.IOException("HTTP $status: ${reason.detail}")
