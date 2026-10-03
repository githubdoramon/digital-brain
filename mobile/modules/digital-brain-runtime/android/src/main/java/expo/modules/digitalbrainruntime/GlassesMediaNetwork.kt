package expo.modules.digitalbrainruntime

import android.content.Context
import android.net.*
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import org.json.JSONException
import java.net.SocketTimeoutException
import java.io.IOException
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.TimeoutCancellationException
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Gallery sockets alone use the local Wi-Fi network. Internet uploads retain Android routing. */
class GlassesMediaNetwork(private val c: Context) : AutoCloseable {
  private val manager = c.getSystemService(ConnectivityManager::class.java)
  private var callback: ConnectivityManager.NetworkCallback? = null
  private var network: Network? = null
  private var address: String? = null

  private fun safeIp(ip: String): Boolean {
    val parts = ip.split('.').map { it.toIntOrNull() ?: -1 }
    return parts.size == 4 && parts.all { it in 0..255 } &&
      (parts[0] == 10 || (parts[0] == 192 && parts[1] == 168) || (parts[0] == 172 && parts[1] in 16..31))
  }
  fun existing(ip: String?): Boolean {
    if (ip == null || !safeIp(ip)) return false
    for (candidate in manager.allNetworks) {
      if (manager.getNetworkCapabilities(candidate)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) != true) continue
      network = candidate; address = ip
      if (runCatching { json("/api/health"); true }.getOrDefault(false)) return true
    }
    network = null; address = null
    return false
  }
  suspend fun join(ssid: String, password: String, ip: String) {
    check(Build.VERSION.SDK_INT >= 29) { "Hotspot transfers require Android 10 or newer" }
    check(safeIp(ip)) { "Invalid glasses hotspot address" }
    val ready = CompletableDeferred<Network>()
    val request = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
      .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
      .setNetworkSpecifier(WifiNetworkSpecifier.Builder().setSsid(ssid).setWpa2Passphrase(password).build()).build()
    val listener = object : ConnectivityManager.NetworkCallback() {
      override fun onCapabilitiesChanged(n: Network, caps: NetworkCapabilities) {
        if (caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_FOREGROUND)) ready.complete(n)
      }
      override fun onUnavailable() { ready.completeExceptionally(IllegalStateException("Open Glasses settings to approve the hotspot connection")) }
      override fun onLost(n: Network) { if (network == n) network = null }
    }
    callback = listener
    manager.requestNetwork(request, listener, 45_000)
    try { network = withTimeout(50_000) { ready.await() } }
    catch (_: TimeoutCancellationException) { error("Open Glasses settings to approve the hotspot connection") }
    address = ip
  }
  fun connection(path: String): HttpURLConnection {
    check(path.startsWith("/api/") && !path.contains('\n'))
    val n = network ?: throw GlassesGalleryException(GlassesGalleryError.DISCONNECTED, path)
    val ip = checkNotNull(address)
    if (!android.security.NetworkSecurityPolicy.getInstance().isCleartextTrafficPermitted(ip)) {
      throw loggedFailure(GlassesGalleryError.CLEARTEXT_BLOCKED, path,
        IOException("Local gallery HTTP blocked by Android policy"))
    }
    return (n.openConnection(URL("http://$ip:8089$path")) as HttpURLConnection).apply {
      connectTimeout = if (path == "/api/health") 2000 else 5000
      readTimeout = if (path == "/api/health") 2000 else 30000; instanceFollowRedirects = false
    }
  }
  fun json(path: String, body: JSONObject? = null): JSONObject {
    val connection = connection(path)
    try {
      if (body != null) {
        val bytes = body.toString().toByteArray()
        connection.requestMethod = "POST"; connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json"); connection.setFixedLengthStreamingMode(bytes.size)
        connection.outputStream.use { it.write(bytes) }
      }
      val status = connection.responseCode
      GlassesMediaDiagnostics.record(c, GlassesMediaEvent.GALLERY_HTTP, path = path, httpStatus = status)
      if (status !in 200..299) throw GlassesGalleryException(GlassesGalleryError.HTTP, path, status)
      val text = connection.inputStream.use { String(GlassesMediaStore.readBounded(it, 2 * 1024 * 1024)) }
      val result = JSONObject(text)
      if (!GlassesMediaProtocol.acceptsStatus(path, if (result.has("status")) result.getString("status") else null))
        throw GlassesGalleryException(GlassesGalleryError.REJECTED, path)
      return result.optJSONObject("data") ?: result
    } catch (e: GlassesGalleryException) {
      GlassesMediaDiagnostics.record(c, GlassesMediaEvent.REQUEST_FAILED, path = path, error = GlassesMediaDiagnosticPolicy.failure(e))
      throw e
    }
    catch (e: SocketTimeoutException) { throw loggedFailure(GlassesGalleryError.TIMEOUT, path, e) }
    catch (e: JSONException) { throw loggedFailure(GlassesGalleryError.INVALID_RESPONSE, path, e) }
    catch (e: IOException) { throw loggedFailure(GlassesMediaProtocol.networkFailure(e), path, e) }
    finally { connection.disconnect() }
  }
  private fun loggedFailure(kind: GlassesGalleryError, path: String, cause: Exception): GlassesGalleryException {
    val error = GlassesGalleryException(kind, path, cause = cause)
    GlassesMediaDiagnostics.record(c, GlassesMediaEvent.REQUEST_FAILED, path = path,
      error = GlassesMediaDiagnosticPolicy.failure(error))
    return error
  }
  /** Joining Wi-Fi does not mean NanoHTTPD has started listening yet. */
  suspend fun awaitGalleryReady(active: () -> Boolean) {
    val deadline = android.os.SystemClock.elapsedRealtime() + 15_000
    var last: GlassesGalleryException? = null
    do {
      if (!active()) throw CancellationException("Glasses sync stopped")
      try {
        json("/api/health")
        return
      } catch (e: GlassesGalleryException) {
        // Firmware without a health route can still have a working gallery.
        if (e.kind == GlassesGalleryError.HTTP && e.httpStatus == 404) {
          json("/api/gallery?limit=1&offset=0")
          return
        }
        last = e
      }
      if (android.os.SystemClock.elapsedRealtime() >= deadline) break
      delay(500)
    } while (true)
    throw checkNotNull(last)
  }
  override fun close() {
    callback?.let { runCatching { manager.unregisterNetworkCallback(it) } }; callback = null; network = null
  }
}
