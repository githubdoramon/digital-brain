package expo.modules.digitalbrainstorage

import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File
import java.io.FileInputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL

/**
 * SAF only grants a selected tree, not a path prefix.  DocumentFile is the
 * supported Android API for creating and returning child directories inside
 * that persisted tree; synthesising a nested content URI is not reliable.
 */
class DigitalBrainStorageModule : Module() {
  private fun context() = appContext.reactContext
    ?: throw IllegalStateException("Android application context is unavailable.")

  private fun directoryFor(baseUri: String): DocumentFile {
    val directory = DocumentFile.fromTreeUri(context(), Uri.parse(baseUri))
      ?: throw IllegalArgumentException("The selected Digital Brain folder is no longer available.")
    if (!directory.canWrite()) {
      throw IllegalStateException("Digital Brain can no longer write to the selected folder.")
    }
    return directory
  }

  private fun openSource(sourceUri: String): InputStream {
    val uri = Uri.parse(sourceUri)
    return when (uri.scheme) {
      "content" -> context().contentResolver.openInputStream(uri)
      "file" -> uri.path?.let { FileInputStream(File(it)) }
      else -> FileInputStream(File(sourceUri))
    } ?: throw IllegalArgumentException("The source file is no longer available.")
  }

  private fun sourceLength(sourceUri: String): Long {
    val uri = Uri.parse(sourceUri)
    return when (uri.scheme) {
      "content" -> context().contentResolver.openAssetFileDescriptor(uri, "r")?.use { it.length } ?: -1L
      "file" -> uri.path?.let { File(it).length() } ?: -1L
      else -> File(sourceUri).length()
    }
  }

  private fun skipFully(input: InputStream, offset: Long) {
    var remaining = offset
    val buffer = ByteArray(64 * 1024)
    while (remaining > 0) {
      val skipped = input.skip(remaining)
      if (skipped > 0) {
        remaining -= skipped
      } else {
        val read = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
        check(read > 0) { "The source file ended before the requested upload range." }
        remaining -= read.toLong()
      }
    }
  }

  private fun copyRange(input: InputStream, output: java.io.OutputStream, length: Long) {
    var remaining = length
    val buffer = ByteArray(64 * 1024)
    while (remaining > 0) {
      val read = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
      check(read > 0) { "The source file ended during the requested upload range." }
      output.write(buffer, 0, read)
      remaining -= read.toLong()
    }
  }

  private fun responseSnippet(connection: HttpURLConnection): String {
    val stream = if (connection.responseCode >= 400) connection.errorStream else connection.inputStream
      ?: return ""
    return stream.use { input ->
      val buffer = ByteArray(4096)
      val read = input.read(buffer)
      if (read > 0) String(buffer, 0, read, Charsets.UTF_8) else ""
    }
  }

  /**
   * Some document providers silently add an extension based on MIME type.
   * Reuse that provider-owned name on later writes instead of creating a new
   * "same" document every time a rolling log is mirrored.
   */
  private fun existingTarget(folder: DocumentFile, requestedName: String): DocumentFile? {
    return folder.findFile(requestedName)
      ?: folder.listFiles().firstOrNull { child ->
        val actualName = child.name ?: return@firstOrNull false
        actualName == requestedName ||
          actualName.startsWith("$requestedName.") ||
          actualName.startsWith("$requestedName (")
      }
  }

  private fun mergeDirectory(source: DocumentFile, target: DocumentFile) {
    source.listFiles().filter { it.isFile }.forEach { child ->
      val originalName = child.name ?: "legacy-file-${System.currentTimeMillis()}"
      val existing = target.findFile(originalName)
      if (existing != null && existing.length() > 0 && existing.length() == child.length()) {
        check(child.delete()) { "Android could not remove the migrated $originalName file." }
        return@forEach
      }
      val targetName = if (existing == null) {
        originalName
      } else {
        val dot = originalName.lastIndexOf('.')
        val stem = if (dot > 0) originalName.substring(0, dot) else originalName
        val extension = if (dot > 0) originalName.substring(dot) else ""
        "$stem-legacy-${System.currentTimeMillis()}$extension"
      }
      val destination = target.createFile(child.type ?: "application/octet-stream", targetName)
        ?: throw IllegalStateException("Android could not migrate $originalName.")
      try {
        val resolver = context().contentResolver
        val copiedBytes = resolver.openInputStream(child.uri).use { input ->
          checkNotNull(input) { "Android could not read $originalName." }
          resolver.openOutputStream(destination.uri, "wt").use { output ->
            checkNotNull(output) { "Android could not write $targetName." }
            input.copyTo(output).also { output.flush() }
          }
        }
        check(copiedBytes > 0 && destination.length() == copiedBytes) {
          "Android could not verify migrated file $targetName."
        }
        check(child.delete()) { "Android could not finish migrating $originalName." }
      } catch (error: Throwable) {
        destination.delete()
        throw error
      }
    }
    if (source.listFiles().isEmpty()) {
      check(source.delete()) { "Android could not remove the old storage folder." }
    }
  }

  override fun definition() = ModuleDefinition {
    Name("DigitalBrainStorage")

    AsyncFunction("ensureSubdirectory") { baseUri: String, name: String ->
      require(name.matches(Regex("[A-Za-z0-9 _-]{1,80}"))) {
        "Storage folder names must use letters, numbers, spaces, underscores, or hyphens."
      }
      val base = directoryFor(baseUri)
      val existing = base.findFile(name)
      val child = when {
        existing?.isDirectory == true -> existing
        existing != null -> throw IllegalStateException("$name exists but is not a folder.")
        else -> base.createDirectory(name)
      } ?: throw IllegalStateException("Could not create the $name folder.")
      mapOf("uri" to child.uri.toString(), "name" to name)
    }

    AsyncFunction("renameSubdirectoryIfExists") {
        baseUri: String,
        oldName: String,
        newName: String,
      ->
      val validName = Regex("[A-Za-z0-9 _-]{1,80}")
      require(oldName.matches(validName) && newName.matches(validName)) {
        "Storage folder names must use letters, numbers, spaces, underscores, or hyphens."
      }
      val base = directoryFor(baseUri)
      val existingNew = base.findFile(newName)
      if (existingNew?.isDirectory == true) {
        val legacy = base.findFile(oldName)
        if (legacy?.isDirectory == true) mergeDirectory(legacy, existingNew)
        return@AsyncFunction mapOf("renamed" to false, "uri" to existingNew.uri.toString())
      }
      val legacy = base.findFile(oldName)
      if (legacy?.isDirectory != true) {
        return@AsyncFunction mapOf("renamed" to false, "uri" to null)
      }
      check(legacy.renameTo(newName)) { "Android could not rename $oldName to $newName." }
      mapOf("renamed" to true, "uri" to legacy.uri.toString())
    }

    AsyncFunction("renameDocument") { uri: String, name: String ->
      require(name.matches(Regex("[A-Za-z0-9 _().-]{1,140}"))) {
        "Recording names contain unsupported characters."
      }
      val document = DocumentFile.fromSingleUri(context(), Uri.parse(uri))
        ?: throw IllegalArgumentException("The recording is no longer available.")
      check(document.renameTo(name)) { "Android could not rename the recording." }
      mapOf("uri" to document.uri.toString(), "name" to name)
    }

    AsyncFunction("getFileInfo") { uri: String ->
      val parsed = Uri.parse(uri)
      when (parsed.scheme) {
        "content" -> {
          val document = DocumentFile.fromSingleUri(context(), parsed)
            ?: return@AsyncFunction mapOf("exists" to false, "bytes" to 0L)
          mapOf("exists" to (document.exists() && document.isFile), "bytes" to document.length())
        }
        "file" -> {
          val file = parsed.path?.let(::File)
          mapOf("exists" to (file?.isFile == true), "bytes" to (file?.length() ?: 0L))
        }
        else -> {
          val file = File(uri)
          mapOf("exists" to file.isFile, "bytes" to file.length())
        }
      }
    }

    AsyncFunction("deleteFile") { uri: String ->
      val parsed = Uri.parse(uri)
      val deleted = when (parsed.scheme) {
        "content" -> DocumentFile.fromSingleUri(context(), parsed)?.delete() ?: false
        "file" -> parsed.path?.let(::File)?.delete() ?: false
        else -> File(uri).delete()
      }
      check(deleted) { "Android could not remove the retained media file." }
    }

    AsyncFunction("copyToSubdirectory") {
        baseUri: String,
        folder: String,
        sourceUri: String,
        name: String,
        mimeType: String,
        skipIfSameSize: Boolean,
      ->
      require(folder.matches(Regex("[A-Za-z0-9 _-]{1,80}"))) {
        "Storage folder names must use letters, numbers, spaces, underscores, or hyphens."
      }
      require(name.matches(Regex("[A-Za-z0-9 _().-]{1,160}"))) {
        "Storage file names contain unsupported characters."
      }

      val base = directoryFor(baseUri)
      val existingFolder = base.findFile(folder)
      val targetFolder = when {
        existingFolder?.isDirectory == true -> existingFolder
        existingFolder != null -> throw IllegalStateException("$folder exists but is not a folder.")
        else -> base.createDirectory(folder)
      } ?: throw IllegalStateException("Could not create the $folder folder.")
      check(targetFolder.canWrite()) { "Digital Brain cannot write to the $folder folder." }

      val expectedBytes = sourceLength(sourceUri)
      check(expectedBytes != 0L) { "Digital Brain cannot copy an empty source file." }
      var target = existingTarget(targetFolder, name)
      if (target != null && skipIfSameSize && expectedBytes > 0 && target.length() == expectedBytes) {
        return@AsyncFunction mapOf(
          "uri" to target.uri.toString(),
          "name" to name,
          "bytes" to expectedBytes,
          "reused" to true,
        )
      }
      if (target != null) {
        check(target.delete()) { "Android could not replace the existing $name file." }
      }

      target = targetFolder.createFile(mimeType, name)
        ?: throw IllegalStateException("Android could not create $name in $folder.")
      try {
        val resolver = context().contentResolver
        val copiedBytes = openSource(sourceUri).use { input ->
          resolver.openOutputStream(target.uri, "wt").use { output ->
            checkNotNull(output) { "Android could not open $name for writing." }
            input.copyTo(output).also { output.flush() }
          }
        }
        check(copiedBytes > 0) { "Android wrote an empty $name file." }
        check(expectedBytes <= 0 || copiedBytes == expectedBytes) {
          "Android copied only $copiedBytes of $expectedBytes bytes to $name."
        }
        val finalBytes = target.length()
        check(finalBytes == copiedBytes) {
          "Android could not verify $name after writing ($finalBytes/$copiedBytes bytes)."
        }
        mapOf(
          "uri" to target.uri.toString(),
          "name" to name,
          "bytes" to copiedBytes,
          "reused" to false,
        )
      } catch (error: Throwable) {
        target.delete()
        throw error
      }
    }

    AsyncFunction("uploadFileRange") {
        url: String,
        sourceUri: String,
        offset: Double,
        length: Double,
        headers: Map<String, String>,
      ->
      val byteOffset = offset.toLong()
      val byteLength = length.toLong()
      require(byteOffset >= 0 && byteLength > 0) { "Upload range must be positive." }
      val sourceBytes = sourceLength(sourceUri)
      require(sourceBytes < 0 || sourceBytes >= byteOffset + byteLength) {
        "Upload range exceeds the source file."
      }

      val connection = (URL(url).openConnection() as HttpURLConnection)
      try {
        connection.requestMethod = "PUT"
        connection.doOutput = true
        connection.connectTimeout = 30_000
        connection.readTimeout = 90_000
        connection.setFixedLengthStreamingMode(byteLength)
        headers.forEach { (name, value) -> connection.setRequestProperty(name, value) }
        connection.setRequestProperty("Content-Type", "application/octet-stream")
        openSource(sourceUri).use { input ->
          skipFully(input, byteOffset)
          connection.outputStream.use { output ->
            copyRange(input, output, byteLength)
            output.flush()
          }
        }
        mapOf("status" to connection.responseCode, "body" to responseSnippet(connection))
      } finally {
        connection.disconnect()
      }
    }

    AsyncFunction("listSubdirectory") { baseUri: String, folder: String ->
      val base = directoryFor(baseUri)
      val target = base.findFile(folder)
      if (target == null) return@AsyncFunction emptyList<Map<String, Any>>()
      check(target.isDirectory) { "$folder exists but is not a folder." }
      target.listFiles().filter { it.isFile && it.length() > 0 }.map { document ->
        mapOf(
          "uri" to document.uri.toString(),
          "name" to (document.name ?: "capture.bin"),
          "mimeType" to (document.type ?: "application/octet-stream"),
          "bytes" to document.length(),
        )
      }
    }
  }
}
