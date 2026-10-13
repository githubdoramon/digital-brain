package expo.modules.digitalbrainruntime

import android.content.Context
import android.location.Location
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority

/** Captures on either the time cadence or the movement threshold, then resets the time cadence. */
class RuntimeLocationCapture(private val context: Context, private val onCommitted: () -> Unit) {
  private val client = LocationServices.getFusedLocationProviderClient(context)
  private val sampleLock = Any()
  private var thread: HandlerThread? = null
  @Volatile var active = false
    private set
  @Volatile private var requested = false
  private var generation = 0L
  private var lastCapturedLocation: Location? = null

  private val timedCallback = callbackFor("time")
  private val movementCallback = callbackFor("movement")

  private fun callbackFor(lane: String) = object : LocationCallback() {
    override fun onLocationResult(result: LocationResult) {
      if (!requested) {

        return
      }
      processLocations(result.locations, lane)
    }
  }

  @Suppress("MissingPermission")
  fun start() {
    if (requested) return
    val attempt = ++generation
    requested = true
    val lastQueuedSample = runCatching {
      RuntimeLocationStore.pendingSamples(context).maxByOrNull(RuntimeLocationSample::timestamp)
    }.onFailure { error ->

    }.getOrNull()
    lastCapturedLocation = lastQueuedSample
      ?.let { sample ->
        Location("persisted-sample").apply {
          latitude = sample.latitude
          longitude = sample.longitude
          time = sample.timestamp
        }
      }

    val worker = HandlerThread("DigitalBrainLocation").also { it.start() }
    thread = worker

    val timedRequest = LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, LOCATION_INTERVAL_MS)
      .setMinUpdateIntervalMillis(LOCATION_INTERVAL_MS)
      .setMinUpdateDistanceMeters(0f)
      .setMaxUpdateDelayMillis(MAX_BATCH_DELAY_MS)
      .build()
    val movementRequest = LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, LOCATION_INTERVAL_MS)
      .setMinUpdateIntervalMillis(MOVEMENT_MIN_INTERVAL_MS)
      .setMinUpdateDistanceMeters(MOVEMENT_DISTANCE_METERS)
      .setMaxUpdateDelayMillis(0L)
      .build()

    requestUpdates(timedRequest, timedCallback, worker, attempt, "time")
    requestUpdates(movementRequest, movementCallback, worker, attempt, "movement")
  }

  @Suppress("MissingPermission")
  private fun requestUpdates(
    request: LocationRequest,
    callback: LocationCallback,
    worker: HandlerThread,
    attempt: Long,
    lane: String,
  ) {
    try {
      client.requestLocationUpdates(request, callback, worker.looper)
        .addOnSuccessListener {
          if (generation == attempt) {
            active = true
            DigitalBrainRuntime.lastError = null


          }
        }
        .addOnFailureListener { error ->
          if (generation == attempt) {

            if (lane == "time") stop("time_registration_failed")
            DigitalBrainRuntime.lastError = "Location registration failed: ${error.javaClass.simpleName}"
            Log.w("DigitalBrainRuntime", "location_registration_failed lane=$lane", error)
          }
        }
    } catch (error: RuntimeException) {

      if (lane == "time") {
        stop("time_registration_exception")
        throw error
      }
      DigitalBrainRuntime.lastError = "Movement location registration failed: ${error.javaClass.simpleName}"
    }
  }

  private fun processLocations(locations: List<Location>, lane: String) {
    val started = SystemClock.elapsedRealtime()
    val ordered = locations.filter { it.latitude.isFinite() && it.longitude.isFinite() && it.time > 0 }
      .sortedBy(Location::getTime)

    try {
      val selected = mutableListOf<Location>()
      var timeTriggered = 0
      var movementTriggered = 0
      var accuracyRejected = 0
      synchronized(sampleLock) {
        var baseline = lastCapturedLocation
        for (location in ordered) {
          if (baseline != null && location.time <= baseline.time) continue
          if (location.hasAccuracy() && location.accuracy > MAX_STORABLE_LOCATION_ACCURACY_METERS) {
            accuracyRejected++
            continue
          }
          val elapsed = baseline?.let { location.time - it.time } ?: Long.MAX_VALUE
          val distance = baseline?.distanceTo(location) ?: Float.POSITIVE_INFINITY
          val trigger = when {
            baseline == null -> "initial"
            distance >= MOVEMENT_DISTANCE_METERS -> "movement"
            elapsed >= LOCATION_INTERVAL_MS -> "time"
            else -> null
          }
          if (trigger != null) {
            selected += Location(location)
            if (trigger == "movement") movementTriggered++ else if (trigger == "time") timeTriggered++
            baseline = location
          }
        }
        if (selected.isNotEmpty()) {
          RuntimeLocationStore.appendBatch(context, selected)
          lastCapturedLocation = selected.last()
        }
      }

      if (selected.isNotEmpty()) {

        // Restart the stationary lane so its next requested capture is ten minutes after this one.
        if (movementTriggered > 0) restartTimedUpdates()

        onCommitted()
      }
    } catch (error: Exception) {
      DigitalBrainRuntime.lastError = "Location persistence failed: ${error.javaClass.simpleName}"

      Log.e("DigitalBrainRuntime", "location_capture_commit_failed lane=$lane", error)
    }
  }

  @Suppress("MissingPermission")
  private fun restartTimedUpdates() {
    val worker = thread ?: return
    val attempt = generation
    if (!requested) return
    // Flush the batched timer lane before replacing its request so pending timed fixes aren't lost.
    client.flushLocations().addOnCompleteListener { flushResult ->
      if (!flushResult.isSuccessful) {

      }
      client.removeLocationUpdates(timedCallback).addOnCompleteListener {
        if (!requested || generation != attempt || thread !== worker) return@addOnCompleteListener
        val request = LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, LOCATION_INTERVAL_MS)
          .setMinUpdateIntervalMillis(LOCATION_INTERVAL_MS)
          .setMinUpdateDistanceMeters(0f)
          .setMaxUpdateDelayMillis(MAX_BATCH_DELAY_MS)
          .build()

        requestUpdates(request, timedCallback, worker, attempt, "time")
      }
    }
  }

  fun stop(reason: String = "unspecified") {
    if (!requested && thread == null) return

    generation++
    requested = false
    active = false
    client.removeLocationUpdates(timedCallback)
    client.removeLocationUpdates(movementCallback)
    thread?.quitSafely()
    thread = null
  }

  private companion object {
    const val LOCATION_INTERVAL_MS = 600_000L
    const val MOVEMENT_MIN_INTERVAL_MS = 60_000L
    const val MOVEMENT_DISTANCE_METERS = 50f
    const val MAX_STORABLE_LOCATION_ACCURACY_METERS = 50f
    const val MAX_BATCH_DELAY_MS = 3_600_000L

    fun isoTimestamp(timestamp: Long): String = java.text.SimpleDateFormat(
      "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
      java.util.Locale.US,
    ).apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }.format(java.util.Date(timestamp))
  }
}
