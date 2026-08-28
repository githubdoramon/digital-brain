# Image understanding POC

This is an Android-first, app-only POC comparing three private on-device image-understanding pipelines against the same selected photo. It does not add backend APIs, Immich enrichment, event creation, or a photo-upload path.

The serialized coordinator owns three engines, but the test screen now exposes one agreed product-like pipeline:

1. **Fast Vision** — deterministic ML Kit OCR and broad image labeling, an EfficientDet-Lite0 INT8 object detector, and a Places365 ResNet18 scene classifier.
2. **Balanced VLM** — quantized LFM2.5-VL-450M inference with ExecuTorch/XNNPACK, supported by Fast Vision counts, detections, and OCR.
3. **LiteRT-LM** — generative Gemma 4 E2B multimodal inference using the strict GPU backend.

The screen runs Fast Vision first for detector/count/OCR evidence, unloads it, and then runs Balanced VLM against the same image. Balanced VLM describes what is happening; Fast Vision evidence is supporting context and exact OCR is appended to the final structured observation. No two native pipelines are loaded simultaneously. Standalone Fast Vision and LiteRT benchmark controls are intentionally hidden from this screen.

The earlier 4.40 GB ExecuTorch Gemma artifact was removed after physical-device testing produced repetitive invalid output. The ExecuTorch runtime has returned only for the much smaller LFM2.5-VL balanced lane. Historical runs remain readable, and model deletion is now exact: it deletes only the selected engine's pinned artifacts rather than clearing the shared resource-fetcher directory.

## Fast Vision runtime and models

The Android-native implementation is the local Expo module in `modules/fast-vision/`. It uses:

Runtime version `0.3.1` reads the Places365 model's real input/output tensor shapes and byte sizes instead of assuming a flat tensor layout. Unsupported shapes and datatypes are reported explicitly in redacted component diagnostics.

- Google Play services ML Kit Latin text recognition 19.0.1.
- Google Play services ML Kit image labeling 16.0.8 with 400+ broad labels.
- MediaPipe Tasks Vision 1.0.0.
- LiteRT 2.2.0 for the small scene classifier.
- Google's EfficientDet-Lite0 INT8 v1 detector, trained on COCO object classes including `person`.
- Places365 ResNet18 FP16, which produces 365 scene classes and aggregate indoor/outdoor evidence.

OCR and image-labeling modules are optional downloads managed by Google Play services. The app requests them explicitly, reports install progress when Google exposes it, and calls `releaseModules` on delete. Release is best-effort because Google Play services may retain a module shared with another app.

The detector and scene files are optional app-private downloads totaling 27,390,930 bytes. The downloader writes temporary files, validates fixed sizes and checksums, and atomically moves them into place. They are never bundled in the APK and are removed exactly by the delete action. The scene model is pinned to commit `a61b97b29accb8ea5e75cc8085db5557b2ebfcdd`; its 22,775,088-byte artifact has MD5 `5461f2f7903dc47ce1c05e0ae331b2e1`. The category and indoor/outdoor metadata are pinned to Places365 commit `8a953ed56438726dc98bdef3796d042e7f1f171e`.

Fast Vision runs on CPU/Google Play services. It records image-decode, OCR, image-labeling, object-detection, scene-classification, cold-load, and total inference latency. Raw output contains label confidence, OCR blocks and boxes, detections and boxes, scene probabilities, and native stage timings, but no image URI or EXIF.

Sources: [EfficientDet-Lite0 INT8 v1](https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/1/efficientdet_lite0.tflite), [Places365 ResNet18 LiteRT](https://huggingface.co/litert-community/Places365-ResNet18-LiteRT), and the [MIT Places365 repository](https://github.com/CSAILVision/places365). Runtime documentation: [ML Kit image labeling](https://developers.google.com/ml-kit/vision/image-labeling/android), [ML Kit text recognition](https://developers.google.com/ml-kit/vision/text-recognition/v2/android), and [MediaPipe object detection](https://ai.google.dev/edge/mediapipe/solutions/vision/object_detector/android). The scene model card declares MIT licensing; the Places365 repository requires attribution and points to its data terms. Review every applicable API, model, and dataset term before product distribution.

## Balanced VLM runtime and model

- `react-native-executorch@0.9.3` with `react-native-executorch-expo-resource-fetcher@0.9.1`.
- `LFM2_5_VL_450M_QUANTIZED`, using the runtime's pinned v0.9.0 ExecuTorch artifacts and XNNPACK 8da4w quantization.
- 653,653,487 downloaded bytes across the model, tokenizer, and tokenizer configuration, approximately 623.4 MiB.
- CPU/XNNPACK execution. Android GPU delegates are not used for this model.
- Android 13/API 33 or newer is required by this React Native ExecuTorch release; the app itself remains minSdk 28 and reports the engine as incompatible on older devices.

The model is an optional app-private download and is never bundled in the APK. The resource fetcher is initialized from `mobile/index.js` after background-task imports and before Expo Router so native resource state is available on every normal app launch. Unload releases the native module after each run; delete removes only this preset's three artifacts.

Model source: [LiquidAI LFM2.5-VL-450M](https://huggingface.co/LiquidAI/LFM2.5-VL-450M). The model card describes a 450M-parameter multimodal model with a SigLIP2 vision tower, multilingual support, image captioning, visual question answering, and object grounding. It also says the model is not intended for fine-grained OCR; Fast Vision therefore remains authoritative for recognized text. Review the model's [LFM Open License v1.0](https://www.liquid.ai/lfm-open-license-v1-0) and the runtime's package licenses before distribution.

## LiteRT-LM runtime and model

- `react-native-litert-lm@0.5.1` with `react-native-nitro-modules@0.36.5`.
- Native LiteRT-LM Android SDK 0.14.0.
- `litert-community/gemma-4-E2B-it-litert-lm`, approximately 2.59 GB.
- Main language-model backend: GPU.
- Vision-encoder backend: GPU.
- CPU fallback is disabled. Failed GPU initialization is reported rather than recorded as a misleading GPU benchmark.
- The benchmark context budget is 2,048 tokens with at most 768 output tokens, which covers the observed structured response while reducing KV-cache pressure.

The Gemma artifact already uses Google's mixed 2-bit, 4-bit, and 8-bit QAT scheme. It is an optional app-private download and is never bundled in the APK. The React Native package is a community bridge over Google's LiteRT-LM rather than an official Google React Native package.

Model source: [Gemma 4 E2B LiteRT-LM](https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm). Review the current [Gemma license](https://ai.google.dev/gemma/docs/gemma_4_license) and prohibited-use terms before distribution. The bridge is MIT licensed and LiteRT-LM is Apache-2.0.

## Shared schema and different production paths

All three engines produce `visual_observation.v2`:

- Fast Vision uses `fast_vision_pipeline.v4`. TypeScript deterministically fuses OCR layout, reliable object positions, indoor/outdoor probability, and conservative scene evidence into the schema. No generic image label becomes a claim by itself. OCR, labeling, detection, and scene classification share an explicit ARGB_8888 bitmap but are fault-isolated; a failed ML Kit component is retried once and recorded without discarding successful evidence from the other components.
- Balanced VLM uses `balanced_observation_prompt.v2`. It asks for a visual memory organized as scene, people, actions, important objects, setting, likely event, and uncertainty. There is no hard word limit. TypeScript tolerantly extracts these headings, preserves free-prose output as a fallback, appends exact Fast Vision OCR, and validates the fused result against the shared schema. Useful visible person descriptions and cautious apparent roles or relationships are retained; unsupported identities and facts must not be invented.
- LiteRT-LM uses `visual_observation_prompt.v4`, whose flat JSON contract explicitly requires string-only people details. Its parser performs a small, auditable set of syntax-only repairs: flattening the known `{label, details}` people shape, removing a premature root brace before a known top-level key, removing trailing commas, and appending missing JSON closers. It never changes semantic values or relaxes privacy/schema validation. Every applied repair is stored in `parseRepairs` and the process trace while the original raw output remains unchanged.

Directly visible evidence stays separate from interpretations and uncertainty. Detector counts and OCR remain machine evidence, while Balanced VLM supplies the semantic account of what appears to be happening.

For Fast Vision, detections at 50% confidence or higher form the lower count bound; person detections from 25% through 49% widen only the upper bound. Low-confidence non-person detections remain in raw evidence and are excluded from the structured observation. Broad labels are used only for a small allowlisted format/context hint and require corroboration. Small, occluded, cropped, or distant objects can still be missed.

LiteRT output remains invalid when syntax-only repair cannot produce the exact shared schema, when a semantic field is malformed, or when the privacy flag is not false.

## Privacy and lifecycle

- The picker passes a local URI only to the selected native engine. No API client, backend call, upload, or background queue receives the image.
- Run history never stores the photo URI, filename, EXIF, auth state, or account identifier.
- History contains runtime/model/backend/device measurements, raw model or detector output, parsed observation, parser-repair actions, redacted errors, and a bounded process trace. Exports currently use version 5.
- History is capped at 20 runs in AsyncStorage. Copy/export is user initiated.
- The coordinator serializes download, load, inference, unload, and deletion.
- Every engine releases native resources after every success and failure.
- LiteRT keeps its memory preflight on devices below 8 GB. On devices with at least 8 GB, this POC can override a conservative available-memory rejection because the 2.59 GB model is already an explicit user-selected benchmark; native load/OOM failures are still captured in the run record.

## Supported devices

- Android API 28+, React Native New Architecture, physical device recommended.
- Fast Vision requires a Google-certified Android device with current Google Play services for its optional ML Kit modules.
- EfficientDet-Lite0 and Places365 are CPU-backed and should run on a wider device range than Gemma.
- Balanced VLM requires Android 13/API 33 or newer and runs on CPU/XNNPACK. Use a physical ARM64 device; it remains independently runnable when Fast Vision evidence is unavailable, but the fused sequential result is the intended comparison.
- LiteRT GPU execution requires a compatible GPU and driver. This strict build fails rather than falling back to CPU.
- LiteRT-LM does not support x86_64 Android emulators. Use a physical ARM64 phone for comparisons.
- Keep at least 4 GB free for Gemma, partial downloads, and runtime headroom.
- Start Gemma testing on a modern phone with at least 8 GB RAM. Fast Vision should also be tested on lower-memory devices.
- iOS is not implemented for Fast Vision and is not validated for this POC.

## Setup

From `mobile/`:

```sh
npm install
GOOGLE_SERVICES_FILE=/absolute/path/to/google-services.json npx expo prebuild --clean --platform android
cd android
./gradlew :app:assembleDebug
```

Install the APK on a physical ARM64 Android device. Open **Settings → Image understanding POC**.

For a signed local preview APK, run `npm run eas:build:android:apk`. The wrapper reads `GOOGLE_SERVICES_FILE` from `.env`, this checkout, or the source checkout backing the Git worktree.

`react-native-litert-lm@0.5.1` declares API 26 while Nitro 0.36.x publishes its Prefab package for API 28. The checked-in `patch-package` patch raises the library declaration to API 28 and makes GPU selection strict. `npm install` reapplies both changes.

## Exact physical-device test protocol

1. Record device model, RAM, Android version, Google Play services version, free storage, battery, and thermal state. Reboot and leave the device idle for two minutes.
2. Install the updated APK as an upgrade so an existing Gemma download remains available.
3. Open **Image understanding POC**. Confirm the screen shows one full pipeline using Fast detector/OCR evidence and the Balanced `CPU / XNNPACK` visual-memory model.
4. Choose one non-sensitive image containing several objects, readable Latin-script text, and zero to three people.
5. Tap **Prepare pipeline** and confirm the combined Fast Vision and Balanced VLM artifacts become ready without bundling them in the APK.
6. Tap **Analyze selected image**. Verify Fast Vision finishes and unloads before Balanced VLM loads.
7. Confirm the final record uses `visual_observation.v2` and `balanced_observation_prompt.v2`.
8. Inspect the final scene, people, actions, important objects, setting, likely event, uncertainty, and separately appended OCR. Export diagnostics when the hidden Fast evidence trace or per-stage timings are needed.
9. Repeat twice with the identical image and unchanged thermal conditions. Compare variance rather than only the fastest run.
10. Repeat with a no-people image, an occluded/crowded image, a document or screenshot, and an outdoor scene.
11. Verify visible person descriptions are retained, interpretations are distinguishable from facts, and no unsupported identity or name is invented.
12. Background and foreground the app, then verify chat, location, dictation, and normal photo picking still work.
13. Delete Fast Vision and verify its detector and scene files are reclaimed; note that Play services module release is best-effort. Delete Balanced VLM and verify only its approximately 624 MiB artifacts are reclaimed. Delete LiteRT and verify its multi-GB artifact is reclaimed.
14. Inspect copied/exported JSON and confirm it contains no local URI, filename, EXIF, email, token, account data, or photo bytes.
15. With models cached, capture an Android network trace during both inference phases and confirm the selected image is never uploaded.

## Known limitations

- Fast Vision recognizes evidence but does not produce the same narrative reasoning as a multimodal LLM.
- EfficientDet-Lite0 can miss small, occluded, cropped, unusual, or densely packed objects. Its count range reflects confidence, not a calibrated guarantee that no additional objects exist.
- ML Kit image labels are broad and can be irrelevant. They are retained with confidence in raw evidence.
- Places365 uses a center crop and reports approximate scene similarity, not a proof of the exact venue or activity.
- Balanced VLM is intentionally a middle lane: richer than deterministic detectors but materially smaller than Gemma. Its CPU latency and output quality need physical-device measurement, and it is not a substitute for Fast Vision OCR or calibrated detection.
- The balanced tagged format reduces malformed structured output but does not guarantee that every generated line is useful. Privacy-sensitive fields are dropped rather than repaired semantically.
- Google Play services can fail an individual ML Kit operation after module installation. The pipeline retries OCR and labeling once with a fresh client, preserves detector or other successful evidence, and records the component and ML Kit error code in the process log and raw evidence.
- ML Kit optional-module size and current process memory are not exposed; the Fast Vision model-size metric covers its approximately 27.39 MB of app-private detector, scene model, and metadata only.
- GPU performance and stability are device/driver-specific. LiteRT intentionally prioritizes benchmark truth over automatic CPU compatibility.
- LiteRT-LM Android time-to-first-token telemetry may report zero; treat that value as unavailable until verified upstream.
- The LiteRT Android runtime does not honor the bridge's per-message output-token cap.
- Generated output can still violate the requested schema or make prohibited inferences. Validate every result before downstream use.
- Raw output may contain model-generated sensitive guesses. Export only when the selected image and output are safe to share.
