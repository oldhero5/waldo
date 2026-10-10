"""Disk-backed history against the locked SAM3 session and tracker contracts."""

import gc
import pickle
import weakref
from types import MethodType

import pytest
import torch
from PIL import Image
from transformers import Sam3ImageProcessor, Sam3VideoConfig
from transformers.models.sam3_tracker_video.modeling_sam3_tracker_video import Sam3TrackerVideoModel
from transformers.models.sam3_video.modeling_sam3_video import Sam3VideoInferenceSession, Sam3VideoModel

from labeler.sam3_frame_storage import FrameOutputMap, PathBackedSam3Session, TensorFieldRecord


@pytest.fixture
def session(tmp_path):
    paths = []
    for ordinal in range(12):
        path = tmp_path / f"frame-{ordinal}.png"
        Image.new("RGB", (8, 6), (ordinal, 20, 40)).save(path)
        paths.append(path)
    return PathBackedSam3Session(
        paths,
        Sam3ImageProcessor(size={"height": 16, "width": 16}),
        working_dir=tmp_path / "history",
        inference_device="cpu",
        dtype=torch.float16,
    )


def test_promoted_record_keeps_nested_writes(tmp_path):
    noncond, cond = FrameOutputMap(tmp_path / "n"), FrameOutputMap(tmp_path / "c")
    noncond[7] = {}
    noncond[7]["pred_masks"] = torch.ones(1, 4, 4, dtype=torch.float16)
    record = noncond.pop(7)
    cond[7] = record
    record["pred_masks"] = torch.zeros(1, 4, 4, dtype=torch.float16)
    assert cond[7] is record
    assert torch.equal(cond[7]["pred_masks"], torch.zeros(1, 4, 4, dtype=torch.float16))
    assert 7 not in noncond
    del cond[7]
    assert torch.equal(record["pred_masks"], torch.zeros(1, 4, 4, dtype=torch.float16))


def test_record_mapping_overwrite_delete_and_metadata(tmp_path):
    metadata = {"reverse": False}
    record = TensorFieldRecord(tmp_path, {"pred_masks": torch.ones(2), "empty": None, "info": metadata})
    assert record["info"] is metadata
    assert record.get("empty", "default") is None
    assert record.get("missing", "default") == "default"
    assert list(record) == ["pred_masks", "empty", "info"]
    assert len(record) == 3
    record["pred_masks"] = torch.zeros(3, dtype=torch.bfloat16)
    assert torch.equal(dict(record.items())["pred_masks"], torch.zeros(3, dtype=torch.bfloat16))
    del record["pred_masks"]
    assert "pred_masks" not in record
    with pytest.raises(KeyError):
        record["pred_masks"]
    with pytest.raises(KeyError):
        del record["pred_masks"]
    assert not list(tmp_path.rglob("*.pt"))


def test_tensor_views_save_only_their_own_storage_and_reads_are_not_cached(tmp_path, monkeypatch):
    parent = torch.arange(1024 * 1024, dtype=torch.float32)
    view = parent[:16].reshape(1, 4, 4)
    record = TensorFieldRecord(tmp_path, {"pred_masks": view})
    assert sum(path.stat().st_size for path in tmp_path.rglob("*.pt")) < 8192
    original_load = torch.load
    loads = []

    def restricted_load(*args, **kwargs):
        loads.append(kwargs)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(torch, "load", restricted_load)
    first, second = record["pred_masks"], record["pred_masks"]
    assert torch.equal(first, torch.arange(16, dtype=torch.float32).reshape(1, 4, 4))
    assert first.dtype == view.dtype and first.device == view.device
    assert first.data_ptr() != second.data_ptr()
    assert loads == [{"map_location": "cpu", "weights_only": True}] * 2


def test_failed_write_preserves_previous_field_and_propagates(tmp_path, monkeypatch):
    record = TensorFieldRecord(tmp_path, {"pred_masks": torch.ones(2)})
    files_before = set(tmp_path.iterdir())

    def fail_save(value, path):
        path.write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(torch, "save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        record["pred_masks"] = torch.zeros(2)
    assert torch.equal(record["pred_masks"], torch.ones(2))
    assert set(tmp_path.iterdir()) == files_before
    next(iter(files_before)).write_bytes(b"corrupt")
    with pytest.raises(pickle.UnpicklingError):
        record["pred_masks"]


def test_path_session_reads_one_frame_with_full_count_and_closes_images(session, monkeypatch):
    assert isinstance(session, Sam3VideoInferenceSession)
    assert session.num_frames == 12
    assert (session.video_width, session.video_height) == (8, 6)
    assert session.processed_frames is None
    assert session.max_vision_features_cache_size == 1
    original_open = Image.open
    opened = []

    def capture_open(*args, **kwargs):
        image = original_open(*args, **kwargs)
        opened.append(image)
        return image

    monkeypatch.setattr(Image, "open", capture_open)
    actual = session.get_frame(7)
    with original_open(session.frame_paths[7]) as image:
        expected = session.processor(images=[image], return_tensors="pt")["pixel_values"][0].half()
    assert torch.equal(actual, expected)
    assert actual.shape == (3, 16, 16)
    assert len(opened) == 1 and opened[0].fp is None
    assert session.num_frames == 12 and session.processed_frames is None


def test_processor_failure_closes_frame_and_propagates(session, monkeypatch):
    image = Image.open(session.frame_paths[0])
    monkeypatch.setattr(Image, "open", lambda path: image)

    def fail_processor(**kwargs):
        raise OSError("processor failed")

    session.processor = fail_processor
    with pytest.raises(OSError, match="processor failed"):
        session.get_frame(0)
    assert image.fp is None


@pytest.mark.parametrize("device", ["cpu", "mps"])
def test_session_preserves_small_tensor_device_and_eager_values(session, device):
    if device == "mps" and not torch.backends.mps.is_available():
        pytest.skip("MPS unavailable")
    session = PathBackedSam3Session(
        session.frame_paths,
        session.processor,
        working_dir=session.working_dir / device,
        inference_device=device,
        dtype=torch.float16,
    )
    eager = Sam3VideoInferenceSession(inference_device=device, dtype=torch.float16)
    for target in (session, eager):
        idx = target.obj_id_to_idx(15)
        target.store_output(
            idx,
            2,
            output_value={
                "pred_masks": torch.arange(16, dtype=torch.float16).reshape(1, 4, 4).to(device),
                "object_pointer": torch.ones(1, 8).to(device),
                "object_score_logits": torch.tensor([[10.0]]).to(device),
                "high_res_masks": torch.ones(1, 1, 8, 8).to(device),
                "maskmem_features": None,
            },
        )
    actual = session.output_dict_per_obj[0]["cond_frame_outputs"][2]
    expected = eager.output_dict_per_obj[0]["cond_frame_outputs"][2]
    for key in ("pred_masks", "high_res_masks", "object_pointer", "object_score_logits"):
        assert actual[key].device == expected[key].device
        assert actual[key].dtype == expected[key].dtype
        assert torch.equal(actual[key].cpu(), expected[key].cpu())
        assert torch.equal(session.get_output(0, 2, key).cpu(), eager.get_output(0, 2, key).cpu())
    assert actual["maskmem_features"] is None
    assert session.get_output(0, 99, "pred_masks") is None


def test_remove_reindex_and_reset_do_not_reuse_history(session):
    first, survivor = session.obj_id_to_idx(10), session.obj_id_to_idx(20)
    session.store_output(first, 3, output_value={"pred_masks": torch.ones(16)})
    session.store_output(survivor, 3, output_value={"pred_masks": torch.full((16,), 20)})
    old_record = session.output_dict_per_obj[first]["cond_frame_outputs"][3]
    survivor_map = session.output_dict_per_obj[survivor]["cond_frame_outputs"]
    session.remove_object(10)
    assert session.obj_id_to_idx(20) == 0
    assert session.output_dict_per_obj[0]["cond_frame_outputs"] is survivor_map
    replacement = session.obj_id_to_idx(30)
    assert replacement == 1
    assert session.get_output(replacement, 3, "pred_masks") is None
    session.store_output(replacement, 3, output_value={"pred_masks": torch.full((16,), 30)})
    assert torch.equal(old_record["pred_masks"], torch.ones(16))
    assert torch.equal(session.get_output(0, 3, "pred_masks"), torch.full((16,), 20))
    session.remove_object(20)
    session.remove_object(30)
    assert not session.obj_ids
    assert session.num_frames == 12
    assert session.obj_id_to_idx(40) == 0
    assert session.get_output(0, 3, "pred_masks") is None
    session.store_output(0, 3, output_value={"pred_masks": torch.full((16,), 40)})
    assert torch.equal(old_record["pred_masks"], torch.ones(16))
    assert torch.equal(survivor_map[3]["pred_masks"], torch.full((16,), 20))


def test_many_dense_outputs_do_not_keep_input_or_loaded_tensors_alive(session):
    idx = session.obj_id_to_idx(1)
    refs = []
    for ordinal in range(96):
        mask = torch.ones(1, 64, 64)
        refs.append(weakref.ref(mask))
        session.store_output(idx, ordinal, output_value={"pred_masks": mask})
        loaded = session.get_output(idx, ordinal, "pred_masks")
        refs.append(weakref.ref(loaded))
        del loaded, mask
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert len(session.output_dict_per_obj[idx]["cond_frame_outputs"]) == 96
    assert len(list(session.working_dir.rglob("*.pt"))) == 96


def make_tracker(monkeypatch):
    """Real tracking transitions; only neural inference/encoding are injected."""
    tracker = Sam3TrackerVideoModel.__new__(Sam3TrackerVideoModel)
    torch.nn.Module.__init__(tracker)
    tracker.config = Sam3VideoConfig().tracker_config
    tracker.num_maskmem = tracker.config.num_maskmem
    tracker.eval()

    def infer(self, *, inference_session, obj_idx, frame_idx, **kwargs):
        assert kwargs["streaming"] is False
        assert inference_session.num_frames == 12
        if frame_idx:
            previous = inference_session.get_output(obj_idx, frame_idx - 1, "pred_masks", frame_idx == 1)
            mask = previous + 1
        else:
            mask = torch.ones(1, 1, 4, 4)
        return {
            "pred_masks": mask,
            "high_res_masks": mask.repeat_interleave(2, -1).repeat_interleave(2, -2),
            "object_pointer": torch.ones(1, 8),
            "object_score_logits": torch.tensor([[[10.0]]]),
        }

    def encode(self, *, current_vision_feats, pred_masks_high_res, **kwargs):
        count = pred_masks_high_res.shape[0]
        return torch.full((16, count, 4), 42, dtype=torch.bfloat16), torch.full((16, count, 4), 17.0)

    monkeypatch.setattr(tracker, "_run_single_frame_inference", MethodType(infer, tracker))
    monkeypatch.setattr(tracker, "_encode_new_memory", MethodType(encode, tracker))
    return tracker


def test_real_tracker_propagation_and_reconditioning_write_spilled_history(session, monkeypatch):
    idx = session.obj_id_to_idx(1)
    session.obj_id_to_prompt_id[1] = session.add_prompt("pipe")
    session.add_mask_inputs(idx, 0, torch.ones(1, 1, 8, 8))
    session.obj_with_new_inputs = [1]
    tracker = make_tracker(monkeypatch)
    for ordinal in range(3):
        session.cache.cache_vision_features(
            ordinal, {"vision_feats": [torch.ones(16, 1, 4)], "vision_pos_embeds": [torch.ones(16, 1, 4)]}
        )
        out = tracker(inference_session=session, frame_idx=ordinal, run_mem_encoder=True)
        assert torch.equal(out.pred_masks, torch.full((1, 1, 4, 4), ordinal + 1.0))
    noncond = session.output_dict_per_obj[idx]["non_cond_frame_outputs"]
    promoted = noncond[2]
    model = Sam3VideoModel.__new__(Sam3VideoModel)
    torch.nn.Module.__init__(model)
    model.tracker_model = tracker
    with torch.inference_mode():
        model._tracker_update_memories(session, 2, out.pred_masks.squeeze(1), {idx: torch.ones(1, 1, 4, 4)})
    cond = session.output_dict_per_obj[idx]["cond_frame_outputs"]
    assert cond[2] is promoted and 2 not in noncond
    assert torch.equal(cond[2]["maskmem_features"], torch.full((16, 1, 4), 42, dtype=torch.bfloat16))
    assert torch.equal(cond[2]["maskmem_pos_enc"], torch.full((16, 1, 4), 17.0))
    assert list(session.frames_tracked_per_obj[idx]) == [1, 2]


@pytest.mark.parametrize("device", ["cpu", "mps"])
def test_real_tracker_memory_selection_reads_spilled_fields(session, monkeypatch, device):
    if device == "mps" and not torch.backends.mps.is_available():
        pytest.skip("MPS unavailable")
    session = PathBackedSam3Session(
        session.frame_paths,
        session.processor,
        working_dir=session.working_dir / device,
        inference_device=device,
        dtype=torch.float16,
    )
    idx = session.obj_id_to_idx(1)
    tracker = make_tracker(monkeypatch)
    tracker.memory_temporal_positional_encoding = torch.nn.Parameter(
        torch.zeros(tracker.num_maskmem, 1, 1, 4).to(device)
    )
    for ordinal in range(6):
        session.store_output(
            idx,
            ordinal,
            output_value={
                "maskmem_features": torch.full((16, 1, 4), ordinal, dtype=torch.bfloat16),
                "maskmem_pos_enc": torch.full((16, 1, 4), ordinal + 10.0),
                "object_pointer": torch.full((1, 8), ordinal + 20.0),
            },
            is_conditioning_frame=ordinal == 0,
        )
    selected = tracker._gather_memory_frame_outputs(session, idx, 6)
    features, positions = tracker._build_memory_attention_inputs(selected, torch.device(device))
    assert [int(value.cpu()[0, 0, 0]) for value in features] == [0, 1, 2, 3, 4, 5]
    assert [int(value.cpu()[0, 0, 0]) for value in positions] == [10, 11, 12, 13, 14, 15]
    offsets, pointers, maximum = tracker._get_object_pointers(session, idx, 6, session.num_frames, torch.device(device))
    assert offsets == [6, 1, 2, 3, 4, 5]
    assert [int(value.cpu()[0, 0]) for value in pointers] == [20, 25, 24, 23, 22, 21]
    assert maximum == 12


@pytest.mark.parametrize("reset", ["reset_tracking_data", "reset_inference_session", "reset_state"])
def test_explicit_resets_preserve_detached_record_and_allocate_fresh_history(session, reset):
    idx = session.obj_id_to_idx(1)
    session.store_output(idx, 0, output_value={"pred_masks": torch.ones(16)})
    previous = session.output_dict_per_obj[idx]["cond_frame_outputs"][0]
    getattr(session, reset)()
    assert session.obj_id_to_idx(1) == 0
    session.store_output(0, 0, output_value={"pred_masks": torch.zeros(16)})
    assert torch.equal(previous["pred_masks"], torch.ones(16))
    assert torch.equal(session.get_output(0, 0, "pred_masks"), torch.zeros(16))
    assert session.num_frames == 12


def test_real_offline_hotstart_removal_keeps_surviving_history(session):
    model = Sam3VideoModel.__new__(Sam3VideoModel)
    torch.nn.Module.__init__(model)
    config = Sam3VideoConfig()
    for key in (
        "hotstart_delay",
        "hotstart_unmatch_thresh",
        "hotstart_dup_thresh",
        "init_trk_keep_alive",
        "max_trk_keep_alive",
        "min_trk_keep_alive",
        "decrease_trk_keep_alive_for_empty_masklets",
        "suppress_unmatched_only_within_hotstart",
    ):
        setattr(model, key, getattr(config, key))
    for obj_id in (1, 2):
        idx = session.obj_id_to_idx(obj_id)
        session.store_output(idx, 0, output_value={"pred_masks": torch.full((1, 4, 4), obj_id)})
        session.obj_first_frame_idx[obj_id] = 0
        session.trk_keep_alive[obj_id] = config.init_trk_keep_alive
    metadata = {
        key: getattr(session, key)
        for key in (
            "obj_first_frame_idx",
            "unmatched_frame_inds",
            "trk_keep_alive",
            "overlap_pair_to_frame_inds",
            "removed_obj_ids",
            "suppressed_obj_ids",
        )
    }
    removed = set()
    for ordinal in range(config.hotstart_unmatch_thresh):
        removed, _ = model._process_hotstart(session, ordinal, False, {0: [2]}, [], [], [1], metadata)
    assert removed == {1}
    model.run_tracker_update_execution_phase(
        session, 7, {}, {"new_det_out_inds": [], "new_det_obj_ids": [], "obj_ids_newly_removed": removed}
    )
    assert session.obj_ids == [2]
    assert torch.equal(session.get_output(0, 0, "pred_masks"), torch.full((1, 4, 4), 2))


def test_mps_reads_share_one_transfer_source_and_close_releases_it(session):
    if not torch.backends.mps.is_available():
        pytest.skip("MPS unavailable")
    session = PathBackedSam3Session(
        session.frame_paths,
        session.processor,
        working_dir=session.working_dir / "mps-lease",
        inference_device="mps",
        dtype=torch.float16,
    )
    refs = []
    for obj_id in (1, 2, 3):
        idx = session.obj_id_to_idx(obj_id)
        for ordinal in range(8):
            session.store_output(idx, ordinal, output_value={"pred_masks": torch.full((128,), obj_id + ordinal)})
            loaded = session.output_dict_per_obj[idx]["cond_frame_outputs"][ordinal]["pred_masks"]
            refs.append(weakref.ref(loaded))
            copied = loaded.to("mps", non_blocking=True)
            del loaded
            gc.collect()
            assert sum(ref() is not None for ref in refs) <= 1
            assert torch.equal(copied.cpu(), torch.full((128,), obj_id + ordinal))
    session.close()
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert torch.equal(copied.cpu(), torch.full((128,), 10))
    session.close()


def test_direct_mps_memory_slice_write_clones_and_offloads_synchronously(session):
    if not torch.backends.mps.is_available():
        pytest.skip("MPS unavailable")
    session = PathBackedSam3Session(
        session.frame_paths,
        session.processor,
        working_dir=session.working_dir / "mps-direct",
        inference_device="mps",
        dtype=torch.float16,
    )
    idx = session.obj_id_to_idx(1)
    session.store_output(idx, 0, output_value={"pred_masks": torch.ones(1, 4, 4)})
    record = session.output_dict_per_obj[idx]["cond_frame_outputs"][0]
    # The real memory encoder writes per-object views of a batched device tensor.
    parent = torch.full((16, 128, 4), 23, dtype=torch.bfloat16).to("mps")
    record["maskmem_features"] = parent[:, 37:38]
    record["maskmem_pos_enc"] = torch.full((16, 1, 4), 19, dtype=torch.float16).to("mps")
    assert torch.equal(record["maskmem_features"], torch.full((16, 1, 4), 23, dtype=torch.bfloat16))
    assert torch.equal(record["maskmem_pos_enc"], torch.full((16, 1, 4), 19, dtype=torch.float16))
    assert sum(path.stat().st_size for path in record.directory.glob("*.pt")) < 8192
    session.close()
