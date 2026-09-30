package expo.modules.digitalbrainruntime

import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class DigitalBrainRuntimeModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("DigitalBrainRuntime")

    AsyncFunction("setRuntimeLocationEnabled") { enabled: Boolean ->
      DigitalBrainRuntime.setFeature(context(), RuntimeFeature.LOCATION.key, enabled)
    }
    AsyncFunction("configureRuntimeLocationUploader") { apiBaseUrl: String, googleWebClientId: String ->
      RuntimeLocationUploadConfigStore.save(context(), apiBaseUrl, googleWebClientId)
    }
    AsyncFunction("getAppRuntimeStatus") { DigitalBrainRuntime.status(context()) }
    AsyncFunction("getRuntimeEnergyDiagnostics") { RuntimeEnergyDiagnostics.sample(context()) }
  }

  private fun context() = requireNotNull(appContext.reactContext).applicationContext
}
