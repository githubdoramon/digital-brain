# Active wake-word acoustic model

Model ID: `wake-word-personal-2026-10-15` (epoch 3).

`GlassesSherpaDetector` loads all five assets from the matching Android asset
folder. Its `model-manifest.json` records their SHA-256 hashes, upstream source,
checkpoint and training revision. The model and search settings are one unit:

| Setting | Value |
|---|---:|
| Sherpa Android dependency | 1.13.2 |
| Keyword score | 0.000001 |
| Keyword threshold | 0.35 |
| Maximum active paths | 16 |
| Trailing blanks | 1 |
| Sample rate / feature dimensions | 16000 / 80 |
| Dither / CPU threads | 0 / 1 |

Encoder front end, Zipformer encoder, predictor and joiner were fine-tuned using
the pinned upstream Icefall RNNT recipe. Encoder/joiner are int8 and decoder is
float. Tokenizer, keyword list and Hey Brain/Okay Brain result aliases are
unchanged. This remains first-stage wake detection; it does not authenticate the
speaker. The firmware VAD/volume gate, onset ring, cooldown, capture endpointing
and command validation retain their existing ownership.

## Desktop evidence

Both original and candidate models were replayed with Sherpa 1.13.2.

| Newer held recordings | Original app model | Personal model |
|---|---:|---:|
| Phrase-timed wakes passing all four context phases | 19/44 | 33/44 |
| Confuser files falsely accepted in any phase | 51/194 | 19/194 |
| Natural ambient events in 2.082 hours | 1 | 0 |

| Historical 58-file regression | Original app model | Personal model |
|---|---:|---:|
| Raw file-level wake detection | 21/25 | 23/25 |
| File-level wakes passing all four context phases | 20/25 | 24/25 |
| Confuser files falsely accepted in any phase | 3/24 | 0/24 |
| Natural ambient events in 2.306 hours | 0 | 0 |

Four context phases prepend 4.00/4.02/4.04/4.06 seconds of real partition-local
ambient audio before each short clip. A positive must pass all four; a negative
fails if any fires. Historical positives lack reviewed phrase timestamps, so
their file-level scores must not be merged with the newer phrase-timed recall.
The historical source hashes match the old benchmark; no exact full-PCM or
recorded-source PCM match was found with fitting/calibration recordings.

Both collections were viewed in earlier experiments and are regression
benchmarks, not fresh independent accuracy estimates. The model/settings were
frozen before replay and were not retuned afterward. Desktop replay does not
reproduce historical firmware VAD or live volume preferences. Physical JNI,
glasses audio, background/locked-phone behavior, cue interference and battery
validation remain pending. A rebuilt APK is not evidence of those behaviors.

Model-weight redistribution terms remain unverified, as in the previous V8
workflow; this package is for the existing personal-device workflow. Training
uses personal recordings and provided-transcript LibriSpeech data (CC BY 4.0).
No raw recordings or transcript proposals are bundled in the app.

## Rollback

Restore the previous `GlassesSherpaDetector.kt` and all five `wake-word-v8/`
assets together from the preceding Git state. Restore score 1 and threshold 0.25
with the original weights. Do not mix old search parameters with these fine-tuned
weights. The original tokenizer/keyword file hashes are identical across both
packages; the encoder, decoder and joiner hashes differ.

A native Android rebuild is required to change the bundled assets and constants.
An OTA JavaScript update cannot apply this model change.
