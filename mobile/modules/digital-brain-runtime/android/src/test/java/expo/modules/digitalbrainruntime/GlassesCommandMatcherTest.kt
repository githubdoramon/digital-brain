package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesCommandMatcherTest {
  @Test fun acceptedAliasesAndPoliteness() {
    for (wake in listOf("Hey Brain", "Okay Brain", "OK Brain")) {
      for (noun in listOf("gate", "door")) {
        assertEquals(GlassesCommand.OPEN_FRONT_GATE, GlassesCommandMatcher.match("$wake, please open the front $noun.").command)
        assertEquals(GlassesCommand.TOGGLE_CAR_GATE, GlassesCommandMatcher.match("$wake toggle the car $noun please").command)
      }
      for (noun in listOf("picture", "pic", "photo", "peek"))
        assertEquals(GlassesCommand.TAKE_PICTURE, GlassesCommandMatcher.match("$wake take a $noun").command)
    }
  }
  @Test fun transcriptionNoiseIsBoundedAndNeverChangesActionOrTarget() {
    assertEquals(GlassesCommandMatch.FUZZY, GlassesCommandMatcher.match("hey brain open front gote").match)
    assertEquals(GlassesCommandMatch.FUZZY, GlassesCommandMatcher.match("hey brain take a pictur").match)
    assertEquals(GlassesCommand.OPEN_FRONT_GATE, GlassesCommandMatcher.match("hey brain open the front gait").command)
    for (text in listOf("open car gate", "toggle front door", "close front gate", "open frond gate", "toggle cat gate", "open front gaits"))
      assertNull(GlassesCommandMatcher.match("hey brain $text").command)
  }
  @Test fun missingOrEmbeddedWakeNeverExecutes() {
    for (text in listOf("open the front gate", "hello brain open front gate", "hey rain open front gate", "I said hey brain open front gate", "hey brian take a picture", "take a peek")) {
      val result = GlassesCommandMatcher.match(text)
      assertFalse(result.wakeVerified); assertNull(result.command)
    }
  }
  @Test fun negationsQuestionsAndMultipleCommandsNeverExecute() {
    for (text in listOf("don't open front gate", "do not toggle car door", "should I open front gate", "take a picture and open front gate", "open front gate later", "don't take a picture", "don't take a peek", "take a peek and open front gate")) {
      val result = GlassesCommandMatcher.match("hey brain $text")
      assertTrue(result.wakeVerified); assertNull(result.command)
    }
  }
  @Test fun punctuationAndInputBounds() {
    assertEquals(GlassesCommand.TAKE_PICTURE, GlassesCommandMatcher.match("HEY, BRAIN! Take a photo.").command)
    assertNull(GlassesCommandMatcher.match("hey brain " + "a".repeat(513)).command)
    assertNull(GlassesCommandMatcher.match("").command)
  }
}
