package expo.modules.fastvision

import android.graphics.Bitmap
import android.graphics.ColorSpace
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.SystemClock
import com.google.android.gms.common.ConnectionResult
import com.google.android.gms.common.GoogleApiAvailability
import com.google.android.gms.common.api.OptionalModuleApi
import com.google.android.gms.common.moduleinstall.InstallStatusListener
import com.google.android.gms.common.moduleinstall.ModuleInstall
import com.google.android.gms.common.moduleinstall.ModuleInstallRequest
import com.google.android.gms.common.moduleinstall.ModuleInstallStatusUpdate
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.common.MlKitException
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.label.ImageLabeler
import com.google.mlkit.vision.label.defaults.ImageLabelerOptions
import com.google.mlkit.vision.label.ImageLabeling
import com.google.mlkit.vision.text.TextRecognizer
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.objectdetector.ObjectDetector
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException
import java.util.concurrent.TimeUnit
import kotlin.math.exp
import kotlin.math.min
import org.tensorflow.lite.DataType
import org.tensorflow.lite.Interpreter

class FastVisionModule : Module() {
  private data class StageResult<T>(
    val value: T?,
    val elapsedMs: Long,
    val error: Map<String, Any?>?,
  )

  private data class TextEvidence(
    val lines: List<String>,
    val blocks: List<Map<String, Any?>>,
  )

  private var textRecognizer: TextRecognizer? = null
  private var imageLabeler: ImageLabeler? = null
  private var objectDetector: ObjectDetector? = null
  private var detectorModelBuffer: MappedByteBuffer? = null
  private var sceneClassifier: Interpreter? = null
  private var sceneLabels: List<String> = emptyList()
  private var sceneIndoorOutdoor: List<Int> = emptyList()

  private fun context() = appContext.reactContext
    ?: throw IllegalStateException("Android application context is unavailable.")

  private fun textRecognizer(): TextRecognizer = textRecognizer
    ?: TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS).also { textRecognizer = it }

  private fun imageLabeler(): ImageLabeler = imageLabeler
    ?: ImageLabeling.getClient(ImageLabelerOptions.DEFAULT_OPTIONS).also { imageLabeler = it }

  private fun optionalApis(): Array<OptionalModuleApi> = arrayOf(textRecognizer(), imageLabeler())

  private fun mlKitModulesAvailable(): Boolean {
    val client = ModuleInstall.getClient(context())
    return Tasks.await(client.areModulesAvailable(*optionalApis())).areModulesAvailable()
  }

  private fun closeMlKitClients() {
    textRecognizer?.close()
    textRecognizer = null
    imageLabeler?.close()
    imageLabeler = null
  }

  private fun closeResources() {
    objectDetector?.close()
    objectDetector = null
    detectorModelBuffer = null
    sceneClassifier?.close()
    sceneClassifier = null
    sceneLabels = emptyList()
    sceneIndoorOutdoor = emptyList()
    closeMlKitClients()
  }

  private fun uriFor(value: String): Uri = if (value.contains("://")) {
    Uri.parse(value)
  } else {
    Uri.fromFile(File(value))
  }

  private fun decodeBitmap(imageUri: String): Bitmap {
    val source = ImageDecoder.createSource(context().contentResolver, uriFor(imageUri))
    val decoded = ImageDecoder.decodeBitmap(source) { decoder, _, _ ->
      decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
      decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB))
    }
    if (decoded.config == Bitmap.Config.ARGB_8888) return decoded
    val converted = decoded.copy(Bitmap.Config.ARGB_8888, false)
      ?: throw IllegalStateException("Could not convert the selected image to ARGB_8888.")
    decoded.recycle()
    return converted
  }

  private fun elapsedMs(startNanos: Long): Long =
    TimeUnit.NANOSECONDS.toMillis(SystemClock.elapsedRealtimeNanos() - startNanos)

  private fun normalizedSceneLabel(line: String): String {
    val path = line.substringBeforeLast(' ')
    return path.removePrefix("/").substringAfter('/').replace('_', ' ').replace('/', ' ')
  }

  private fun putTensorFloat(buffer: ByteBuffer, type: DataType, value: Float) {
    require(type == DataType.FLOAT32) { "Unsupported scene input tensor type: $type" }
    buffer.putFloat(value)
  }

  private fun readTensorFloat(buffer: ByteBuffer, type: DataType): Float {
    require(type == DataType.FLOAT32) { "Unsupported scene output tensor type: $type" }
    return buffer.float
  }

  private fun classifyScene(bitmap: Bitmap): Map<String, Any> {
    val classifier = sceneClassifier ?: throw IllegalStateException("Scene classifier is not loaded.")
    check(sceneLabels.size == 365 && sceneIndoorOutdoor.size == 365) {
      "Places365 metadata is malformed."
    }
    val cropSize = min(bitmap.width, bitmap.height)
    val left = (bitmap.width - cropSize) / 2
    val top = (bitmap.height - cropSize) / 2
    val cropped = Bitmap.createBitmap(bitmap, left, top, cropSize, cropSize)
    val scaled = Bitmap.createScaledBitmap(cropped, 224, 224, true)
    if (cropped !== bitmap && cropped !== scaled) cropped.recycle()
    try {
      val pixels = IntArray(224 * 224)
      scaled.getPixels(pixels, 0, 224, 0, 0, 224, 224)
      val inputTensor = classifier.getInputTensor(0)
      val inputShape = inputTensor.shape()
      val inputType = inputTensor.dataType()
      val channelsFirst = inputShape.contentEquals(intArrayOf(1, 3, 224, 224))
      val channelsLast = inputShape.contentEquals(intArrayOf(1, 224, 224, 3))
      require(channelsFirst || channelsLast) {
        "Unsupported Places365 input shape: ${inputShape.contentToString()}"
      }
      val input = ByteBuffer.allocateDirect(inputTensor.numBytes()).order(ByteOrder.nativeOrder())
      val means = floatArrayOf(0.485f, 0.456f, 0.406f)
      val standardDeviations = floatArrayOf(0.229f, 0.224f, 0.225f)
      if (channelsFirst) {
        for (channel in 0..2) {
          for (pixel in pixels) {
            val component = when (channel) {
              0 -> (pixel shr 16) and 0xff
              1 -> (pixel shr 8) and 0xff
              else -> pixel and 0xff
            }
            putTensorFloat(
              input,
              inputType,
              (component / 255f - means[channel]) / standardDeviations[channel],
            )
          }
        }
      } else {
        for (pixel in pixels) {
          for (channel in 0..2) {
            val component = when (channel) {
              0 -> (pixel shr 16) and 0xff
              1 -> (pixel shr 8) and 0xff
              else -> pixel and 0xff
            }
            putTensorFloat(
              input,
              inputType,
              (component / 255f - means[channel]) / standardDeviations[channel],
            )
          }
        }
      }
      input.rewind()
      val outputTensor = classifier.getOutputTensor(0)
      val outputElements = outputTensor.shape().fold(1) { total, dimension -> total * dimension }
      require(outputElements == 365) {
        "Unsupported Places365 output shape: ${outputTensor.shape().contentToString()}"
      }
      val output = ByteBuffer.allocateDirect(outputTensor.numBytes()).order(ByteOrder.nativeOrder())
      classifier.run(input, output)
      output.rewind()
      val logits = FloatArray(outputElements) { readTensorFloat(output, outputTensor.dataType()) }
      val maxLogit = logits.maxOrNull() ?: 0f
      val weights = DoubleArray(logits.size) { index -> exp((logits[index] - maxLogit).toDouble()) }
      val denominator = weights.sum().coerceAtLeast(1e-12)
      val probabilities = DoubleArray(weights.size) { index -> weights[index] / denominator }
      var indoorProbability = 0.0
      for (index in probabilities.indices) {
        if (sceneIndoorOutdoor[index] == 1) indoorProbability += probabilities[index]
      }
      val topScenes = probabilities.indices
        .sortedByDescending { probabilities[it] }
        .take(5)
        .map { index ->
          mapOf(
            "label" to sceneLabels[index],
            "confidence" to probabilities[index],
            "settingType" to if (sceneIndoorOutdoor[index] == 1) "indoor" else "outdoor",
          )
        }
      return mapOf(
        "scenes" to topScenes,
        "indoorProbability" to indoorProbability,
        "outdoorProbability" to (1.0 - indoorProbability),
      )
    } finally {
      if (scaled !== bitmap) scaled.recycle()
    }
  }

  private fun rootCause(error: Throwable): Throwable {
    var current = error
    while ((current is ExecutionException || current is java.util.concurrent.CompletionException) &&
      current.cause != null
    ) {
      current = current.cause!!
    }
    return current
  }

  private fun emitAnalysisProgress(
    stage: String,
    status: String,
    elapsedMs: Long? = null,
    errorCode: Int? = null,
  ) {
    sendEvent(
      "onFastVisionProgress",
      mapOf(
        "stage" to stage,
        "status" to status,
        "elapsedMs" to elapsedMs,
        "errorCode" to errorCode,
      ),
    )
  }

  private fun <T> runMlKitStage(
    stage: String,
    resetClient: () -> Unit,
    operation: () -> T,
  ): StageResult<T> {
    val started = SystemClock.elapsedRealtimeNanos()
    emitAnalysisProgress(stage, "starting")
    for (attempt in 0..1) {
      try {
        val value = operation()
        val duration = elapsedMs(started)
        emitAnalysisProgress(stage, "completed", duration)
        return StageResult(value, duration, null)
      } catch (error: Throwable) {
        val cause = rootCause(error)
        val code = (cause as? MlKitException)?.errorCode
        if (attempt == 0) {
          emitAnalysisProgress(stage, "retrying", elapsedMs(started), code)
          resetClient()
          Thread.sleep(350)
          continue
        }
        val duration = elapsedMs(started)
        emitAnalysisProgress(stage, "failed", duration, code)
        return StageResult(
          null,
          duration,
          mapOf(
            "stage" to stage,
            "message" to "ML Kit component failed after one client reinitialization retry.",
            "errorCode" to code,
          ),
        )
      }
    }
    error("Unreachable ML Kit retry state.")
  }

  override fun definition() = ModuleDefinition {
    Name("FastVision")
    Events("onMlKitInstallProgress", "onFastVisionProgress")

    AsyncFunction("getSupportStatus") {
      val playServicesStatus = GoogleApiAvailability.getInstance()
        .isGooglePlayServicesAvailable(context())
      val supported = playServicesStatus == ConnectionResult.SUCCESS
      val available = if (supported) {
        try {
          runCatching { mlKitModulesAvailable() }.getOrDefault(false)
        } finally {
          if (objectDetector == null) {
            closeMlKitClients()
          }
        }
      } else {
        false
      }
      mapOf(
        "supported" to supported,
        "detail" to if (supported) null else "Google Play services is unavailable or needs an update.",
        "mlKitModulesAvailable" to available,
        "modelsLoaded" to (objectDetector != null && sceneClassifier != null),
      )
    }

    AsyncFunction("installMlKitModules") {
      val client = ModuleInstall.getClient(context())
      val completed = CompletableFuture<Unit>()
      val listener = InstallStatusListener { update ->
        val progress = update.progressInfo
        sendEvent(
          "onMlKitInstallProgress",
          mapOf(
            "state" to when (update.installState) {
              ModuleInstallStatusUpdate.InstallState.STATE_PENDING -> "pending"
              ModuleInstallStatusUpdate.InstallState.STATE_DOWNLOADING -> "downloading"
              ModuleInstallStatusUpdate.InstallState.STATE_INSTALLING -> "installing"
              ModuleInstallStatusUpdate.InstallState.STATE_COMPLETED -> "completed"
              ModuleInstallStatusUpdate.InstallState.STATE_CANCELED -> "canceled"
              ModuleInstallStatusUpdate.InstallState.STATE_FAILED -> "failed"
              ModuleInstallStatusUpdate.InstallState.STATE_DOWNLOAD_PAUSED -> "paused"
              else -> "unknown"
            },
            "downloadedBytes" to progress?.bytesDownloaded,
            "totalBytes" to progress?.totalBytesToDownload,
          ),
        )
        when (update.installState) {
          ModuleInstallStatusUpdate.InstallState.STATE_COMPLETED -> completed.complete(Unit)
          ModuleInstallStatusUpdate.InstallState.STATE_CANCELED ->
            completed.completeExceptionally(IllegalStateException("ML Kit module installation was canceled."))
          ModuleInstallStatusUpdate.InstallState.STATE_FAILED ->
            completed.completeExceptionally(
              IllegalStateException("ML Kit module installation failed with code ${update.errorCode}.")
            )
        }
      }
      val request = ModuleInstallRequest.newBuilder()
        .addApi(textRecognizer())
        .addApi(imageLabeler())
        .setListener(listener)
        .build()
      try {
        val response = Tasks.await(client.installModules(request))
        if (response.areModulesAlreadyInstalled()) {
          completed.complete(Unit)
        }
        completed.get(10, TimeUnit.MINUTES)
      } finally {
        runCatching { Tasks.await(client.unregisterListener(listener)) }
      }
    }

    AsyncFunction("releaseMlKitModules") {
      val client = ModuleInstall.getClient(context())
      val apis = optionalApis()
      Tasks.await(client.releaseModules(*apis))
      closeMlKitClients()
    }

    AsyncFunction("loadModels") {
        detectorModelPath: String,
        sceneModelPath: String,
        sceneLabelsPath: String,
        sceneIndoorOutdoorPath: String,
      ->
      closeResources()
      val detectorFile = File(detectorModelPath.removePrefix("file://"))
      val sceneFile = File(sceneModelPath.removePrefix("file://"))
      val labelsFile = File(sceneLabelsPath.removePrefix("file://"))
      val indoorOutdoorFile = File(sceneIndoorOutdoorPath.removePrefix("file://"))
      require(detectorFile.isFile) { "Detector model is not available locally." }
      require(sceneFile.isFile) { "Scene model is not available locally." }
      require(labelsFile.isFile && indoorOutdoorFile.isFile) {
        "Scene classifier metadata is not available locally."
      }

      val loadedLabels = labelsFile.readLines().filter { it.isNotBlank() }.map(::normalizedSceneLabel)
      val loadedIndoorOutdoor = indoorOutdoorFile.readLines()
        .filter { it.isNotBlank() }
        .map { it.substringAfterLast(' ').toInt() }
      require(loadedLabels.size == 365 && loadedIndoorOutdoor.size == 365) {
        "Places365 metadata must contain exactly 365 categories."
      }

      val modelBuffer = FileInputStream(detectorFile).channel.use { channel ->
        channel.map(FileChannel.MapMode.READ_ONLY, 0, channel.size())
      }
      val baseOptions = BaseOptions.builder().setModelAssetBuffer(modelBuffer).build()
      val detectorOptions = ObjectDetector.ObjectDetectorOptions.builder()
        .setBaseOptions(baseOptions)
        .setRunningMode(RunningMode.IMAGE)
        .setMaxResults(25)
        .setScoreThreshold(0.2f)
        .build()
      val loadedDetector = ObjectDetector.createFromOptions(context(), detectorOptions)
      try {
        val loadedSceneClassifier = Interpreter(
          sceneFile,
          Interpreter.Options().setNumThreads(4),
        )
        loadedSceneClassifier.allocateTensors()
        objectDetector = loadedDetector
        detectorModelBuffer = modelBuffer
        sceneClassifier = loadedSceneClassifier
        sceneLabels = loadedLabels
        sceneIndoorOutdoor = loadedIndoorOutdoor
      } catch (error: Throwable) {
        loadedDetector.close()
        throw error
      }
    }

    AsyncFunction("analyze") { imageUri: String ->
      val detector = objectDetector ?: throw IllegalStateException("Detector model is not loaded.")
      check(mlKitModulesAvailable()) { "Required ML Kit modules are not installed." }
      val totalStarted = SystemClock.elapsedRealtimeNanos()

      val decodeStarted = SystemClock.elapsedRealtimeNanos()
      emitAnalysisProgress("decode", "starting")
      val bitmap = decodeBitmap(imageUri)
      val imageDecodeMs = elapsedMs(decodeStarted)
      emitAnalysisProgress("decode", "completed", imageDecodeMs)
      val imageWidth = bitmap.width
      val imageHeight = bitmap.height
      try {
        val inputImage = InputImage.fromBitmap(bitmap, 0)
        val componentErrors = mutableListOf<Map<String, Any?>>()

        val textResult = runMlKitStage(
          stage = "text_recognition",
          resetClient = { textRecognizer?.close(); textRecognizer = null },
        ) {
          val blocks = Tasks.await(textRecognizer().process(inputImage)).textBlocks
            .take(20)
            .mapNotNull { block ->
              val lines = block.lines.map { line -> line.text.trim() }.filter { it.isNotEmpty() }
              if (lines.isEmpty()) return@mapNotNull null
              val box = block.boundingBox
              mapOf(
                "text" to lines.joinToString(" "),
                "lines" to lines,
                "box" to box?.let {
                  mapOf(
                    "left" to it.left.toDouble(),
                    "top" to it.top.toDouble(),
                    "right" to it.right.toDouble(),
                    "bottom" to it.bottom.toDouble(),
                  )
                },
              )
            }
          @Suppress("UNCHECKED_CAST")
          TextEvidence(
            lines = blocks.flatMap { it["lines"] as List<String> }.take(30),
            blocks = blocks,
          )
        }
        textResult.error?.let(componentErrors::add)

        val labelResult = runMlKitStage(
          stage = "image_labeling",
          resetClient = { imageLabeler?.close(); imageLabeler = null },
        ) {
          Tasks.await(imageLabeler().process(inputImage))
            .sortedByDescending { it.confidence }
            .take(12)
            .map { label ->
              mapOf(
                "text" to label.text,
                "confidence" to label.confidence.toDouble(),
                "index" to label.index,
              )
            }
        }
        labelResult.error?.let(componentErrors::add)

        val detectionStarted = SystemClock.elapsedRealtimeNanos()
        emitAnalysisProgress("object_detection", "starting")
        val detectionResult = runCatching {
          val mpImage = BitmapImageBuilder(bitmap).build()
          try {
            detector.detect(mpImage).detections().mapNotNull { detection ->
              val category = detection.categories().maxByOrNull { it.score() }
                ?: return@mapNotNull null
              val box = detection.boundingBox()
              mapOf(
                "label" to category.categoryName(),
                "confidence" to category.score().toDouble(),
                "index" to category.index(),
                "box" to mapOf(
                  "left" to box.left.toDouble(),
                  "top" to box.top.toDouble(),
                  "right" to box.right.toDouble(),
                  "bottom" to box.bottom.toDouble(),
                ),
              )
            }
          } finally {
            mpImage.close()
          }
        }
        val objectDetectionMs = elapsedMs(detectionStarted)
        val detections = detectionResult.getOrElse {
          emitAnalysisProgress("object_detection", "failed", objectDetectionMs)
          val cause = rootCause(it)
          componentErrors.add(
            mapOf(
              "stage" to "object_detection",
              "message" to "MediaPipe object detection failed (${cause.javaClass.simpleName}).",
              "errorCode" to null,
            ),
          )
          emptyList()
        }
        if (detectionResult.isSuccess) {
          emitAnalysisProgress("object_detection", "completed", objectDetectionMs)
        }

        val sceneStarted = SystemClock.elapsedRealtimeNanos()
        emitAnalysisProgress("scene_classification", "starting")
        val sceneResult = runCatching { classifyScene(bitmap) }
        val sceneClassificationMs = elapsedMs(sceneStarted)
        val sceneEvidence = sceneResult.getOrElse {
          emitAnalysisProgress("scene_classification", "failed", sceneClassificationMs)
          val cause = rootCause(it)
          val detail = cause.message
            ?.replace(Regex("file://\\S+"), "[local file]")
            ?.take(240)
          componentErrors.add(
            mapOf(
              "stage" to "scene_classification",
              "message" to listOfNotNull(
                "Places365 scene classification failed (${cause.javaClass.simpleName}).",
                detail,
              ).joinToString(" "),
              "errorCode" to null,
            ),
          )
          mapOf(
            "scenes" to emptyList<Map<String, Any>>(),
            "indoorProbability" to 0.0,
            "outdoorProbability" to 0.0,
          )
        }
        if (sceneResult.isSuccess) {
          emitAnalysisProgress("scene_classification", "completed", sceneClassificationMs)
        }

        check(componentErrors.size < 4) {
          "Every Fast Vision component failed; see component-stage process logs."
        }

        mapOf(
          "imageWidth" to imageWidth,
          "imageHeight" to imageHeight,
          "labels" to (labelResult.value ?: emptyList<Map<String, Any>>()),
          "visibleText" to (textResult.value?.lines ?: emptyList<String>()),
          "textBlocks" to (textResult.value?.blocks ?: emptyList<Map<String, Any?>>()),
          "detections" to detections,
          "scenes" to sceneEvidence["scenes"],
          "indoorProbability" to sceneEvidence["indoorProbability"],
          "outdoorProbability" to sceneEvidence["outdoorProbability"],
          "componentErrors" to componentErrors,
          "timings" to mapOf(
            "imageDecodeMs" to imageDecodeMs,
            "textRecognitionMs" to textResult.elapsedMs,
            "imageLabelingMs" to labelResult.elapsedMs,
            "objectDetectionMs" to objectDetectionMs,
            "sceneClassificationMs" to sceneClassificationMs,
            "totalMs" to elapsedMs(totalStarted),
          ),
        )
      } finally {
        bitmap.recycle()
      }
    }

    AsyncFunction("unload") {
      closeResources()
    }

    OnDestroy {
      closeResources()
    }
  }
}
