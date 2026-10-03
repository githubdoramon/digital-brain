package expo.modules.digitalbrainruntime

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.work.*
import com.mentra.bluetoothsdk.*
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONException
import java.io.FileOutputStream
import java.net.URLEncoder
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** Event-driven transfer work. Queue and upload offsets survive process death and lost replies. */
class GlassesMediaWorker(c: Context, params: WorkerParameters) : CoroutineWorker(c, params) {
  private val c = applicationContext
  private lateinit var owner: String
  private var deadline = 0L
  private var unsupportedCaptures = 0
  private var phase = GlassesMediaPhase.UPLOAD
    set(value) {
      field = value
      GlassesMediaDiagnostics.record(c, GlassesMediaEvent.PHASE, phase = value)
    }
  private fun eligible() = !isStopped && RuntimeGlasses.enabled(c) && RuntimeGlasses.signedIn(c) && GlassesMediaStore.owner(c) == owner
  private fun guard() { check(eligible() && System.currentTimeMillis() < deadline) { "Sync will continue shortly" } }
  private fun state(text: String) = GlassesMediaStore.state(c, text)
  private fun save(record: JSONObject) = GlassesMediaStore.save(c, owner, record)
  private fun files(record: JSONObject): List<JSONObject> = record.getJSONArray("files").let { a -> (0 until a.length()).map(a::getJSONObject) }

  override suspend fun doWork(): Result = syncMutex.withLock {
    val started = android.os.SystemClock.elapsedRealtime()
    GlassesMediaDiagnostics.record(c, GlassesMediaEvent.RUN_STARTED, attempt = runAttemptCount)
    try {
      val result = runSync()
      val event = when (result) {
        is Result.Retry -> GlassesMediaEvent.RUN_RETRY
        is Result.Failure -> GlassesMediaEvent.RUN_FAILED
        else -> GlassesMediaEvent.RUN_SUCCESS
      }
      GlassesMediaDiagnostics.record(c, event, durationMs = android.os.SystemClock.elapsedRealtime() - started)
      result
    } catch (e: CancellationException) {
      GlassesMediaDiagnostics.record(c, GlassesMediaEvent.RUN_CANCELLED, phase = phase)
      throw e
    } catch (e: Exception) {
      GlassesMediaDiagnostics.record(c, GlassesMediaEvent.RUN_FAILED, phase = phase, error = GlassesMediaDiagnosticPolicy.failure(e))
      throw e
    }
  }
  private suspend fun runSync(): Result = withContext(Dispatchers.IO) {
    owner = GlassesMediaStore.owner(c) ?: return@withContext Result.success()
    if (!eligible()) return@withContext Result.success()
    GlassesMediaStore.recover(c, owner)
    GlassesMediaStore.reconcile(c, owner)
    deadline = System.currentTimeMillis() + 7 * 60_000
    val local = GlassesMediaNetwork(c)
    var ownedHotspot = false
    var device: GlassesMediaDevice? = null
    phase = GlassesMediaPhase.UPLOAD
    var networkNote: String? = null
    GlassesMediaStore.transport(c, GlassesMediaTransport.NONE)
    try {
      // Upload cached originals even while glasses are disconnected.
      val uploader = GlassesMediaUpload(c, owner)
      for (record in GlassesMediaStore.records(c, owner).filter { !it.optBoolean("done") }) {
        for (file in files(record).filter { !it.optBoolean("confirmed") && it.has("sha256") }) {
          guard(); state("Uploading originals to Immich")
          uploader.upload(record, file, deadline) { eligible() }; save(record)
        }
      }
      device = withContext(Dispatchers.Main) { RuntimeGlasses.mediaDevice() }
      if (device == null) { GlassesMediaDiagnostics.record(c, GlassesMediaEvent.WAITING_FOR_GLASSES); state("Waiting for glasses"); return@withContext Result.success() }
      val current = device
      ownedHotspot = c.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE).getString("owned_hotspot", null) == current.key
      phase = GlassesMediaPhase.GALLERY
      val gallery = withContext(Dispatchers.Main) { current.sdk.queryGalleryStatus().values }
      if (gallery["cameraBusy"] == true) { GlassesMediaDiagnostics.record(c, GlassesMediaEvent.CAMERA_BUSY); state("Waiting for recording to finish"); return@withContext Result.retry() }
      val count = (gallery["total"] as? Number)?.toInt() ?: (gallery["totalCount"] as? Number)?.toInt() ?: (gallery["total_count"] as? Number)?.toInt()
        ?: ((gallery["photos"] as? Number)?.toInt() ?: 0) + ((gallery["videos"] as? Number)?.toInt() ?: 0)
      GlassesMediaDiagnostics.record(c, GlassesMediaEvent.GALLERY_COUNT, count = count.toLong())
      c.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE).edit()
        .putString("remote_owner", owner).putInt("remote_count", count).apply()
      val needsCleanup = GlassesMediaStore.records(c, owner).any { !it.optBoolean("done") && it.optString("device") == current.key }
      if (count == 0 && !needsCleanup && !inputData.getBoolean("manual", false)) { state("Up to date"); return@withContext Result.success() }
      val permission = if (Build.VERSION.SDK_INT >= 33) Manifest.permission.NEARBY_WIFI_DEVICES else Manifest.permission.ACCESS_FINE_LOCATION
      check(ContextCompat.checkSelfPermission(c, permission) == PackageManager.PERMISSION_GRANTED) { "Open Glasses settings and tap Sync now to allow Wi-Fi transfers" }
      phase = GlassesMediaPhase.WIFI
      state("Checking glasses Wi-Fi")
      var stationReachable = local.existing(current.wifiIp)
      if (!stationReachable && current.wifiIp != null) {
        state("Enabling glasses Wi-Fi gallery")
        val enabled = try {
          withTimeout(5000) {
            withContext(Dispatchers.Main) {
              val ack = current.sdk.setGalleryServerEnabled(true)
              check(ack.status == "applied" && ack.values["enabled"] == true)
            }
          }
          true
        } catch (e: TimeoutCancellationException) { false }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { false }
        GlassesMediaDiagnostics.record(c, if (enabled) GlassesMediaEvent.LAN_GALLERY_ENABLED else GlassesMediaEvent.LAN_GALLERY_UNAVAILABLE)
        if (enabled) {
          // Enabling the listener is acknowledged before it is necessarily reachable.
          repeat(4) {
            if (!stationReachable) { guard(); delay(500); stationReachable = local.existing(current.wifiIp) }
          }
        }
      }
      if (stationReachable) {
        GlassesMediaStore.transport(c, GlassesMediaTransport.WIFI)
      } else {
        networkNote = "Gallery unavailable over the current Wi-Fi; hotspot fallback selected"
        GlassesMediaStore.transport(c, GlassesMediaTransport.NONE, networkNote)
        phase = GlassesMediaPhase.HOTSPOT_START
        state("Starting glasses hotspot")
        val previous = current.hotspot
        ownedHotspot = ownedHotspot || previous !is HotspotStatus.Enabled
        if (ownedHotspot) check(c.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE).edit().putString("owned_hotspot", current.key).commit())
        val hotspot = previous as? HotspotStatus.Enabled ?: withContext(Dispatchers.Main) {
          current.sdk.setHotspotState(true).status as? HotspotStatus.Enabled ?: error("Glasses hotspot unavailable")
        }
        phase = GlassesMediaPhase.HOTSPOT_JOIN
        state("Connecting to glasses hotspot")
        local.join(hotspot.ssid, hotspot.password, hotspot.localIp)
        GlassesMediaStore.transport(c, GlassesMediaTransport.HOTSPOT, networkNote)
      }
      phase = GlassesMediaPhase.GALLERY
      state("Waiting for glasses gallery")
      local.awaitGalleryReady { eligible() }
      state("Checking glasses media")
      discover(local, current.key)
      val pending = GlassesMediaStore.records(c, owner).filter { !it.optBoolean("done") && it.optString("device") == current.key }
      for (record in pending) {
        guard()
        for (file in files(record)) {
          guard()
          if (!file.optBoolean("confirmed")) {
            if (runCatching { uploader.request("/receipts/${file.getString("key")}") }.getOrNull()?.optBoolean("confirmed") == true) {
              file.put("confirmed", true); save(record)
            } else {
              if (!file.has("sha256")) { phase = GlassesMediaPhase.DOWNLOAD; state("Downloading originals from glasses"); download(local, record, file); save(record) }
              phase = GlassesMediaPhase.UPLOAD
              state("Uploading originals to Immich")
              uploader.upload(record, file, deadline) { eligible() }; save(record)
            }
          }
        }
        guard()
        check(files(record).all { it.optBoolean("confirmed") })
        phase = GlassesMediaPhase.CLEANUP
        state("Cleaning up confirmed media")
        if (record.optBoolean("v3")) {
          val result = local.json("/api/v3/ack", JSONObject().put("capture_id", record.getString("capture"))
            .put("ack_id", record.getString("key")))
          check(result.optBoolean("success")) { "Glasses cleanup not confirmed" }
        } else {
          val result = local.json("/api/delete-files", JSONObject().put("files", JSONArray(files(record).map { it.getString("name") })))
          val results = result.getJSONArray("results")
          val expected = files(record).map { it.getString("name") }.toSet()
          val reported = (0 until results.length()).map(results::getJSONObject)
          check(reported.map { it.getString("file") }.toSet() == expected) { "Glasses cleanup acknowledgement mismatch" }
          for (item in reported.filter { !it.optBoolean("success") }) {
            val checkMissing = local.connection("/api/download?file=${encode(item.getString("file"))}")
            try { check(checkMissing.responseCode == 404) { "Glasses cleanup not confirmed" } }
            finally { checkMissing.disconnect() }
          }
        }
        record.put("done", true); save(record)
        GlassesMediaDiagnostics.record(c, GlassesMediaEvent.CAPTURE_CLEANED)
        files(record).forEach { GlassesMediaStore.bytes(c, owner, it.getString("key")).delete() }
      }
      c.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE).edit().putInt("remote_count", unsupportedCaptures).apply()
      state(if (unsupportedCaptures > 0) "Unsupported originals remain on glasses" else if (GlassesMediaStore.records(c, owner).any { !it.optBoolean("done") }) "Waiting for saved glasses" else "Up to date")
      Result.success()
    } catch (e: CancellationException) { throw e }
    catch (e: Exception) {
      GlassesMediaDiagnostics.record(c, GlassesMediaEvent.REQUEST_FAILED, phase = phase, error = GlassesMediaDiagnosticPolicy.failure(e))
      if (eligible() && System.currentTimeMillis() >= deadline) {
        state("Continuing queued originals")
        enqueue(c)
        return@withContext Result.success()
      }
      state(when {
        e is GlassesUploadException -> "${phase.failure}: ${e.message}. Originals retained; will retry."
        e is GlassesGalleryException -> "${phase.failure}: ${e.message}. Originals retained; will retry."
        e is JSONException -> "${phase.failure}: ${GlassesMediaProtocol.safeMetadataReason(e.message)}. Originals retained; will retry."
        e is SecurityException -> "Open Glasses settings to allow Wi-Fi transfers"
        e.message?.startsWith("Open Glasses") == true -> e.message!!
        e.message?.startsWith("Sign in") == true -> "Sign in to resume uploads"
        else -> "${phase.failure}. Originals retained; will retry."
      })
      Result.retry()
    } finally {
      GlassesMediaStore.transport(c, GlassesMediaTransport.NONE, networkNote)
      local.close()
      if (ownedHotspot) withContext(NonCancellable + Dispatchers.Main) {
        if (device?.sdk === RuntimeGlasses.mediaDevice()?.sdk) runCatching {
          device?.sdk?.setHotspotState(false)
          c.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE).edit().remove("owned_hotspot").commit()
        }
      }
    }
  }

  private fun discover(local: GlassesMediaNetwork, device: String) {
    val capabilities = try { local.json("/api/v3/capabilities") }
    catch (e: GlassesGalleryException) {
      if (e.kind == GlassesGalleryError.HTTP && e.httpStatus in setOf(404, 405, 501)) null else throw e
    }
    if (capabilities?.optBoolean("idempotent_ack") == true) {
      var cursor: String? = null
      repeat(100) {
        guard()
        val page = local.json("/api/v3/manifest?limit=64" + (cursor?.let { "&cursor=${encode(it)}" } ?: ""))
        val captures = page.getJSONArray("captures")
        for (i in 0 until captures.length()) {
          val item = captures.getJSONObject(i)
          val media = item.getJSONArray("files").let { a -> (0 until a.length()).map(a::getJSONObject) }
            .filter { it.optString("role") == "primary" }
          if (media.any { !supported(it.optString("mime_type")) || it.optLong("size") <= 0 }) {
            unsupportedCaptures++; continue
          }
          if (media.isNotEmpty()) addRecord(device, item.getString("capture_id"), item.optLong("timestamp").takeIf { it > 0 }, media, true)
        }
        if (!page.optBoolean("has_more")) return
        cursor = page.getString("next_cursor")
      }
      error("Gallery is too large; queued originals are retained")
    } else {
      var offset = 0
      repeat(100) {
        guard()
        val page = local.json("/api/gallery?limit=64&offset=$offset")
        val media = page.getJSONArray("photos")
        for (i in 0 until media.length()) {
          val file = media.getJSONObject(i)
          if (!supported(file.optString("mime_type")) || file.optLong("size") <= 0) { unsupportedCaptures++; continue }
          // Legacy timestamps have no timezone. Do not invent a capture time/location.
          addRecord(device, file.getString("name"), null, listOf(file), false)
        }
        if (!page.optBoolean("has_more")) return
        offset += media.length()
        check(media.length() > 0)
      }
      error("Gallery is too large; queued originals are retained")
    }
  }
  private fun addRecord(device: String, capture: String, timestamp: Long?, media: List<JSONObject>, v3: Boolean) {
    val key = GlassesMediaStore.hash("$device\n$capture")
    val existing = GlassesMediaStore.find(c, owner, key)
    if (existing != null) return
    val record = JSONObject().put("key", key).put("device", device).put("capture", capture).put("v3", v3)
    timestamp?.takeIf { it > 0 }?.let { record.put("timestamp", it) }
    record.put("files", JSONArray(media.map { item ->
      val name = item.getString("name")
      JSONObject().put("key", GlassesMediaStore.hash("$device\n$name"))
        .put("name", name).put("size", item.getLong("size")).put("mime", item.getString("mime_type"))
        .put("etag", item.optString("etag"))
    }))
    save(record)
  }
  private fun download(local: GlassesMediaNetwork, record: JSONObject, file: JSONObject) {
    val original = GlassesMediaStore.bytes(c, owner, file.getString("key"))
    val size = file.getLong("size")
    check(size in 1..(2 * 1024 * 1024 * 1024L)) { "Original exceeds upload size limit" }
    check(c.filesDir.usableSpace > size - original.length() + 32 * 1024 * 1024) { "Phone storage is full" }
    val v3 = record.optBoolean("v3")
    if (!v3 || original.length() > size) original.delete()
    while (original.length() < size) {
      guard()
      val start = original.length()
      val end = if (v3) minOf(size - 1, start + 4 * 1024 * 1024 - 1) else size - 1
      val connection = local.connection("/api/download?file=${encode(file.getString("name"))}")
      try {
        if (v3) {
          connection.setRequestProperty("Range", "bytes=$start-$end")
          if (file.optString("etag").isNotBlank()) connection.setRequestProperty("If-Match", file.getString("etag"))
        }
        val status = connection.responseCode
        if (status != if (v3) 206 else 200) GlassesMediaDiagnostics.record(
          c, GlassesMediaEvent.GALLERY_HTTP, path = "/api/download", httpStatus = status)
        check(status == if (v3) 206 else 200) { "Original download range rejected" }
        if (v3) check(connection.getHeaderField("Content-Range") == "bytes $start-$end/$size") { "Original range mismatch" }
        connection.inputStream.use { input -> FileOutputStream(original, true).use { output ->
          var remaining = end - start + 1
          val buffer = ByteArray(65536)
          while (remaining > 0) {
            guard(); val n = input.read(buffer, 0, minOf(remaining, buffer.size.toLong()).toInt())
            check(n > 0) { "Incomplete original" }; output.write(buffer, 0, n); remaining -= n
          }
          check(input.read() == -1) { "Original grew during transfer" }; output.fd.sync()
        } }
      } finally { connection.disconnect() }
    }
    val digest = MessageDigest.getInstance("SHA-256")
    original.inputStream().use { input -> val buffer = ByteArray(65536); while (true) {
      guard(); val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n)
    } }
    val hash = digest.digest().joinToString("") { "%02x".format(it) }
    if (v3) {
      val expected = local.json("/api/v3/hash?file=${encode(file.getString("name"))}").getString("sha256")
      if (hash != expected) { original.delete(); error("Original checksum mismatch") }
    }
    file.put("sha256", hash)
    GlassesMediaDiagnostics.record(c, GlassesMediaEvent.DOWNLOAD_COMPLETE)
  }
  private fun encode(text: String) = URLEncoder.encode(text, "UTF-8")
  private fun supported(mime: String) = mime in setOf("image/jpeg", "image/png", "image/heic", "image/avif", "video/mp4", "video/quicktime")
  companion object {
    private val syncMutex = Mutex()
    private const val WORK = "glasses-media-sync"
    fun enqueue(c: Context, manual: Boolean = false) {
      if (!RuntimeGlasses.enabled(c) || !RuntimeGlasses.signedIn(c)) return
      val request = OneTimeWorkRequestBuilder<GlassesMediaWorker>().setInputData(workDataOf("manual" to manual))
        .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build()
      GlassesMediaDiagnostics.record(c, GlassesMediaEvent.ENQUEUED)
      WorkManager.getInstance(c).enqueueUniqueWork(WORK, if (manual) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.APPEND_OR_REPLACE, request)
    }
    fun cancel(c: Context) { GlassesMediaDiagnostics.record(c, GlassesMediaEvent.CANCEL_REQUESTED); WorkManager.getInstance(c).cancelUniqueWork(WORK); GlassesMediaStore.state(c, "Sync paused") }
  }
}

data class GlassesMediaDevice(val sdk: MentraBluetoothSdk, val key: String, val wifiIp: String?, val hotspot: HotspotStatus)
