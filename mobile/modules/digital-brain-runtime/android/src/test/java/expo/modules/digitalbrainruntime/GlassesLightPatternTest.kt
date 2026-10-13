package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesLightPatternTest {
  @Test fun everyPatternIsFiniteAndFitsTheObservationWindow() {
    for (pattern in GlassesLightPattern.entries) {
      assertTrue(pattern.count in 1..3)
      assertTrue(pattern.onMs > 0)
      assertTrue(pattern.offMs >= 0)
      val elapsed = pattern.onMs * pattern.count + pattern.offMs * (pattern.count - 1)
      assertTrue(elapsed <= 5000)
    }
  }
  @Test fun invalidSelectionsNeverDefaultToAnotherColorOrPattern() {
    assertThrows(IllegalStateException::class.java) { GlassesLightPattern.parse("infinite") }
    assertThrows(IllegalStateException::class.java) { GlassesLightPattern.color("purple") }
  }
  @Test fun supportedSdkColorsRemainDistinct() {
    val colors = listOf("red", "green", "blue", "orange", "white").map { GlassesLightPattern.color(it) }
    assertEquals(5, colors.toSet().size)
  }
}
