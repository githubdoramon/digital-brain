package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesMediaProtocolTest {
  @Test fun networkFailuresPreserveCategoryWithoutPrivateMessages() {
    assertEquals(GlassesGalleryError.CONNECTION_REFUSED, GlassesMediaProtocol.networkFailure(
      java.net.ConnectException("example-private-address: ECONNREFUSED")))
    assertEquals(GlassesGalleryError.NO_ROUTE, GlassesMediaProtocol.networkFailure(
      java.io.IOException("wrapper", java.net.NoRouteToHostException("example-private-address"))))
    assertEquals(GlassesGalleryError.CLEARTEXT_BLOCKED, GlassesMediaProtocol.networkFailure(
      java.net.UnknownServiceException("CLEARTEXT communication to example-private-address not permitted")))
    assertEquals(GlassesGalleryError.NETWORK, GlassesMediaProtocol.networkFailure(java.io.IOException("example-private-address")))
    assertFalse(GlassesGalleryException.reason(GlassesGalleryError.NO_ROUTE, "/api/health", null).contains("example-private-address"))
  }

  @Test fun reachableHealthDoesNotForceHotspotFallback() {
    assertTrue(GlassesMediaProtocol.acceptsStatus("/api/health", "healthy"))
    assertTrue(GlassesMediaProtocol.acceptsStatus("/api/health", "success"))
    assertFalse(GlassesMediaProtocol.acceptsStatus("/api/health", "unhealthy"))
    assertFalse(GlassesMediaProtocol.acceptsStatus("/api/health", null))
  }
  @Test fun galleryErrorsStillFailClosed() {
    assertTrue(GlassesMediaProtocol.acceptsStatus("/api/gallery", "success"))
    assertTrue(GlassesMediaProtocol.acceptsStatus("/api/gallery", null))
    assertFalse(GlassesMediaProtocol.acceptsStatus("/api/gallery", "healthy"))
    assertFalse(GlassesMediaProtocol.acceptsStatus("/api/v3/ack", "error"))
  }
  @Test fun failureDetailsRetainEndpointAndCodeButExcludePrivateQuery() {
    val reason = GlassesGalleryException.reason(GlassesGalleryError.HTTP, "/api/gallery?file=example.jpg", 404)
    assertEquals("/api/gallery returned HTTP 404", reason)
    assertFalse(reason.contains("example.jpg"))
    assertEquals("gallery request timed out", GlassesGalleryException.reason(GlassesGalleryError.TIMEOUT, "/untrusted", null))
  }
  @Test fun metadataErrorsExposeOnlyKnownFieldNames() {
    assertEquals("missing gallery field captures", GlassesMediaProtocol.safeMetadataReason("No value for captures"))
    assertEquals("incompatible media metadata", GlassesMediaProtocol.safeMetadataReason("No value for example-private-file.jpg"))
  }
}
