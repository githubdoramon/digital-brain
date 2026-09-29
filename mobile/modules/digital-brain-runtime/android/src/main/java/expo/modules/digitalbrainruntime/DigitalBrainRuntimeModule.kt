package expo.modules.digitalbrainruntime

import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class DigitalBrainRuntimeModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("DigitalBrainRuntime")

    AsyncFunction("setRuntimeLocationEnabled") { enabled: Boolean ->
      DigitalBrainRuntime.setFeature(context(), RuntimeFeature.LOCATION.key, enabled)
    }
    AsyncFunction("getAppRuntimeStatus") { DigitalBrainRuntime.status(context()) }
    AsyncFunction("getRuntimeEnergyDiagnostics") { RuntimeEnergyDiagnostics.sample(context()) }
    AsyncFunction("completeRuntimeWork") { token: String -> RuntimeWorkService.complete(token) }
    AsyncFunction("readRuntimeLocations") { RuntimeLocationStore.samples(context()) }
    AsyncFunction("acknowledgeRuntimeLocations") { ids: List<String> ->
      RuntimeLocationStore.acknowledge(context(), ids.toSet())
    }
  }

  private fun context() = requireNotNull(appContext.reactContext).applicationContext
}
