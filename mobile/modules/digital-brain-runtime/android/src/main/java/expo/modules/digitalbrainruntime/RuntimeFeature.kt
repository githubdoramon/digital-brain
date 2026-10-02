package expo.modules.digitalbrainruntime

enum class RuntimeFeature(val key: String, val persistent: Boolean) {
  LOCATION("location", true),
  GLASSES("glasses", true);

  companion object {
    fun fromKey(key: String) = entries.firstOrNull { it.key == key }
  }
}

class RuntimeFeatures {
  private val owners = mutableSetOf<RuntimeFeature>()
  fun set(feature: RuntimeFeature, enabled: Boolean) {
    if (enabled) owners.add(feature) else owners.remove(feature)
  }
  fun snapshot(): Set<RuntimeFeature> = owners.toSet()
  fun restore(keys: Set<String>) {
    owners.clear()
    RuntimeFeature.entries.filter { it.persistent && it.key in keys }.forEach(owners::add)
  }
  fun persistentKeys() = owners.filter { it.persistent }.map { it.key }.toSet()
}
