package expo.modules.digitalbrainstorage

import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

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

    AsyncFunction("renameDocument") { uri: String, name: String ->
      require(name.matches(Regex("[A-Za-z0-9 _().-]{1,140}"))) {
        "Recording names contain unsupported characters."
      }
      val document = DocumentFile.fromSingleUri(context(), Uri.parse(uri))
        ?: throw IllegalArgumentException("The recording is no longer available.")
      check(document.renameTo(name)) { "Android could not rename the recording." }
      mapOf("uri" to document.uri.toString(), "name" to name)
    }
  }
}
