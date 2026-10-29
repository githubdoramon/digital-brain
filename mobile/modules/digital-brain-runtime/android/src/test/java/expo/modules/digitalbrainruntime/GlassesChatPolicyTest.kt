package expo.modules.digitalbrainruntime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.Base64

class GlassesChatPolicyTest {
  @Test fun ordinaryAndAmbiguousSpeechUseChatOnlyWithAnAnchoredWake() {
    for (text in listOf("Hey Brain what is on my calendar?", "Okay Brian don't open the gate",
      "Hey Brain can you take a picture and open the door?", "Hey Brain slash new")) {
      assertTrue(text, GlassesChatPolicy.shouldChat(text, GlassesCommandMatcher.match(text)))
    }
    for (text in listOf("Hey Brain", "Hey Brain.", "Hey Brain! ...", "I said Hey Brain what is next?", "Hey train what is next?", "what is next?"))
      assertFalse(text, GlassesChatPolicy.shouldChat(text, GlassesCommandMatcher.match(text)))
    assertEquals("what is next?", GlassesChatPolicy.phrase("Hey Brain, what is next?"))
    assertEquals("remember café plans", GlassesChatPolicy.phrase("Héy Bráin, remember café plans"))
  }
  @Test fun matchedCommandsNeverFallThroughToChatEvenWhenExecutionWouldFail() {
    for (text in listOf("Hey Brain open front gate", "Hey Brain toggle car gate", "Hey Brain take picture"))
      assertFalse(text, GlassesChatPolicy.shouldChat(text, GlassesCommandMatcher.match(text)))
  }
  @Test fun longerConversationsAreIndependentOfTheCommandLengthLimit() {
    val text = "Hey Brain " + "tell me about tomorrow ".repeat(30)
    assertTrue(GlassesChatPolicy.shouldChat(text, GlassesCommandMatcher.match(text)))
    assertNull(GlassesChatPolicy.phrase("Hey Brain " + "a".repeat(8192)))
  }
  private fun audio(rate: Int = 24000, pcm: ByteArray = ByteArray(200)) = JSONObject()
    .put("encoding", "pcm16").put("sample_rate", rate)
    .put("data", Base64.getEncoder().encodeToString(pcm))
  @Test fun onlyBoundedMonoPcmIsPlayable() {
    assertEquals(24000, GlassesChatPolicy.audio(audio()).sampleRate)
    for (value in listOf(audio(8000), audio(pcm = ByteArray(0)), audio(pcm = ByteArray(3)),
      audio(pcm = ByteArray(24000 * 2 * 46)), audio().put("encoding", "mp3"), audio().put("data", "!bad!"))) {
      assertTrue(runCatching { GlassesChatPolicy.audio(value) }.isFailure)
    }
  }
}
