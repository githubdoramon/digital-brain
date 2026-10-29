package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesCommandMatcherTest {
  @Test fun grammarRepairsOnlyKnownWordBoundaries() {
    assertNull(GlassesCommandMatcher.literal("hey brain cargate").command)
    val car = GlassesCommandMatcher.grammar("hey brain cargate")
    assertEquals(GlassesCommand.TOGGLE_CAR_GATE, car.command)
    assertEquals("boundary", car.stage)
    assertEquals(0.2, car.cost!!, 0.0001)
    assertEquals(GlassesCommand.OPEN_FRONT_GATE, GlassesCommandMatcher.match("hey brain open frontdoor").command)
    for (text in listOf("cargate frontgate", "cargauge", "not cargate", "cargate later", "openfrontdoor"))
      assertNull(text, GlassesCommandMatcher.match("hey brain $text").command)
  }
  @Test fun phoneticsAndTranspositionsDoNotChangeProtectedWords() {
    assertNull(GlassesCommandMatcher.grammar("hey brain take a foto").command)
    val photo = GlassesCommandMatcher.match("hey brain take a foto")
    assertEquals(GlassesCommand.TAKE_PICTURE, photo.command)
    assertEquals(0.8, photo.cost!!, 0.0001)
    assertEquals(GlassesCommand.TOGGLE_CAR_GATE, GlassesCommandMatcher.match("hey brain toggel car gate").command)
    for (text in listOf("car gauge", "open front goat", "open frond gate", "toggle card gate", "close front gate",
      "take a picture and open front gate", "toggel car gait", "open car gaits"))
      assertNull(text, GlassesCommandMatcher.match("hey brain $text").command)
    assertEquals("negated_or_not_immediate", GlassesCommandMatcher.match("hey brain don't open the door").reason)
    assertEquals("repeated_wake", GlassesCommandMatcher.match("hey brain take picture hey brain").reason)
  }
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
    for (text in listOf("toggle front door", "close front gate", "open frond gate", "toggle cat gate", "open front gaits"))
      assertNull(GlassesCommandMatcher.match("hey brain $text").command)
  }
  @Test fun doorAndCarAliasesUseCanonicalWordsWithProtectedTargets() {
    for (article in listOf("", "the ")) {
      val door = GlassesCommandMatcher.match("Hey Brain, open ${article}door.")
      assertEquals(GlassesCommandMatch.EXACT, door.match)
      assertEquals(GlassesCommand.OPEN_FRONT_GATE, door.command)
      val car = GlassesCommandMatcher.match("Hey Brain, open ${article}car gate.")
      assertEquals(GlassesCommandMatch.EXACT, car.match)
      assertEquals(GlassesCommand.TOGGLE_CAR_GATE, car.command)
    }
    for (text in listOf("open the floor", "open the gore", "open the card gate", "open the cat gate",
      "don't open the door", "do not open the car gate", "should I open the door",
      "open the car gate later", "open the door and take a picture", "open the door hey brain")) {
      assertNull(text, GlassesCommandMatcher.match("Hey Brain, $text").command)
    }
    assertNull(GlassesCommandMatcher.match("I said Hey Brain, open the car gate").command)
  }
  @Test fun missingOrEmbeddedWakeNeverExecutes() {
    for (text in listOf("open the front gate", "hello brain open front gate", "hey rain open front gate", "hey train open front gate", "hey bringer open front gate", "I said hey brain open front gate", "take a peek")) {
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
  @Test fun phoneticWakeAliasesRemainAnchoredAndKeepCommandsStrict() {
    for (wake in listOf("brane", "bring", "brian")) {
      val result = GlassesCommandMatcher.match("hey $wake open the front gate")
      assertEquals(GlassesCommand.OPEN_FRONT_GATE, result.command)
      assertEquals(GlassesWakeMatch.FUZZY, result.wakeMatch)
      assertNull(GlassesCommandMatcher.match("I said hey $wake open front gate").command)
      assertNull(GlassesCommandMatcher.match("hey $wake don't open front gate").command)
      assertNull(GlassesCommandMatcher.match("hey $wake").command)
    }
    assertEquals(GlassesWakeMatch.EXACT, GlassesCommandMatcher.match("hey brain take a picture").wakeMatch)
  }
  @Test fun shortFrontDoorAndBoundedPhoneticAliasSelectOnlyTheFrontGate() {
    for (phrase in listOf("front door", "from dore"))
      assertEquals(GlassesCommand.OPEN_FRONT_GATE, GlassesCommandMatcher.match("okay brane $phrase").command)
    assertNull(GlassesCommandMatcher.match("hey brain open from gate").command)
    assertNull(GlassesCommandMatcher.match("hey brain from car").command)
  }
  @Test fun punctuationAndInputBounds() {
    assertEquals(GlassesCommand.TAKE_PICTURE, GlassesCommandMatcher.match("HEY, BRAIN! Take a photo.").command)
    assertNull(GlassesCommandMatcher.match("hey brain " + "a".repeat(513)).command)
    assertNull(GlassesCommandMatcher.match("").command)
  }
  @Test fun versionFourRepairsAreBoundedAndAuditable() {
    for ((text, expected, repair) in listOf(
      Triple("take a pick", GlassesCommand.TAKE_PICTURE, "pick->pic"),
      Triple("cartgate", GlassesCommand.TOGGLE_CAR_GATE, "cartgate->car gate"),
      Triple("tugle cargate", GlassesCommand.TOGGLE_CAR_GATE, "tugle->toggle"),
      Triple("tuggle car gate", GlassesCommand.TOGGLE_CAR_GATE, "tuggle->toggle"),
      Triple("duggle cargate", GlassesCommand.TOGGLE_CAR_GATE, "duggle->toggle")
    )) {
      val decision = GlassesCommandMatcher.match("hey brain $text")
      assertEquals(text, expected, decision.command)
      assertTrue(text, repair in decision.repairs)
      if (text != "tuggle car gate") assertNull(text, GlassesCommandMatcher.previous("hey brain $text").command)
      assertTrue(decision.cost!! <= 1.0)
    }
    for (text in listOf("take a pick", "cartgate", "tugle cargate")) {
      for (prefix in listOf("hey brain don't ", "hey brain should I ", "I said hey brain ", "brain "))
        assertNull(GlassesCommandMatcher.match(prefix + text).command)
      assertNull(GlassesCommandMatcher.match("hey brain $text and take photo").command)
      assertNull(GlassesCommandMatcher.match("hey brain $text later").command)
    }
    for (text in listOf("cardgate", "catgate", "cartdoor", "open phone door", "tugle front gate",
      "tugle card gate", "take a pig", "take pick up", "tugle cartgate", "open cartgate frontdoor"))
      assertNull(text, GlassesCommandMatcher.match("hey brain $text").command)
    assertNull(GlassesCommandMatcher.match("Brain open front door").command)
  }

}
