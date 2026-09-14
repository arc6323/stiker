from __future__ import annotations

import base64
import json
import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        self.workers = max(1, min(int(os.getenv("IMAGE_WORKERS", "4")), 6))
        if not self.api_key:
            raise AIServiceError("OPENAI_API_KEY is empty")

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def describe_person(self, photo_paths: Sequence[Path]) -> str:
        content = [{
            "type": "input_text",
            "text": (
                "Analyze these reference photos of one person. Write a compact stable identity brief in English "
                "for image generation: apparent age range, face shape, skin tone, hair, eyes, facial details and vibe. "
                "Focus on identity consistency and keep it under 120 words."
            ),
        }]
        for path in photo_paths[:3]:
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"})
        payload = {"model": self.vision_model, "input": [{"role": "user", "content": content}]}
        r = requests.post(
            f"{self.api_base}/responses",
            headers={**self.headers, "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        self._raise(r, "identity analysis")
        data = r.json()
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"].strip()
        parts: list[str] = []
        for item in data.get("output", []):
            for c in item.get("content", []):
                if isinstance(c.get("text"), str) and c["text"].strip():
                    parts.append(c["text"].strip())
        if not parts:
            raise AIServiceError("Identity analysis returned empty text")
        return "\n".join(parts)

    def generate_image(self, prompt: str, output_path: Path) -> Path:
        payload = {"model": self.image_model, "prompt": prompt, "size": "1024x1024"}
        r = requests.post(
            f"{self.api_base}/images/generations",
            headers={**self.headers, "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        self._raise(r, "image generation")
        data = r.json()
        image_b64 = next(
            (x.get("b64_json") for x in data.get("data", []) if isinstance(x, dict) and x.get("b64_json")),
            None,
        )
        if not image_b64:
            raise AIServiceError("Image API returned no b64_json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(image_b64))
        return output_path

    def _prompt(self, identity: str, style: str, style_desc: str, idea: StickerIdea) -> str:
        return (
            f"Create a high-quality Telegram static sticker. Character identity: {identity}. "
            f"Style: {style}. {style_desc}. Action: {idea.instruction}. "
            "Single subject, centered, expressive face, bold white outline, subtle shadow, plain light background, "
            "no text, no watermark, no frame. Keep the same identity across the set."
        )

    def generate_set(
        self,
        photo_paths: Sequence[Path],
        style: str,
        style_desc: str,
        ideas: Sequence[StickerIdea],
        output_dir: Path,
    ) -> tuple[str, list[Path]]:
        identity = self.describe_person(photo_paths)
        output_dir.mkdir(parents=True, exist_ok=True)
        indexed_ideas = list(enumerate(ideas, 1))
        results: dict[int, Path] = {}

        def one(index: int, idea: StickerIdea) -> tuple[int, Path]:
            path = output_dir / f"raw_{index:02d}_{idea.title}.png"
            return index, self.generate_image(self._prompt(identity, style, style_desc, idea), path)

        with ThreadPoolExecutor(max_workers=min(self.workers, len(indexed_ideas) or 1)) as pool:
            futures = [pool.submit(one, index, idea) for index, idea in indexed_ideas]
            for future in as_completed(futures):
                index, path = future.result()
                results[index] = path

        return identity, [results[index] for index, _ in indexed_ideas]

    @staticmethod
    def _raise(response: requests.Response, stage: str) -> None:
        if response.ok:
            return
        message = response.text
        try:
            message = json.dumps(response.json(), ensure_ascii=False)
        except Exception:
            pass
        raise AIServiceError(f"OpenAI {stage} failed: HTTP {response.status_code}: {message}")
