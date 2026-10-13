package expo.modules.digitalbrainruntime

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.AtomicFile
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.File

internal enum class RecordingSaveState { CAPTURING, FINISHING, QUEUED, ENCODING, COPYING, VERIFYING, FAILED }
internal class RecordingSaveJob(val id: String, val dir: File, val name: String, val startedAt: Long, var baseUri: String) {
  var state = RecordingSaveState.CAPTURING
  var reason = GlassesRecordingStop.USER
  var bytes = 0L
  var progress: Int? = null
  @Volatile var targetUri: String? = null
  var drainWake: PowerManager.WakeLock? = null
  fun metadata() = JSONObject().put("id", id).put("name", name).put("startedAt", startedAt)
    .put("baseUri", baseUri).put("reason", reason.name).apply { targetUri?.let { put("targetUri", it) } }
}

/** Main-looper queue ownership; encoding, SAF calls and journals are isolated on IO. */
internal object GlassesRecordingSaves {
  const val LIMIT = 4
  private val main = Handler(Looper.getMainLooper())
  private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
  private val jobs = RecordingSaveQueue(LIMIT)
  private var context: Context? = null
  private var initialized = false
  private var preparing = 0
  var ready = false
    private set
  private var processing = false
  @Volatile private var suspended = false
  var revision = 0L
    private set
  val hasRoom get() = ready && jobs.size + preparing < LIMIT
  val working get() = !suspended && jobs.values.any { it.state !in setOf(RecordingSaveState.CAPTURING, RecordingSaveState.FAILED) }
  private fun root(c: Context) = File(c.filesDir, "glasses-audio/jobs").apply { mkdirs() }
  private fun journal(dir: File) = AtomicFile(File(dir, "current.json"))
  private fun write(job: RecordingSaveJob) {
    val file = journal(job.dir); val output = file.startWrite()
    try { output.write(job.metadata().toString().toByteArray()); file.finishWrite(output) }
    catch (e: Exception) { file.failWrite(output); throw e }
  }
  fun initialize(c: Context) {
    context = c.applicationContext
    if (initialized) return
    initialized = true
    scope.launch {
      try {
      val recovered = withContext(Dispatchers.IO) {
        // Upgrade the previous singleton spool without deleting recovery audio.
        val legacy = File(c.filesDir, "glasses-audio")
        val old = AtomicFile(File(legacy, "current.json"))
        if (old.baseFile.exists()) runCatching {
          val data = JSONObject(old.openRead().use { String(it.readBytes()) })
          val id = "legacy-recovery"
          val dir = File(root(c), id).apply { mkdirs() }
          if (!journal(dir).baseFile.exists()) {
            val job = RecordingSaveJob(id, dir, data.getString("name"), System.currentTimeMillis(), data.getString("baseUri"))
            job.targetUri = data.optString("targetUri").takeIf { it.isNotBlank() }
            job.reason = GlassesRecordingStop.RECOVERED; write(job)
          }
          for (name in listOf("capture.pcm", "output.m4a")) {
            val file = File(legacy, name)
            if (file.exists()) check(file.renameTo(File(dir, name)))
          }
          old.delete()
        }
        root(c).listFiles()?.filter { journal(it).baseFile.exists() }?.mapNotNull { dir ->
          runCatching {
            val data = JSONObject(journal(dir).openRead().use { String(it.readBytes()) })
            RecordingSaveJob(dir.name, dir, data.getString("name"), data.optLong("startedAt", System.currentTimeMillis()), data.getString("baseUri")).apply {
              reason = GlassesRecordingStop.RECOVERED
              bytes = File(dir, "capture.pcm").length()
              targetUri = data.optString("targetUri").takeIf { it.isNotBlank() }
              state = RecordingSaveState.QUEUED
            }
          }.getOrElse {
            RecordingSaveJob(dir.name, dir, "Recovered-${dir.name.take(6)}.m4a", dir.lastModified(), "").apply {
              reason = GlassesRecordingStop.RECOVERED; bytes = File(dir, "capture.pcm").length()
              state = RecordingSaveState.FAILED
            }
          }
        } ?: emptyList()
      }
      recovered.sortedBy { it.startedAt }.forEach { jobs.restore(it) }
      ready = true; revision++
      processNext()
      } catch (_: Exception) { initialized = false; ready = false }
    }
  }
  suspend fun prepare(c: Context, baseUri: String): RecordingSaveJob {
    check(!suspended || !processing) { "Wait for the interrupted save to finish stopping" }
    suspended = false
    check(hasRoom) { "Let a recording finish saving or retry a pending save first" }
    preparing++
    val job = try { withContext(Dispatchers.IO) {
      val base = DocumentFile.fromTreeUri(c, Uri.parse(baseUri))
      check(base?.isDirectory == true && base.canWrite()) { "Choose a writable Digital Brain folder" }
      val folder = root(c); check(folder.usableSpace > 16 * 1024 * 1024) { "Not enough space for a recording" }
      val id = java.util.UUID.randomUUID().toString()
      val dir = File(folder, id).apply { check(mkdir()) }
      val started = System.currentTimeMillis()
      val name = "Glasses-${java.text.SimpleDateFormat("yyyy-MM-dd-HH-mm-ss", java.util.Locale.US).format(java.util.Date(started))}-${id.take(6)}.m4a"
      RecordingSaveJob(id, dir, name, started, baseUri).also {
        try { write(it) } catch (e: Exception) { dir.deleteRecursively(); throw e }
      }
    } } finally { preparing-- }
    jobs[job.id] = job; revision++
    return job
  }
  fun finishing(job: RecordingSaveJob, reason: GlassesRecordingStop, bytes: Long = 0) {
    if (job.state != RecordingSaveState.CAPTURING) return
    job.bytes = bytes
    job.reason = reason; job.state = RecordingSaveState.FINISHING
    job.drainWake = context?.getSystemService(PowerManager::class.java)
      ?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DigitalBrain:RecordingDrain")?.apply { acquire(30 * 60_000L) }
    revision++
    DigitalBrainRuntime.service?.refresh(forceNotification = true)
  }
  fun drained(job: RecordingSaveJob, bytes: Long) {
    job.bytes = bytes
    if (job.state == RecordingSaveState.CAPTURING) finishing(job, GlassesRecordingStop.STORAGE)
    job.state = if (suspended) RecordingSaveState.FAILED else RecordingSaveState.QUEUED
    job.drainWake?.let { if (it.isHeld) it.release() }; job.drainWake = null
    revision++; processNext()
  }
  private fun processNext() {
    if (processing || suspended) return
    val job = jobs.nextReady() ?: run {
      DigitalBrainRuntime.service?.refresh(forceNotification = true); return
    }
    val c = context ?: return
    processing = true
    try {
      if (DigitalBrainRuntime.service != null) DigitalBrainRuntime.service?.refresh(forceNotification = true)
      else {
        check(DigitalBrainRuntime.activityVisible) { "Open Recordings to finish saving" }
        DigitalBrainRuntime.refresh(c)
      }
    } catch (_: RuntimeException) {
      job.state = RecordingSaveState.FAILED; revision++; processing = false; return
    }
    scope.launch {
      val wake = c.getSystemService(PowerManager::class.java)
        .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DigitalBrain:RecordingSave").apply { acquire(30 * 60_000L) }
      try {
        withContext(Dispatchers.IO) { save(c, job) }
        jobs.remove(job.id); revision++
      } catch (_: Exception) { job.state = RecordingSaveState.FAILED; job.progress = null; revision++ }
      finally { if (wake.isHeld) wake.release(); processing = false; processNext() }
    }
  }
  private fun stage(job: RecordingSaveJob, state: RecordingSaveState, progress: Int? = null) {
    main.post { if (!suspended && jobs[job.id] === job) { job.state = state; job.progress = progress } }
  }
  private fun save(c: Context, job: RecordingSaveJob) {
    write(job)
    val pcm = File(job.dir, "capture.pcm"); val output = File(job.dir, "output.m4a")
    if (pcm.length() % 2 != 0L) java.io.RandomAccessFile(pcm, "rw").use { it.setLength(pcm.length() - 1) }
    if (pcm.length() > 0) {
      if (!output.exists() || output.length() == 0L) {
        stage(job, RecordingSaveState.ENCODING)
        val temp = File(job.dir, "encoding.m4a"); temp.delete()
        GlassesM4aEncoder.encode(pcm, temp) { !suspended }; check(temp.renameTo(output))
      }
      stage(job, RecordingSaveState.COPYING, 0)
      val base = checkNotNull(DocumentFile.fromTreeUri(c, Uri.parse(job.baseUri)))
      check(base.canWrite())
      val folder = base.findFile("Recordings") ?: base.createDirectory("Recordings")
      check(folder?.isDirectory == true && folder.canWrite())
      val prior = job.targetUri?.let { DocumentFile.fromSingleUri(c, Uri.parse(it)) }
      val target = prior?.takeIf { it.exists() } ?: checkNotNull(folder.createFile("audio/mp4", job.name))
      job.targetUri = target.uri.toString(); write(job)
      var lastReport = 0L
      val copied = output.inputStream().buffered().use { input ->
        checkNotNull(c.contentResolver.openOutputStream(target.uri, "wt")).buffered().use { out ->
          val buffer = ByteArray(64 * 1024); var total = 0L
          while (true) {
            check(!suspended) { "Recording save interrupted" }
            val count = input.read(buffer); if (count < 0) break
            out.write(buffer, 0, count); total += count
            if (total - lastReport >= 4L * 1024 * 1024 || total == output.length()) {
              stage(job, RecordingSaveState.COPYING, (total * 100 / output.length()).toInt().coerceIn(0, 100)); lastReport = total
            }
          }
          out.flush(); total
        }
      }
      stage(job, RecordingSaveState.VERIFYING)
      check(copied == output.length() && target.length() == copied && copied > 0)
    }
    check(!suspended) { "Recording save interrupted" }
    check(job.dir.deleteRecursively())
  }
  suspend fun retry(c: Context, baseUri: String, id: String?) {
    initialize(c)
    check(!suspended || !processing) { "Wait for the interrupted save to finish stopping" }
    suspended = false
    val selected = jobs.values.filter { it.state == RecordingSaveState.FAILED && (id == null || it.id == id) }
    check(selected.isNotEmpty()) { "No recording needs retrying" }
    withContext(Dispatchers.IO) {
      val base = DocumentFile.fromTreeUri(c, Uri.parse(baseUri))
      check(base?.canWrite() == true) { "Choose a writable Digital Brain folder" }
    }
    selected.forEach { job ->
      if (job.baseUri != baseUri) job.targetUri = null
      job.baseUri = baseUri; job.state = RecordingSaveState.QUEUED; job.progress = null
    }
    revision++; processNext()
  }
  fun pauseForTimeout() {
    suspended = true
    jobs.values.filter { it.state != RecordingSaveState.CAPTURING }.forEach {
      it.state = RecordingSaveState.FAILED; it.progress = null
      it.drainWake?.let { lock -> if (lock.isHeld) lock.release() }; it.drainWake = null
    }
    revision++
  }
  fun snapshot(): List<Map<String, Any?>> = jobs.values.filter { it.state != RecordingSaveState.CAPTURING }
    .sortedByDescending { it.startedAt }.map { job ->
      mapOf("id" to job.id, "name" to job.name, "startedAtMs" to job.startedAt,
        "durationMs" to GlassesRecordingPolicy.presentationUs(job.bytes) / 1000,
        "state" to job.state.name, "progress" to job.progress, "targetUri" to job.targetUri)
    }
  fun diagnostics(): Map<String, Any> = mapOf("pending" to jobs.size, "processing" to processing,
    "failed" to jobs.values.count { it.state == RecordingSaveState.FAILED })
}
