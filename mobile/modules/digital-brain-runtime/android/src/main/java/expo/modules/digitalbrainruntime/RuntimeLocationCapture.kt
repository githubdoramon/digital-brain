package expo.modules.digitalbrainruntime

import android.content.Context
import android.os.HandlerThread
import android.util.Log
import android.os.SystemClock
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority

class RuntimeLocationCapture(private val context: Context, private val onCommitted: () -> Unit) {
  private val client = LocationServices.getFusedLocationProviderClient(context)
  private var thread: HandlerThread? = null
  @Volatile var active = false
    private set
  @Volatile private var requested = false
  private var generation = 0L
  private val callback = object : LocationCallback() {
    override fun onLocationResult(result: LocationResult) {
      if (!requested) return
      val started = SystemClock.elapsedRealtime()
      try {
        RuntimeLocationStore.appendBatch(context, result.locations)
        Log.i("DigitalBrainRuntime", "location_batch count=${result.locations.size} commit_ms=${SystemClock.elapsedRealtime() - started}")
        if (result.locations.isNotEmpty()) onCommitted()
      } catch (error: Exception) {
        DigitalBrainRuntime.lastError = "Location persistence failed: ${error.javaClass.simpleName}"
        Log.e("DigitalBrainRuntime", "location_capture_commit_failed", error)
      }
    }
  }

  @Suppress("MissingPermission")
  fun start() {
    if (requested) return
    val attempt = ++generation
    requested = true
    val worker = HandlerThread("DigitalBrainLocation").also { it.start() }
    thread = worker
    val request = LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, LOCATION_INTERVAL_MS)
      .setMinUpdateIntervalMillis(LOCATION_INTERVAL_MS)
      .setMinUpdateDistanceMeters(50f)
      .setMaxUpdateDelayMillis(MAX_BATCH_DELAY_MS)
      .build()
    try {
      client.requestLocationUpdates(request, callback, worker.looper)
        .addOnSuccessListener {
          if (generation == attempt) {
            active = true
            DigitalBrainRuntime.lastError = null
            Log.i("DigitalBrainRuntime", "location_registered interval_ms=$LOCATION_INTERVAL_MS distance_m=50 max_delay_ms=$MAX_BATCH_DELAY_MS accuracy=balanced")
          }
        }
        .addOnFailureListener { error ->
          if (generation == attempt) {
            stop()
            DigitalBrainRuntime.lastError = "Location registration failed: ${error.javaClass.simpleName}"
            Log.w("DigitalBrainRuntime", "location_registration_failed", error)
          }
        }
    } catch (error: RuntimeException) {
      stop()
      throw error
    }
  }

  fun stop() {
    if (!requested && thread == null) return
    generation++
    requested = false
    active = false
    client.removeLocationUpdates(callback)
    thread?.quitSafely()
    thread = null
  }

  private companion object {
    const val LOCATION_INTERVAL_MS = 600_000L
    const val MAX_BATCH_DELAY_MS = 3_600_000L
  }
}
