package expo.modules.digitalbrainruntime

import android.app.ActivityManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.work.*
import com.rnwhisper.GlassesWhisperBridge
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Native replay only. It deliberately has no dependency on GlassesCommandExecutor. */
class SpeechEvalWorker(c: Context, params: WorkerParameters) : CoroutineWorker(c, params) {
  private val c = applicationContext
  @Volatile private var bridge: GlassesWhisperBridge? = null
  @Volatile private var connection: HttpURLConnection? = null
  private var loaded: String? = null
  private var owner = ""
  private var runId = ""
  private val timeout = Executors.newSingleThreadScheduledExecutor()
  private fun guard() {
    check(!isStopped && RuntimeGlasses.signedIn(c) && GlassesWakeStore.account(c) == owner) { "interrupted" }
    val run = SpeechEvalStore.readMeta(c, owner)
    check(run?.optString("id") == runId && run.runState() in setOf(SpeechEvalRunState.READY, SpeechEvalRunState.RUNNING)) { "interrupted" }
  }
  private fun persist(value: JSONObject) {
    synchronized(SpeechEvalStore) {
      guard()
      SpeechEvalStore.save(c, owner, value)
    }
  }
  override suspend fun doWork(): Result = mutex.withLock {
    withContext(Dispatchers.IO) {
      owner = inputData.getString("owner") ?: return@withContext Result.failure()
      val value = SpeechEvalStore.read(c, owner) ?: return@withContext Result.success()
      runId = value.getString("id")
      if (!RuntimeGlasses.signedIn(c) || GlassesWakeStore.account(c) != owner || value.runState() == SpeechEvalRunState.PAUSED) return@withContext Result.success()
      try {
        running = true
        timeout.scheduleAtFixedRate({
          if (isStopped || !RuntimeGlasses.signedIn(c) || GlassesWakeStore.account(c) != owner) {
            bridge?.abort(); connection?.disconnect()
          }
        }, 0, 250, TimeUnit.MILLISECONDS)
        setForeground(notification())
        withContext(Dispatchers.Main) { GlassesListening.pauseForComparison() }
        GlassesListening.awaitSpeechWorker()
        guard()
        value.put("state", SpeechEvalRunState.RUNNING.key).put("message", "Preparing models")
        persist(value)
        bridge = GlassesWhisperBridge(1700000011)
        val deadline = SystemClock.elapsedRealtime() + 6 * 60_000
        val rows = value.getJSONArray("results")
        val existing = SpeechEvalStore.array(rows).map { it.getString("key") }.toMutableSet()
        val samples = SpeechEvalStore.array(value.getJSONArray("samples"))
        val candidates = value.getJSONArray("candidates")
        for (candidateIndex in 0 until candidates.length()) {
          val candidate = candidates.getString(candidateIndex)
          val server = SpeechEvalServerCandidate.parse(candidate)
          val unfinished = samples.any { sample -> (0..value.getInt("repeats")).any { repetition -> "$candidate:${sample.getInt("index")}:$repetition" !in existing } }
          if (!unfinished) continue
          guard()
          val candidateLabel = SpeechEvalServerCandidate.parse(candidate)?.label ?: candidate.removePrefix("local:")
          value.put("message", "Preparing $candidateLabel")
          persist(value)
          var preparationError: Exception? = null
          val preparationStarted = SystemClock.elapsedRealtime()
          var model: File? = null
          try {
            if (candidate.startsWith("local:")) model = download(candidate)
            else request(candidate, "prepare")
          } catch (e: CancellationException) { throw e }
          catch (error: Exception) { guard(); preparationError = error }
          val preparationMs = SystemClock.elapsedRealtime() - preparationStarted
          for (sample in samples) for (repetition in 0..value.getInt("repeats")) {
            val key = "$candidate:${sample.getInt("index")}:$repetition"
            if (key in existing) continue
            guard()
            if (SystemClock.elapsedRealtime() >= deadline) {
              value.put("message", "Continuing shortly"); persist(value)
              return@withContext Result.retry()
            }
            val row = JSONObject().put("key", key).put("candidate", candidate).put("sample", sample.getString("audioFile"))
              .put("sampleSha256", sample.getString("sha256")).put("mode", (server?.modes ?: listOf("cold", "warm"))[if (repetition == 0) 0 else 1])
              .put("repeat", repetition).put("expectedCommand", sample.get("expectedCommand"))
              .put("tags", sample.optJSONArray("tags") ?: JSONArray())
            val started = SystemClock.elapsedRealtime()
            try {
              preparationError?.let { throw it }
              val audio = File(SpeechEvalStore.root(c, owner), "$runId/${sample.getInt("index")}.wav").readBytes()
              require(SpeechEvalStore.hash(audio) == sample.getString("sha256")) { "audio_changed" }
              val pcm = SpeechEvalPolicy.pcm(audio)
              // After interruption, explicitly prepare a warm context before measuring a warm replay.
              val text: String
              if (candidate.startsWith("local:")) {
                val path = checkNotNull(model)
                val metadata = SpeechEvalStore.local(c, candidate)
                row.put("decoding", JSONObject().put("language", "en").put("strategy", "greedy").put("bestOf", 5)
                  .put("temperature", 0).put("temperatureIncrement", 0.2).put("noContext", true))
                row.put("modelSha256", metadata.getString("sha256")).put("modelRevision", metadata.getString("revision"))
                if (repetition > 0 && loaded != candidate) {
                  load(path, candidate); transcribe(pcm)
                  row.put("resumeWarmupMs", SystemClock.elapsedRealtime() - started)
                }
                guard()
                val timed = SpeechTiming({ SystemClock.elapsedRealtime() })
                val alreadyLoaded = repetition > 0 && loaded == candidate
                timed.measure("modelLoadMs") { if (!alreadyLoaded) { bridge?.release(); loaded = null; load(path, candidate) } }
                text = timed.measure("inferenceMs") { transcribe(pcm) }
                row.put("modelAlreadyLoaded", alreadyLoaded).put("modelLoadMs", timed.duration("modelLoadMs"))
                  .put("inferenceMs", timed.duration("inferenceMs")).put("totalMs", timed.elapsed())
                  .put("engine", "whisper.rn-0.5.5").put("cpuThreads", 2)
              } else {
                // Each warm replay follows an unscored request, even across server process eviction.
                if (repetition > 0) {
                  val warmupStart = SystemClock.elapsedRealtime()
                  request(candidate, "transcribe", false, audio)
                  row.put("warmupMs", SystemClock.elapsedRealtime() - warmupStart)
                }
                guard()
                val requestStart = SystemClock.elapsedRealtime()
                val response = request(candidate, "transcribe", repetition == 0, audio)
                val roundTrip = SystemClock.elapsedRealtime() - requestStart
                for (field in response.keys()) row.put(field, response.get(field))
                row.put("roundTripMs", roundTrip).put("totalMs", roundTrip)
                text = response.getString("text")
                if (repetition > 0 && server?.remote != true && !response.optBoolean("modelAlreadyLoaded")) throw IllegalStateException("warm_context_lost")
              }
              guard()
              val matchingAt = SystemClock.elapsedRealtime()
              val decision = GlassesCommandMatcher.match(text)
              val matchingMs = SystemClock.elapsedRealtime() - matchingAt
              row.put("matchingMs", matchingMs).put("totalMs", row.getLong("totalMs") + matchingMs)
              row.put("text", text).put("command", decision.command?.key ?: JSONObject.NULL)
                .put("commandMatch", decision.match.name.lowercase()).put("wakeMatch", decision.wakeMatch.name.lowercase())
                .put("correct", (if (sample.isNull("expectedCommand")) null else sample.getString("expectedCommand")) == decision.command?.key)
                .put("expectedTranscript", sample.getString("expectedTranscript"))
              val errors = SpeechEvalPolicy.errors(sample.getString("expectedTranscript"), text)
              row.put("wordErrors", errors.first).put("referenceWords", errors.second).put("status", SpeechEvalResult.COMPLETE.key)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
              guard()
              row.put("status", SpeechEvalResult.FAILED.key)
                .put("attemptDurationMs", SystemClock.elapsedRealtime() - started)
              SpeechEvalFailures.annotate(row, e)
              if (preparationError != null) row.put("preparationDurationMs", preparationMs)
              bridge?.release(); loaded = null
            } catch (_: OutOfMemoryError) {
              row.put("status", SpeechEvalResult.FAILED.key).put("error", "insufficient_memory")
              bridge?.release(); loaded = null
            } catch (_: LinkageError) {
              row.put("status", SpeechEvalResult.FAILED.key).put("error", "unsupported_model")
              bridge?.release(); loaded = null
            }
            row.put("processPssKb", android.os.Debug.getPss())
            val network = c.getSystemService(android.net.ConnectivityManager::class.java)
            val caps = network.getNetworkCapabilities(network.activeNetwork)
            row.put("network", when {
              caps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) == true -> "wifi"
              caps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) == true -> "cellular"
              else -> "other"
            })
            synchronized(SpeechEvalStore) {
              guard()
              row.put("sequence", rows.length())
              SpeechEvalStore.result(c, owner, runId, row)
            }
            rows.put(row); existing.add(key)
            value.put("message", "${rows.length()} results saved")
            persist(value)
          }
        }
        value.put("state", SpeechEvalRunState.COMPLETE.key).put("message", "Comparison complete")
        persist(value)
        Result.success()
      } catch (e: CancellationException) {
        pauseJournal()
        throw e
      }
      catch (_: Exception) {
        pauseJournal()
        Result.success()
      } finally {
        bridge?.release(); bridge = null; loaded = null
        running = false
        connection?.disconnect(); connection = null
        timeout.shutdownNow()
        withContext(kotlinx.coroutines.NonCancellable + Dispatchers.Main) {
          val current = SpeechEvalStore.readMeta(c, owner)
          if (current?.runState() != SpeechEvalRunState.RUNNING) {
            GlassesListening.endComparison()
            if (DigitalBrainRuntime.activityVisible) GlassesListening.resumeForeground(c)
          }
        }
      }
    }
  }
  private fun pauseJournal() {
    synchronized(SpeechEvalStore) {
      val latest = SpeechEvalStore.readMeta(c, owner)
      if (latest?.optString("id") == runId && latest.runState() !in setOf(SpeechEvalRunState.PAUSED, SpeechEvalRunState.COMPLETE)) {
        latest.put("state", SpeechEvalRunState.PAUSED.key).put("message", "Test paused. Resume when you are ready.")
        SpeechEvalStore.save(c, owner, latest)
      }
    }
  }
  private fun load(file: File, candidate: String) {
    val info = ActivityManager.MemoryInfo()
    c.getSystemService(ActivityManager::class.java).getMemoryInfo(info)
    require(!info.lowMemory && info.availMem > file.length() * 2 + 256L * 1024 * 1024) { "insufficient_memory" }
    if (loaded != candidate) { bridge!!.release(); loaded = null }
    bridge!!.load(file.path); loaded = candidate
  }
  private fun transcribe(pcm: FloatArray): String {
    guard()
    val timedOut = java.util.concurrent.atomic.AtomicBoolean(false)
    val engine = bridge!!
    val task = timeout.schedule({ timedOut.set(true); engine.abort() }, 60, TimeUnit.SECONDS)
    try {
      val text = engine.transcribe(pcm)
      check(!timedOut.get()) { "inference_timeout" }
      guard(); return text
    } finally { task.cancel(false) }
  }
  private fun download(candidate: String): File {
    val model = SpeechEvalStore.local(c, candidate)
    val folder = File(c.filesDir, "speech-eval-models").apply { mkdirs() }
    val target = File(folder, model.getString("file"))
    if (target.length() == model.getLong("bytes") && SpeechEvalStore.hash(target) == model.getString("sha256")) return target
    require(folder.usableSpace > model.getLong("bytes") + 128 * 1024 * 1024) { "Free some phone storage first" }
    val temporary = File(folder, target.name + ".download")
    val url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/${model.getString("revision")}/${target.name}"
    val http = URL(url).openConnection() as HttpURLConnection
    connection = http
    try {
      http.connectTimeout = 20_000; http.readTimeout = 30_000
      check(http.responseCode == 200)
      var size = 0L
      http.inputStream.use { input -> FileOutputStream(temporary).use { output ->
        val buffer = ByteArray(65536)
        while (true) { guard(); val count = input.read(buffer); if (count < 0) break
          size += count; require(size <= model.getLong("bytes")); output.write(buffer, 0, count) }
        output.fd.sync()
      } }
      require(size == model.getLong("bytes") && SpeechEvalStore.hash(temporary) == model.getString("sha256"))
      check(temporary.renameTo(target)); return target
    } finally { http.disconnect(); connection = null; temporary.delete() }
  }
  private fun request(candidate: String, operation: String, cold: Boolean = false, audio: ByteArray = byteArrayOf()): JSONObject {
    guard()
    val config = checkNotNull(RuntimeLocationUploadConfigStore.load(c)) { "Configure your server first" }
    val authStart = SystemClock.elapsedRealtime()
    val token = checkNotNull(RuntimeGoogleIdTokenProvider.getFreshIdToken(c, config.googleWebClientId))
    guard()
    val authMs = SystemClock.elapsedRealtime() - authStart
    val selection = checkNotNull(SpeechEvalServerCandidate.parse(candidate))
    val http = URL("${config.apiBaseUrl.trimEnd('/')}/mobile/glasses/speech-eval/$operation?${selection.query}&cold=$cold").openConnection() as HttpURLConnection
    connection = http
    try {
      http.requestMethod = "POST"; http.instanceFollowRedirects = false
      http.connectTimeout = 20_000; http.readTimeout = if (operation == "prepare") 620_000 else 200_000
      http.setRequestProperty("Authorization", "Bearer $token")
      http.setRequestProperty("Content-Type", "audio/wav")
      http.doOutput = true; http.setFixedLengthStreamingMode(audio.size)
      val uploadStart = SystemClock.elapsedRealtime()
      http.outputStream.use { it.write(audio) }
      val uploadMs = SystemClock.elapsedRealtime() - uploadStart
      val responseStart = SystemClock.elapsedRealtime()
      if (http.responseCode !in 200..299) {
        val body = runCatching { JSONObject(String(readResponse(http.errorStream), Charsets.UTF_8)) }.getOrNull()
        throw SpeechEvalFailures.http(http.responseCode, body)
      }
      val bytes = readResponse(http.inputStream)
      val response = JSONObject(String(bytes, Charsets.UTF_8))
      selection.validateResponse(response)
      guard(); return response.put("authMs", authMs)
        .put("requestWriteMs", uploadMs).put("responseWaitMs", SystemClock.elapsedRealtime() - responseStart)
    } finally { http.disconnect(); connection = null }
  }
  private fun readResponse(stream: java.io.InputStream?): ByteArray = checkNotNull(stream).use { input ->
    val out = java.io.ByteArrayOutputStream(); val buffer = ByteArray(4096)
    while (out.size() <= 32768) { val count = input.read(buffer); if (count < 0) break; out.write(buffer, 0, count) }
    require(out.size() <= 32768)
    out.toByteArray()
  }
  private fun notification(): ForegroundInfo {
    val manager = c.getSystemService(NotificationManager::class.java)
    manager.createNotificationChannel(NotificationChannel("speech_eval", "Speech comparisons", NotificationManager.IMPORTANCE_LOW).apply { setSound(null, null) })
    val notification = NotificationCompat.Builder(c, "speech_eval").setSmallIcon(android.R.drawable.ic_dialog_info)
      .setContentTitle("Comparing speech recognition").setContentText("Your test is running. Open Listening to see progress.")
      .setOngoing(true).setSilent(true).build()
    return ForegroundInfo(4320, notification, if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0)
  }
  companion object {
    const val WORK = "glasses-speech-eval"
    private val mutex = Mutex()
    @Volatile var running = false
      private set
    fun enqueue(c: Context, owner: String) {
      WorkManager.getInstance(c).enqueueUniqueWork(WORK, ExistingWorkPolicy.KEEP,
        OneTimeWorkRequestBuilder<SpeechEvalWorker>().setInputData(workDataOf("owner" to owner))
          .setBackoffCriteria(BackoffPolicy.LINEAR, 10, TimeUnit.SECONDS).build())
    }
    fun busy(c: Context) = WorkManager.getInstance(c).getWorkInfosForUniqueWork(WORK).get(5, TimeUnit.SECONDS).any { !it.state.isFinished }
    fun pause(c: Context) {
      val owner = SpeechEvalStore.owner(c)
      synchronized(SpeechEvalStore) {
        SpeechEvalStore.read(c, owner)?.let { if (it.runState() != SpeechEvalRunState.COMPLETE) {
          it.put("state", SpeechEvalRunState.PAUSED.key).put("message", "Test paused"); SpeechEvalStore.save(c, owner, it)
        } }
      }
      WorkManager.getInstance(c).cancelUniqueWork(WORK).result.get(5, TimeUnit.SECONDS)
    }
  }
}
