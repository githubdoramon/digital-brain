package expo.modules.digitalbrainruntime

import android.content.Context
import android.util.AtomicFile
import com.google.android.gms.auth.api.signin.GoogleSignIn
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/** Private original bytes, atomic queue records, and account-scoped completion tombstones. */
object GlassesMediaStore {
  fun readBounded(input: java.io.InputStream, limit: Int): ByteArray {
    val out = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    while (true) {
      val n = input.read(buffer)
      if (n < 0) break
      check(out.size() + n <= limit) { "Response exceeds size limit" }
      out.write(buffer, 0, n)
    }
    return out.toByteArray()
  }
  fun hash(value: String) = MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }
  fun owner(c: Context): String? = GoogleSignIn.getLastSignedInAccount(c)?.id?.let(::hash)
  fun directory(c: Context, owner: String): File = File(c.filesDir, "glasses-media/$owner").apply { mkdirs() }
  @Synchronized fun records(c: Context, owner: String): List<JSONObject> = directory(c, owner).listFiles().orEmpty()
    .filter { it.name.endsWith(".json") }.map { JSONObject(AtomicFile(it).openRead().bufferedReader().use { r -> r.readText() }) }
  @Synchronized fun find(c: Context, owner: String, key: String): JSONObject? {
    val file = File(directory(c, owner), "$key.json")
    if (!file.exists()) return null
    return JSONObject(AtomicFile(file).openRead().bufferedReader().use { it.readText() })
  }
  @Synchronized fun recover(c: Context, owner: String) {
    records(c, owner).filter { it.optBoolean("done") }.forEach { record ->
      val files = record.getJSONArray("files")
      for (i in 0 until files.length()) bytes(c, owner, files.getJSONObject(i).getString("key")).delete()
      val tombstone = File(directory(c, owner), record.getString("key") + ".json")
      if (System.currentTimeMillis() - tombstone.lastModified() > 30 * 86400_000L) AtomicFile(tombstone).delete()
    }
  }
  @Synchronized fun reconcile(c: Context, owner: String) {
    c.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE).edit()
      .putInt("pending_$owner", records(c, owner).count { !it.optBoolean("done") }).commit()
  }
  @Synchronized fun save(c: Context, owner: String, value: JSONObject) {
    val previous = find(c, owner, value.getString("key"))
    val atomic = AtomicFile(File(directory(c, owner), value.getString("key") + ".json"))
    val stream = atomic.startWrite()
    try { stream.write(value.toString().toByteArray()); atomic.finishWrite(stream) }
    catch (e: Exception) { atomic.failWrite(stream); throw e }
    val delta = (if (!value.optBoolean("done")) 1 else 0) - (if (previous != null && !previous.optBoolean("done")) 1 else 0)
    if (delta != 0) {
      val prefs = c.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE)
      check(prefs.edit().putInt("pending_$owner", (prefs.getInt("pending_$owner", 0) + delta).coerceAtLeast(0)).commit())
    }
  }
  fun bytes(c: Context, owner: String, key: String) = File(directory(c, owner), "$key.original")
  fun status(c: Context): Map<String, Any?> {
    val owner = owner(c)
    val prefs = c.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE)
    val pending = if (owner == null) 0 else maxOf(prefs.getInt("pending_$owner", 0),
      if (prefs.getString("remote_owner", null) == owner) prefs.getInt("remote_count", 0) else 0)
    return mapOf("pending" to pending, "status" to prefs.getString("state", "Waiting for glasses"))
  }
  fun state(c: Context, text: String) { c.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE).edit().putString("state", text).apply() }
}
