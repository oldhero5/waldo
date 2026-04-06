"""Score-first SAM3.1 detection — drop-in replacement for _detect_with_backbone.

Evaluates detection scores BEFORE running the mask decoder, then only generates
masks for queries that pass the score threshold. Saves ~25ms per call on M4 Max
(200 queries × mask decoder → ~5 queries × mask decoder).

Validated: zero accuracy impact across 200 frames (mean_iou identical).
"""

import logging

import mlx.core as mx
import numpy as np
from mlx_vlm.models.sam3.generate import DetectionResult, Sam3Predictor, nms
from mlx_vlm.models.sam3_1.generate import (
    _get_det_features,
    _resize_masks,
    _run_detr_encoder,
)

logger = logging.getLogger(__name__)


def detect_with_backbone_fast(
    predictor: Sam3Predictor,
    backbone_features: mx.array,
    prompts: list[str],
    image_size,
    threshold: float,
    encoder_cache: dict | None = None,
) -> DetectionResult:
    """Score-first detection on pre-computed backbone features.

    Same interface as mlx_vlm's _detect_with_backbone, but scores queries
    before running the mask decoder. Only generates masks for kept detections.

    Typical savings: ~25ms/call (mask decoder on ~5 vs 200 queries).
    """
    det = predictor.model.detector_model

    # FPN neck (~3ms)
    src, pos_flat, det_features, spatial = _get_det_features(
        predictor.model, backbone_features
    )
    H_f, W_f = spatial

    W, H = (
        image_size if isinstance(image_size, tuple)
        else (image_size[1], image_size[0])
    )

    all_boxes, all_masks, all_scores, all_labels = [], [], [], []

    for prompt in prompts:
        inputs_embeds, attention_mask = predictor._get_input_embeddings(prompt)

        # DETR encoder — use cache if available
        cached = encoder_cache.get(prompt) if encoder_cache is not None else None
        if cached is not None:
            encoded = cached["encoded"]
        else:
            encoded = _run_detr_encoder(
                predictor.model, src, pos_flat, inputs_embeds, attention_mask
            )
            if encoder_cache is not None:
                encoder_cache[prompt] = {"encoded": encoded}

        # DETR decoder
        hs, ref_boxes, presence_logits = det.detr_decoder(
            vision_features=encoded,
            inputs_embeds=inputs_embeds,
            vision_pos_encoding=pos_flat,
            text_mask=attention_mask,
            spatial_shape=(H_f, W_f),
        )

        # Box conversion in MLX
        pred_boxes_cxcywh = ref_boxes[-1]
        cx, cy, w, h = (
            pred_boxes_cxcywh[..., 0],
            pred_boxes_cxcywh[..., 1],
            pred_boxes_cxcywh[..., 2],
            pred_boxes_cxcywh[..., 3],
        )
        pred_boxes_xyxy = mx.stack(
            [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=-1
        )

        # Scoring in MLX
        all_logits = det.dot_product_scoring(hs, inputs_embeds, attention_mask)
        pred_logits = all_logits[-1].squeeze(-1)
        presence = presence_logits[-1]

        # --- SCORE FIRST: evaluate scores + boxes without mask decoder ---
        mx.eval(pred_logits, pred_boxes_xyxy, presence)

        scores_np = np.array(mx.sigmoid(pred_logits.squeeze()))
        if presence is not None:
            pres_np = np.array(mx.sigmoid(presence)).squeeze()
            scores_np = scores_np * pres_np
        keep = scores_np > threshold

        if not keep.any():
            continue

        # --- MASK ONLY KEPT: run mask decoder on kept queries only ---
        keep_indices = mx.array(np.where(keep)[0].astype(np.int32))
        last_hs_kept = hs[-1][:, keep_indices]
        seg_out = det.mask_decoder(
            last_hs_kept,
            list(det_features),
            encoder_hidden_states=encoded,
            prompt_features=inputs_embeds,
            prompt_mask=attention_mask,
        )
        mx.eval(seg_out)

        # Post-process kept detections
        pboxes = np.array(pred_boxes_xyxy)
        if pboxes.ndim == 3:
            pboxes = pboxes[0]
        boxes_np = pboxes[keep] * np.array([W, H, W, H])
        boxes_np = np.clip(boxes_np, 0, max(H, W))

        masks_np = np.array(seg_out["pred_masks"][0])
        masks_resized = _resize_masks(masks_np, (H, W))
        masks_binary = (masks_resized > 0).astype(np.uint8)

        result = DetectionResult(
            boxes=boxes_np,
            masks=masks_binary,
            scores=scores_np[keep],
        )
        if len(result.scores) > 0:
            result = nms(result)
            all_boxes.append(result.boxes)
            all_masks.append(result.masks)
            all_scores.append(result.scores)
            all_labels.extend([prompt] * len(result.scores))

    if not all_scores:
        return DetectionResult(
            boxes=np.zeros((0, 4)),
            masks=np.zeros((0, H, W), dtype=np.uint8),
            scores=np.zeros((0,)),
            labels=[],
        )

    return DetectionResult(
        boxes=np.concatenate(all_boxes),
        masks=np.concatenate(all_masks),
        scores=np.concatenate(all_scores),
        labels=all_labels,
    )
