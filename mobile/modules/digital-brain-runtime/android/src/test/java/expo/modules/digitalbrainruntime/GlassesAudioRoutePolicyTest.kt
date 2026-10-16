package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesAudioRoutePolicyTest {
  @Test fun asynchronousSwitchFromPhoneToGlassesIsAllowedOnlyWhileMuted() {
    assertEquals(GlassesAudioRouteAction.WAIT_MUTED, GlassesAudioRoutePolicy.action(8, listOf(2), false))
    assertEquals(GlassesAudioRouteAction.WAIT_MUTED, GlassesAudioRoutePolicy.action(8, emptyList(), false))
    assertEquals(GlassesAudioRouteAction.PLAY, GlassesAudioRoutePolicy.action(8, listOf(8), false))
  }
  @Test fun losingVerifiedOutputStopsRatherThanPlayingOnPhoneOrMultipleDevices() {
    for (outputs in listOf(emptyList(), listOf(2), listOf(8, 2)))
      assertEquals(GlassesAudioRouteAction.STOP, GlassesAudioRoutePolicy.action(8, outputs, true))
    assertEquals(GlassesAudioRouteAction.PLAY, GlassesAudioRoutePolicy.action(8, listOf(8), true))
  }
  @Test fun noUnverifiedCombinationCanUnmuteWithAnExtraOutput() {
    for (outputs in listOf(listOf(8, 2), listOf(2, 9), emptyList()))
      assertEquals(GlassesAudioRouteAction.WAIT_MUTED, GlassesAudioRoutePolicy.action(8, outputs, false))
  }
}
