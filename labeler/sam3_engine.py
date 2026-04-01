from dataclasses import dataclass

import cv2
import numpy as np
import torch
from PIL import Image

from lib.config import settings

_engine = None


@dataclass
class SegmentationResult:
    frame_index: int
    masks: np.ndarray  # (N, H, W) bool
    boxes: np.ndarray  # (N, 4) float
    scores: np.ndarray  # (N,) float
    class_indices: np.ndarray | None = None  # (N,) int — per-mask class index


def _extract_masks_from_output(output, height: int, width: int, threshold: float) -> SegmentationResult:
    """Extract masks, boxes, scores from a SAM 3 frame output.

    Handles both Sam3VideoSegmentationOutput (obj_id_to_mask dict) and
    Sam3TrackerVideoSegmentationOutput (pred_masks tensor).
    """
    frame_idx = output.frame_idx

    # Tracker output: pred_masks (N, 1, H, W) tensor + object_score_logits
    if hasattr(output, "pred_masks"):
        pred_masks = output.pred_masks
        if pred_masks is None or (isinstance(pred_masks, torch.Tensor) and pred_masks.numel() == 0):
            return SegmentationResult(
                frame_index=frame_idx,
                masks=np.empty((0, height, width), dtype=bool),
                boxes=np.empty((0, 4), dtype=np.float32),
                scores=np.empty(0, dtype=np.float32),
            )

        masks_tensor = pred_masks.cpu().squeeze(1)  # (N, H, W)
        score_logits = getattr(output, "object_score_logits", None)
        if score_logits is not None:
            scores_all = torch.sigmoid(score_logits).cpu().numpy().flatten()
        else:
            scores_all = np.ones(masks_tensor.shape[0])

        masks_list = []
        scores_list = []
        for i in range(masks_tensor.shape[0]):
            score = float(scores_all[i]) if i < len(scores_all) else 1.0
            if score < threshold:
                continue
            mask = masks_tensor[i].numpy()
            if mask.min() < -0.5 or mask.max() > 1.5:
                mask = 1.0 / (1.0 + np.exp(-np.clip(mask, -50, 50)))
            mask = (mask > 0.5).astype(np.uint8)
            if mask.shape != (height, width):
                mask = cv2.resize(mask, (width, height),
                                  interpolation=cv2.INTER_NEAREST)
            mask = mask.astype(bool)
            if not mask.any():
                continue
            masks_list.append(mask)
            scores_list.append(score)

    # Detect-track output: obj_id_to_mask dict
    else:
        obj_id_to_mask = output.obj_id_to_mask
        obj_id_to_score = getattr(output, "obj_id_to_score", {})

        if not obj_id_to_mask:
            return SegmentationResult(
                frame_index=frame_idx,
                masks=np.empty((0, height, width), dtype=bool),
                boxes=np.empty((0, 4), dtype=np.float32),
                scores=np.empty(0, dtype=np.float32),
            )

        masks_list = []
        scores_list = []

        for obj_id, mask_tensor in obj_id_to_mask.items():
            score = obj_id_to_score.get(obj_id, 1.0)
            if isinstance(score, torch.Tensor):
                score = score.item()
            if score < threshold:
                continue

            mask = mask_tensor.cpu().float().numpy().squeeze()
            # Apply sigmoid if mask contains logits (values outside 0-1)
            if mask.min() < -0.5 or mask.max() > 1.5:
                mask = 1.0 / (1.0 + np.exp(-np.clip(mask, -50, 50)))
            # Threshold to binary
            mask = (mask > 0.5).astype(np.uint8)
            # Resize to original frame dimensions
            if mask.shape != (height, width):
                mask = cv2.resize(mask, (width, height),
                                  interpolation=cv2.INTER_NEAREST)
            mask = mask.astype(bool)
            if not mask.any():
                continue

            masks_list.append(mask)
            scores_list.append(score)

    if masks_list:
        masks_np = np.stack(masks_list)
        scores_np = np.array(scores_list, dtype=np.float32)
    else:
        masks_np = np.empty((0, height, width), dtype=bool)
        scores_np = np.empty(0, dtype=np.float32)

    boxes_np = np.zeros((masks_np.shape[0], 4), dtype=np.float32)
    for j, mask in enumerate(masks_np):
        if mask.any():
            ys, xs = np.where(mask)
            boxes_np[j] = [xs.min(), ys.min(), xs.max(), ys.max()]

    return SegmentationResult(
        frame_index=frame_idx, masks=masks_np, boxes=boxes_np, scores=scores_np,
    )


def _cleanup(device: str, *tensors):
    for t in tensors:
        del t
    if device == "cuda":
        torch.cuda.empty_cache()
    elif device == "mps":
        torch.mps.empty_cache()


class Sam3Engine:
    def __init__(self, model_id: str, device: str, dtype: str):
        from transformers import Sam3VideoModel, Sam3VideoProcessor

        torch_dtype = getattr(torch, dtype, torch.float32)

        self.processor = Sam3VideoProcessor.from_pretrained(model_id)
        self.model = Sam3VideoModel.from_pretrained(model_id, torch_dtype=torch_dtype)
        self.model.to(device)
        self.model.eval()
        self.device = device
        self.torch_dtype = torch_dtype
        self.model_id = model_id

        # Lazy-loaded tracker model for point prompts
        self._tracker_model = None
        self._tracker_processor = None

    def _get_tracker(self):
        if self._tracker_model is None:
            from transformers import Sam3TrackerVideoModel, Sam3TrackerVideoProcessor

            self._tracker_processor = Sam3TrackerVideoProcessor.from_pretrained(self.model_id)
            self._tracker_model = Sam3TrackerVideoModel.from_pretrained(
                self.model_id, torch_dtype=self.torch_dtype
            )
            self._tracker_model.to(self.device)
            self._tracker_model.eval()
        return self._tracker_model, self._tracker_processor

    def segment_frames(
        self,
        frames: list[Image.Image],
        text_prompt: str,
        threshold: float = 0.5,
    ) -> list[SegmentationResult]:
        """Segment frames using a text prompt (detect-and-track)."""
        from transformers.models.sam3_video.modeling_sam3_video import Sam3VideoInferenceSession

        if not frames:
            return []

        first = frames[0]
        processed = self.processor(images=frames, return_tensors="pt")
        pixel_values = processed["pixel_values"].to(self.device, dtype=self.torch_dtype)

        session = Sam3VideoInferenceSession(
            video=pixel_values,
            video_height=first.height,
            video_width=first.width,
            inference_device=self.device,
            video_storage_device=self.device,
            dtype=self.torch_dtype,
        )
        self.processor.add_text_prompt(session, text_prompt)

        results = []
        for frame_idx in range(len(frames)):
            output = self.model(inference_session=session, frame_idx=frame_idx)
            results.append(_extract_masks_from_output(output, first.height, first.width, threshold))

        _cleanup(self.device, session, pixel_values)
        return results

    def segment_frames_with_points(
        self,
        frames: list[Image.Image],
        prompt_frame_idx: int,
        points: list[list[float]],
        labels: list[int],
        threshold: float = 0.5,
    ) -> list[SegmentationResult]:
        """Segment frames using point prompts on a reference frame (tracker model).

        Args:
            frames: All video frames as PIL images.
            prompt_frame_idx: Index of the frame where points are placed.
            points: List of [x, y] coordinates in pixel space.
            labels: List of 1 (positive) or 0 (negative) per point.
            threshold: Confidence threshold for keeping masks.
        """
        from transformers.models.sam3_tracker_video.modeling_sam3_tracker_video import (
            Sam3TrackerVideoInferenceSession,
        )

        if not frames:
            return []

        tracker_model, tracker_processor = self._get_tracker()
        first = frames[0]

        processed = tracker_processor(images=frames, return_tensors="pt")
        pixel_values = processed["pixel_values"].to(self.device, dtype=self.torch_dtype)

        session = Sam3TrackerVideoInferenceSession(
            video=pixel_values,
            video_height=first.height,
            video_width=first.width,
            inference_device=self.device,
            video_storage_device=self.device,
            dtype=self.torch_dtype,
        )

        # Add point prompts on the reference frame
        # Format: input_points is [[[x, y], ...]] per object, input_labels is [[[1], ...]] per object
        input_points = [[points]]  # one object, multiple points
        input_labels = [[labels]]  # corresponding labels
        tracker_processor.add_inputs_to_inference_session(
            session,
            frame_idx=prompt_frame_idx,
            obj_ids=[1],
            input_points=input_points,
            input_labels=input_labels,
            original_size=(first.height, first.width),
        )

        # Process the prompt frame first to get initial mask
        output = tracker_model(inference_session=session, frame_idx=prompt_frame_idx)

        # Propagate forward through all frames
        results = [None] * len(frames)
        results[prompt_frame_idx] = _extract_masks_from_output(
            output, first.height, first.width, threshold
        )

        for frame_idx in range(len(frames)):
            if frame_idx == prompt_frame_idx:
                continue
            output = tracker_model(inference_session=session, frame_idx=frame_idx)
            results[frame_idx] = _extract_masks_from_output(
                output, first.height, first.width, threshold
            )

        _cleanup(self.device, session, pixel_values)
        return results


def get_engine() -> Sam3Engine:
    global _engine
    if _engine is None:
        _engine = Sam3Engine(
            model_id=settings.sam3_model_id,
            device=settings.device,
            dtype=settings.dtype,
        )
    return _engine
