package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock

/** Permission-free OS counters. Battery/current are device-wide, never app-attributed energy. */
object RuntimeEnergyDiagnostics {
  private var previousElapsed: Long? = null
  private var previousUptime = 0L
  private var previousCpu = 0L
  private var previousCharge: Int? = null
  @Synchronized fun sample(context: Context): Map<String, Any?> {
    val elapsed = SystemClock.elapsedRealtime()
    val uptime = SystemClock.uptimeMillis()
    val cpu = Process.getElapsedCpuTime()
    val battery = context.getSystemService(BatteryManager::class.java)
    val power = context.getSystemService(PowerManager::class.java)
    val state = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    fun property(id: Int): Int? = battery.getIntProperty(id).takeUnless { it == Int.MIN_VALUE }
    val charge = property(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER)
    val result = mapOf<String, Any?>(
      "sampleElapsedMs" to elapsed,
      "processCpuMs" to cpu,
      "intervalElapsedMs" to previousElapsed?.let { elapsed - it },
      "intervalProcessCpuMs" to previousElapsed?.let { cpu - previousCpu },
      "intervalDeviceAwakeMs" to previousElapsed?.let { uptime - previousUptime },
      "intervalDeviceDeepSleepMs" to previousElapsed?.let { (elapsed - it - (uptime - previousUptime)).coerceAtLeast(0) },
      "batteryChargeMicroAh" to charge,
      "intervalBatteryChargeDeltaMicroAh" to previousCharge?.let { before -> charge?.let { it.toLong() - before.toLong() } },
      "batteryCurrentMicroA" to property(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW),
      "batteryAverageCurrentMicroA" to property(BatteryManager.BATTERY_PROPERTY_CURRENT_AVERAGE),
      "batteryEnergyNanoWh" to battery.getLongProperty(BatteryManager.BATTERY_PROPERTY_ENERGY_COUNTER).takeUnless { it == Long.MIN_VALUE },
      "batteryTemperatureC" to state?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)?.takeUnless { it == Int.MIN_VALUE }?.div(10.0),
      "batteryVoltageMv" to state?.getIntExtra(BatteryManager.EXTRA_VOLTAGE, -1)?.takeIf { it >= 0 },
      "screenInteractive" to power.isInteractive,
      "deviceIdle" to power.isDeviceIdleMode,
      "powerSaveMode" to power.isPowerSaveMode,
      "workerServiceTotalMs" to RuntimeWorkService.totalDurationMs,
      "workerLastFinishReason" to RuntimeWorkService.lastFinishReason,
      "attribution" to "battery_and_awake_counters_are_device_wide; cpu_is_this_process; worker_duration_is_service_lifetime",
    )
    previousElapsed = elapsed; previousUptime = uptime; previousCpu = cpu; previousCharge = charge
    return result
  }
}
