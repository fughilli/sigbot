"""Stage-1 aesthetic scoring: CLIP embedding similarity between listing photos
and the query's reference images / text description.

CPU inference, lazy model load (first score pays the model download into
cache/models/ — workspace-persistent). Callers run score() in a thread
(asyncio.to_thread); this module is synchronous.
"""

from __future__ import annotations

import io
import logging
import pathlib
import threading

log = logging.getLogger(__name__)

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
_MAX_LISTING_IMAGES = 4
# Blend: photos are the primary signal; the text description nudges.
_VISUAL_WEIGHT, _TEXT_WEIGHT = 0.85, 0.15

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class ClipScorer:
    def __init__(self, cache_dir: str | pathlib.Path = "cache/models"):
        self.cache_dir = str(cache_dir)
        self._lock = threading.Lock()
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        # (dir, fingerprint) -> reference embedding tensor
        self._ref_cache: dict[str, tuple[str, "object"]] = {}
        self._text_cache: dict[str, "object"] = {}

    # -- model -----------------------------------------------------------------

    def _ensure_model(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            import open_clip  # heavy import deferred until first use
            import torch

            torch.set_num_threads(max(1, (torch.get_num_threads() or 4) // 2))
            log.info("loading CLIP %s/%s (first run downloads weights)", MODEL_NAME, PRETRAINED)
            model, _, preprocess = open_clip.create_model_and_transforms(
                MODEL_NAME, pretrained=PRETRAINED, cache_dir=self.cache_dir,
            )
            model.eval()
            self._model = model
            self._preprocess = preprocess
            self._tokenizer = open_clip.get_tokenizer(MODEL_NAME)

    def _embed_images(self, images: list[bytes]):
        import torch
        from PIL import Image

        tensors = []
        for data in images:
            try:
                img = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception as e:
                log.warning("undecodable image skipped: %s", e)
                continue
            tensors.append(self._preprocess(img))
        if not tensors:
            return None
        with torch.no_grad():
            feats = self._model.encode_image(torch.stack(tensors))
        return feats / feats.norm(dim=-1, keepdim=True)

    def _embed_text(self, text: str):
        import torch

        if text not in self._text_cache:
            with torch.no_grad():
                feats = self._model.encode_text(self._tokenizer([text]))
            self._text_cache[text] = feats / feats.norm(dim=-1, keepdim=True)
        return self._text_cache[text]

    # -- references ---------------------------------------------------------------

    def _reference_embedding(self, ref_dir: pathlib.Path):
        """Mean-pooled, re-normalized embedding of the reference board; cached
        and invalidated when the directory's file set / mtimes change."""
        files = sorted(
            p for p in ref_dir.iterdir()
            if p.suffix.lower() in _IMAGE_EXTS
        ) if ref_dir.is_dir() else []
        if not files:
            return None, 0
        fingerprint = "|".join(f"{p.name}:{p.stat().st_mtime_ns}" for p in files)
        cached = self._ref_cache.get(str(ref_dir))
        if cached and cached[0] == fingerprint:
            return cached[1], len(files)
        feats = self._embed_images([p.read_bytes() for p in files])
        if feats is None:
            return None, 0
        board = feats.mean(dim=0, keepdim=True)
        board = board / board.norm(dim=-1, keepdim=True)
        self._ref_cache[str(ref_dir)] = (fingerprint, board)
        log.info("embedded %d reference images for %s", len(files), ref_dir)
        return board, len(files)

    # -- scoring ---------------------------------------------------------------

    def score(self, listing_images: list[bytes], query_spec: dict,
              ref_dir: str | pathlib.Path) -> dict:
        """Returns {"score", "visual", "text", "refs"} or {"skipped": reason}."""
        description = (query_spec.get("aesthetic_description") or "").strip()
        ref_dir = pathlib.Path(ref_dir)
        self._ensure_model()

        board, n_refs = self._reference_embedding(ref_dir)
        if board is None and not description:
            return {"skipped": "no reference images and no aesthetic description"}

        feats = self._embed_images(listing_images[:_MAX_LISTING_IMAGES])
        if feats is None:
            return {"skipped": "no decodable listing photos"}

        visual = float((feats @ board.T).max()) if board is not None else None
        text = None
        if description:
            text_emb = self._embed_text(description)
            text = float((feats @ text_emb.T).max())

        if visual is not None and text is not None:
            score = _VISUAL_WEIGHT * visual + _TEXT_WEIGHT * text
        else:
            score = visual if visual is not None else text
        return {
            "score": round(score, 4),
            "visual": round(visual, 4) if visual is not None else None,
            "text": round(text, 4) if text is not None else None,
            "refs": n_refs,
        }
