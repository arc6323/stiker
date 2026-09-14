from __future__ import annotations

from collections import deque
from pathlib import Path
from PIL import Image


def sanitize_pack_name(raw: str) -> str:
    out = []
    last_us = False
    for ch in raw.lower():
        if ch.isalnum():
            out.append(ch); last_us = False
        elif not last_us:
            out.append("_"); last_us = True
    name = "".join(out).strip("_") or "pack"
    if not name[0].isalpha():
        name = "p_" + name
    while "__" in name:
        name = name.replace("__", "_")
    return name[:48]


def remove_background_from_corners(image: Image.Image, tolerance: int = 24) -> Image.Image:
    image = image.convert("RGBA")
    w, h = image.size
    px = image.load()
    q = deque()
    seen = set()
    for x, y in [(0,0),(w-1,0),(0,h-1),(w-1,h-1)]:
        q.append((x, y, px[x, y][:3]))
    def close(a, b):
        return all(abs(i-j) <= tolerance for i, j in zip(a, b))
    while q:
        x, y, ref = q.popleft()
        if (x,y) in seen:
            continue
        seen.add((x,y))
        cur = px[x,y]
        if not close(cur[:3], ref):
            continue
        px[x,y] = (cur[0],cur[1],cur[2],0)
        for nx, ny in [(x-1,y),(x+1,y),(x,y-1),(x,y+1)]:
            if 0 <= nx < w and 0 <= ny < h and (nx,ny) not in seen:
                q.append((nx,ny,ref))
    return image


def prepare_static_sticker(source: Path, output: Path, max_side: int = 512, margin: int = 24) -> Path:
    img = remove_background_from_corners(Image.open(source).convert("RGBA"))
    inner = max_side - margin * 2
    img.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (max_side, max_side), (0,0,0,0))
    canvas.alpha_composite(img, ((max_side-img.width)//2, (max_side-img.height)//2))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, "PNG", optimize=True)
    return output
