package expo.modules.digitalbrainruntime

internal enum class GlassesAudioRouteAction { WAIT_MUTED, PLAY, STOP }

/** A preferred-device request can report an old output before its route settles. */
internal object GlassesAudioRoutePolicy {
  fun action(expectedId: Int, outputIds: List<Int>, verified: Boolean): GlassesAudioRouteAction = when {
    outputIds.size == 1 && outputIds.single() == expectedId -> GlassesAudioRouteAction.PLAY
    verified -> GlassesAudioRouteAction.STOP
    else -> GlassesAudioRouteAction.WAIT_MUTED
  }
}
