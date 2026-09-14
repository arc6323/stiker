from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import requests


@dataclass(frozen=True)
class StickerIdea:
    emoji: str
    title: str
    instruction: str


class AIServiceError(RuntimeError):
    pass


class OpenAIImageService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
        self.image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1").strip()
        self.vision_model = os.getenv("OPENAI_VISION_MODEL", "gpt-5-mini").strip()
        self.timeout = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "240"))
        if not self.api_key:
            raise AIServiceError("OPENAI_API_KEY is empty")

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def describe_person(self, photo_paths: Sequence[Path]) -> str:
        content = [{"type": "input_text", "text": "Analyze these photos of one person and write a compact stable visual identity brief for image generation: apparent age range, face, skin tone, hair, eyes, facial hair and overall vibe. Keep under 120 words."}]
        for path in photo_paths[:3]:
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"})
        payload = {"model": self.vision_model, "input": [{"role": "user", "content": content}]}
        r = requests.post(f"{self.api_base}/responses", headers={**self.headers, "Content-Type": "application/json"}, json=payload, timeout=self.timeout)
        self._raise(r, "identity analysis")
        data = r.json()
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"].strip()
        parts = []
        for item in data.get("output", []):
            for c in item.get("content", []):
                if isinstance(c.get("text"), str):
                    parts.append(c["text"].strip())
        text = "\n".join(x for x in parts if x)
        if not text:
            raise AIServiceError("Identity analysis returned empty text")
        return text

    def generate_image(self, prompt: str, output_path: Path) -> Path:
        payload = {"model": self.image_model, "prompt": prompt, "size": "1024x1024"}
        r = requests.post(f"{self.api_base}/images/generations", headers={**self.headers, "Content-Type": "application/json"}, json=payload, timeout=self.timeout)
        self._raise(r, "image generation")
        data = r.json()
        image_b64 = next((x.get("b64_json") for x in data.get("data", []) if isinstance(x, dict) and x.get("b64_json")), None)
        if not image_b64:
            raise AIServiceError("Image API returned no b64_json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(image_b64))
        return output_path

    def generate_set(self, photo_paths: Sequence[Path], style: str, style_desc: str, ideas: Sequence[StickerIdea], output_dir: Path) -> tuple[str, list[Path]]:
        identity = self.describe_person(photo_paths)
        output_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for i, idea in enumerate(ideas, 1):
            prompt = (
                f"Create a high-quality Telegram static sticker. Character identity: {identity}. "
                f"Style: {style}. {style_desc}. Action: {idea.instruction}. "
                "Single subject, centered, expressive face, bold white outline, subtle shadow, plain light background, no text, no watermark, no frame. Keep the same identity across the set."
            )
            result.append(self.generate_image(prompt, output_dir / f"raw_{i:02d}_{idea.title}.png"))
        return identity, result

    @staticmethod
    def _raise(response: requests.Response, stage: str) -> None:
        if response.ok:
            return
        msg = response.text
        try:
            msg = json.dumps(response.json(), ensure_ascii=False)
        except Exception:
            pass
        raise AIServiceError(f"OpenAI {stage} failed: HTTP {response.status_code}: {msg}")
