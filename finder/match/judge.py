"""Stage-2 aesthetic judge: Claude vision compares listing photos + prose
against the query's aesthetic description and exemplar reference images, and
returns a structured verdict whose reason string goes into the Signal ping
and the dashboard's judgement trail.

Uses structured outputs (output_config.format) so the verdict is
guaranteed-valid JSON — no forced-tool plumbing needed.
"""

from __future__ import annotations

import io
import json
import logging
import pathlib

import anthropic

log = logging.getLogger(__name__)

_MAX_LISTING_IMAGES = 4
_MAX_REF_IMAGES = 4
# The API rejects images >5MB and downscales >1568px server-side anyway;
# resizing client-side keeps big phone-photo references under both limits.
_MAX_EDGE_PX = 1568

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "match": {"type": "boolean",
                  "description": "Does this listing fit the target aesthetic?"},
        "confidence": {"type": "number",
                       "description": "0-1, how confident you are in the verdict"},
        "reason": {"type": "string",
                   "description": "One short sentence, concrete visual evidence "
                                  "(e.g. 'low walnut frame, tapered legs, mustard cushions')"},
    },
    "required": ["match", "confidence", "reason"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You judge whether furniture listings match a target aesthetic. You are "
    "shown reference images of the target style (when available), then one "
    "listing's photos and text. Judge on visual style — form, materials, era, "
    "silhouette — not price or condition. Be strict: a plain or generic piece "
    "that merely shares a category with the references is not a match."
)


def _resize_jpeg(data: bytes) -> bytes:
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        if max(img.size) > _MAX_EDGE_PX:
            img.thumbnail((_MAX_EDGE_PX, _MAX_EDGE_PX))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception as e:
        log.warning("image resize failed (%s); sending original", e)
        return data


def _image_block(data: bytes) -> dict:
    import base64

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(_resize_jpeg(data)).decode(),
        },
    }


def load_reference_images(ref_dir: str | pathlib.Path,
                          limit: int = _MAX_REF_IMAGES) -> list[bytes]:
    ref_dir = pathlib.Path(ref_dir)
    if not ref_dir.is_dir():
        return []
    files = sorted(p for p in ref_dir.iterdir()
                   if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    return [p.read_bytes() for p in files[:limit]]


class Judge:
    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def judge(self, listing: dict, listing_images: list[bytes],
                    query_spec: dict, ref_images: list[bytes]) -> dict:
        """Returns {"match": bool, "confidence": float, "reason": str}."""
        description = (query_spec.get("aesthetic_description") or "").strip()

        content: list[dict] = [{
            "type": "text",
            "text": "Target aesthetic: "
                    + (description or "(no text description — go by the reference images)"),
        }]
        if ref_images:
            content.append({"type": "text",
                            "text": f"Reference images of the target style ({len(ref_images)}):"})
            content.extend(_image_block(d) for d in ref_images)
        content.append({"type": "text", "text": (
            "Now judge this listing:\n"
            f"Title: {listing.get('title', '')}\n"
            f"Price: {listing.get('price')}\n"
            f"Description: {(listing.get('description') or '')[:1500]}\n"
            f"Photos ({min(len(listing_images), _MAX_LISTING_IMAGES)}):"
        )})
        content.extend(_image_block(d) for d in listing_images[:_MAX_LISTING_IMAGES])

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=512,
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
        text = next(b.text for b in response.content if b.type == "text")
        verdict = json.loads(text)
        verdict["confidence"] = max(0.0, min(1.0, float(verdict["confidence"])))
        return verdict
