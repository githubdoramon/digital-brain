package expo.modules.digitalbrainruntime

import android.content.Context
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.auth.api.signin.GoogleSignInOptions
import com.google.android.gms.common.Scopes
import com.google.android.gms.common.api.Scope
import com.google.android.gms.tasks.Tasks
import java.util.concurrent.TimeUnit

/** Obtain a fresh ID token from the cached Google account without starting React Native. */
object RuntimeGoogleIdTokenProvider {
  private val speechCache = SpeechTokenCache()

  internal fun getSpeechIdToken(context: Context, webClientId: String): SpeechToken? {
    val account = GoogleSignIn.getLastSignedInAccount(context)
    val token = speechCache.get(account?.id, webClientId, account?.idToken, System.currentTimeMillis() / 1000) {
      getFreshIdToken(context, webClientId)
    }
    // Sign-out/account replacement while silent sign-in was pending cannot populate another request.
    if (GoogleSignIn.getLastSignedInAccount(context)?.id != account?.id) {
      speechCache.clear()
      return null
    }
    return token
  }

  internal fun rejectSpeechToken(token: String) { speechCache.reject(token) }

  fun getFreshIdToken(context: Context, webClientId: String): String? {
    if (GoogleSignIn.getLastSignedInAccount(context) == null) return null

    val account = Tasks.await(
      GoogleSignIn.getClient(context, options(webClientId)).silentSignIn(),
      25,
      TimeUnit.SECONDS,
    )
    return account.idToken?.takeIf(String::isNotBlank)
  }

  fun options(webClientId: String): GoogleSignInOptions =
    GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
      .requestScopes(Scope(Scopes.EMAIL), Scope(Scopes.PROFILE))
      .requestIdToken(webClientId)
      .requestServerAuthCode(webClientId, false)
      .build()
}
