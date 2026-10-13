package expo.modules.digitalbrainruntime

import com.mentra.bluetoothsdk.RgbLedColor

internal enum class GlassesLightPattern(val key: String, val onMs: Int, val offMs: Int, val count: Int) {
  QUICK("quick", 250, 0, 1),
  DOUBLE("double", 1500, 250, 2),
  TRIPLE("triple", 250, 250, 3),
  SLOW("slow", 1000, 1000, 3),
  HOLD("hold", 3000, 0, 1);
  companion object {
    fun parse(key: String): GlassesLightPattern = checkNotNull(entries.firstOrNull { it.key == key }) { "Choose a light pattern" }
    fun color(key: String): RgbLedColor = checkNotNull(RgbLedColor.entries.firstOrNull { it.value == key }) { "Choose a light color" }
  }
}
