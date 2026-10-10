---
title: Choosing perception models for video evidence
sidebar_position: 6
---

# Choosing perception models for video evidence

Assessment: October 6, 2026. Target: **physical traffic cameras visible in footage**.
This is a source-based shortlist, not a benchmark on Waldo footage. No replacement
model has been downloaded, trained or selected for production in this cleanup.

## Recommendation

Keep SAM 3.1 in the comparison, but do not make it the only perception engine.
Benchmark a **hybrid detector and tracker pipeline** before committing to it:

1. A text/visual-prompt detector finds unfamiliar concepts without retraining.
2. A camera-specific detector scans densely once reviewed examples exist.
3. A segmenter/tracker expands candidates through adjacent frames when masks or
   temporal continuity are needed. Detection boxes alone can already supply a
   useful evidence card and source-video seek point.

My first challengers are **YOLOE-26** for prompted discovery and **RF-DETR Small
or Medium** for a trained camera detector, compared with Waldo's existing
**YOLO26** training path. Test both full-frame and overlapping-tile inference.
This recommendation is an engineering inference: smaller models may allow denser
sampling at a fixed budget, which could recover brief sightings a slower sparse
scan misses. It is not evidence that any challenger already beats SAM on cameras.

## The useful competitors

| Candidate | Role in Waldo | Why test it | Main qualification |
| --- | --- | --- | --- |
| SAM 3.1 | Prompted segmentation and video tracking baseline | Integrated language-guided masks and Object Multiplex video tracking | Its headline speedup targets many tracked objects on H100, not tiny-camera discovery or Mac performance. |
| YOLOE-26 | Fast text/example-guided discovery; optional masks | Current Ultralytics integration supports text and reference-image prompts, detection, segmentation and tracking workflows | Prompt recall, tiny-target accuracy and temporal identity need independent testing. Prompt-free mode is a fixed vocabulary, not arbitrary natural-language search. |
| RF-DETR Small/Medium | Fine-tuned physical-camera detector | Trainable transformer detector; segmentation variants also exist | A standard pretrained checkpoint is not a camera specialist. Needs independently reviewed positive and hard-negative examples. |
| Existing YOLO26 | Fine-tuned camera baseline | Lowest integration cost because Waldo already trains and serves it | Measure its recall at the same input resolution, tiling, data split and compute budget. Avoid comparing tuned YOLO to untuned competitors as if conditions were equal. |
| Grounding DINO + SAM 2.1 | Modular prompted detector plus masks/tracking | Established open implementation separates discovery from segmentation | Two model stages add latency and failure modes. Useful baseline even if SAM 3.1 ultimately wins. |
| DINO-X API | Optional hosted prompted discovery benchmark | Text, visual and customized prompts offer an alternative for long-tail objects | API examples are not downloadable model weights. Account access, data transfer, latency and cost need separate qualification. |
| DEIMv2 | Reserve trained-detector challenger | DINOv3-based variants and smaller alternatives warrant a second round if RF-DETR/YOLO plateau | Additional training/serving integration; review its current model/project terms before product use. |
| Rex-Omni | Reserve candidate verification/localization model | A 3B multimodal model reformulates localization as token prediction | Evaluate on difficult retrieved crops; do not assume it provides continuous video tracking or can replace an exhaustive scan. |

SAM 3.1's release reports roughly sevenfold acceleration at 128 objects on one
H100 versus the original SAM 3 release. Its video concept segmentation results
are mixed across datasets. That does not establish camera-specific superiority.
[Meta release](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md).

YOLOE supports text and visual examples; the current integration includes the
YOLOE-26 family. Text prompting downloads additional encoder assets, so package
those explicitly for offline installations. Visual prompts require mapping the
returned generic labels back to Waldo concepts.
[Original implementation](https://github.com/THU-MIG/yoloe),
[current integration](https://docs.ultralytics.com/models/yoloe).

RF-DETR provides detection and segmentation families. The repository distinguishes
Apache-designated models from Plus models with different terms; Small/Medium
provide a focused starting point without assuming all sizes have the same license.
Published COCO/RF100-VL results justify testing, not a claim about roadside cameras.
[RF-DETR repository and model table](https://github.com/roboflow/rf-detr).

Grounded SAM 2 demonstrates detector-plus-segmenter video workflows. DINO-X is a
distinct hosted option. DEIMv2 and Rex-Omni remain reserve candidates to keep the
first comparison affordable.
[Grounded SAM 2](https://github.com/IDEA-Research/Grounded-SAM-2),
[DINO-X API](https://github.com/IDEA-Research/DINO-X-API),
[DEIMv2](https://github.com/Intellindust-AI-Lab/DEIMv2),
[DEIMv2 terms](https://github.com/Intellindust-AI-Lab/DEIMv2/blob/main/LICENSE.md),
[Rex-Omni](https://github.com/IDEA-Research/Rex-Omni).

FastSAM/MobileSAM-style segmenters do not by themselves resolve the core discovery
question: which tiny object is a physical traffic camera? Keep the initial matrix
small instead of integrating every segmentation model. Likewise, geolocation is a
separate geometry/provenance problem; none of these models turns a mask into a
trustworthy latitude/longitude.

## Comparison protocol

Use a frozen corpus of whole drives from different sites/dates. Label every camera
sighting in evaluation footage independently of model output, including brief
appearances; annotate visibility intervals and useful pixel boxes. Include signs,
traffic lights, lights, sensors and empty mounting brackets as hard negatives.
Keep a discovery split for choosing prompts/thresholds and a held-out test split.
No neighboring frames, repeat passes of the same site or augmented descendants
may cross the train/test boundary. Video grouping in the cleanup is a first guard;
site/drive grouping still requires source metadata.

Run two clearly separate comparisons:

- **No training:** current Waldo backend, official SAM 3.1, YOLOE-26, and Grounding
  DINO + SAM 2.1, with a frozen prompt set and equal permitted examples.
- **Camera specialist:** RF-DETR and YOLO26 trained on the same human-reviewed
  data and evaluated on unseen sites. Compare each alone and with SAM propagation.

Record full-frame versus tiled operation and 1/4/native-FPS sampling as separate
conditions. Add the segmenter only after measuring detector recall. Report total
video decoding, tiling, inference, association and storage time, not model-forward
latency alone. Run cold and warm trials on the intended Apple Silicon and NVIDIA
hardware; neither CUDA numbers nor community MLX parity should be assumed.

Primary measures:

- Camera-event recall, stratified by target pixel size, visibility duration,
  daylight/night, blur, glare and occlusion; report sample counts and uncertainty.
- False positives per video-hour and reviewer time per confirmed physical camera.
- First/last visible-time error, missed intervals, track fragmentation and ID
  switches. A representative frame is not the full observation history.
- Time to first useful evidence, total wall time per footage-hour, peak memory,
  hardware/runtime versions and API cost where applicable.
- Mask quality only where masks improve inspection or downstream geometry.
  Keep spatial-position error separate and evaluate it only with ground truth.

Choose the lowest-cost configuration meeting the agreed held-out recall and review
burden, rather than the highest unrelated leaderboard score. If throughput wins
but brief-event recall falls, it fails Waldo's purpose. No numerical acceptance
claim should be invented before the baseline and corpus size are known.

## Integration boundary

Implement capabilities for prompted detection, trained detection, image masks,
video propagation and local track identity. A model need only provide the
capabilities it actually supports. Normalize geometry at the adapter boundary,
retain source timestamp provenance, and persist detector/checkpoint/config/run
identities with each observation. Keep tracking association distinct from a
verified physical asset identity. Return sampled coverage and failures alongside
results. Preserve the fallback backend until the challenger is qualified.

System 1 experiments have been removed from the active plan. Desktop/Tauri work
is on hold. Neither is a dependency of this comparison or the correctness cleanup.
