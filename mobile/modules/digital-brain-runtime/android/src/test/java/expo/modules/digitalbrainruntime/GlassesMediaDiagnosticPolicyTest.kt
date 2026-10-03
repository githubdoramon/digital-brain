package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesMediaDiagnosticPolicyTest {
  @Test fun galleryQueriesNeverReachDiagnostics() {
    assertEquals("/api/download", GlassesMediaDiagnosticPolicy.endpoint("/api/download?file=example-private.jpg"))
    assertEquals("/api/v3/manifest", GlassesMediaDiagnosticPolicy.endpoint("/api/v3/manifest?cursor=private-cursor"))
    assertEquals("unknown", GlassesMediaDiagnosticPolicy.endpoint("http://192.168.1.2/api/gallery"))
  }
  @Test fun uploadIdentifiersAreReplacedWithTemplates() {
    assertEquals("/receipts/{key}", GlassesMediaDiagnosticPolicy.endpoint("/receipts/example-key"))
    assertEquals("/sessions/{id}", GlassesMediaDiagnosticPolicy.endpoint("/sessions/example-session?offset=123"))
    assertEquals("/sessions/{id}/complete", GlassesMediaDiagnosticPolicy.endpoint("/sessions/example-session/complete"))
    assertEquals("unknown", GlassesMediaDiagnosticPolicy.endpoint("/unexpected/private-value"))
  }
  @Test fun rawExceptionMessagesAreDiscarded() {
    assertEquals(GlassesMediaFailure.OTHER, GlassesMediaDiagnosticPolicy.failure(IllegalStateException("private-value")))
    assertEquals(GlassesMediaFailure.NETWORK, GlassesMediaDiagnosticPolicy.failure(java.io.IOException("private-network")))
    assertEquals(GlassesMediaFailure.HTTP, GlassesMediaDiagnosticPolicy.failure(
      GlassesGalleryException(GlassesGalleryError.HTTP, "/api/gallery?file=private-value", 404)))
  }
}
