#!/usr/bin/env python3
"""Download IKEA 3D models and convert to OBJ for use in SweetHome3D."""

import re
import sys
from pathlib import Path

import httpx
import trimesh


def extract_product_id(url: str) -> str:
    match = re.search(r'/p/[^/]+-(\d+)/', url)
    if not match:
        raise ValueError(f"Could not extract product ID from URL: {url}")
    return match.group(1)


def find_best_glb_url(html: str) -> str:
    """Find the highest-quality non-draco GLB URL embedded in the page."""
    pattern = r'https://web-api\.ikea\.com/dimma/assets/[^\'"]+\.glb[^\'"]*'
    all_urls = re.findall(pattern, html)
    if not all_urls:
        raise ValueError("No GLB model URLs found on page")

    # Prefer rqp3/glb/ (non-draco, highest quality preset)
    for quality in ("rqp3/glb/", "rqp2/glb/", "rqp1/glb/", "glb/"):
        matches = [u for u in all_urls if quality in u]
        if matches:
            return matches[0]

    return all_urls[0]


def download_glb(url: str, dest: Path) -> None:
    with httpx.Client(follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        dest.write_bytes(response.content)


def glb_to_obj(glb_path: Path, obj_path: Path) -> None:
    loaded = trimesh.load(str(glb_path), force="scene")
    if isinstance(loaded, trimesh.Scene):
        geometries = list(loaded.geometry.values())
        if not geometries:
            raise ValueError("Scene contains no geometry")
        mesh = trimesh.util.concatenate(geometries)
    else:
        mesh = loaded
    mesh.export(str(obj_path))


def process(product_url: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    product_id = extract_product_id(product_url)
    print(f"  product id : {product_id}")

    response = httpx.get(product_url, follow_redirects=True)
    response.raise_for_status()

    glb_url = find_best_glb_url(response.text)
    print(f"  model url  : {glb_url}")

    glb_path = output_dir / f"{product_id}.glb"
    obj_path = output_dir / f"{product_id}.obj"

    print(f"  downloading...")
    download_glb(glb_url, glb_path)
    print(f"  converting to OBJ...")
    glb_to_obj(glb_path, obj_path)
    glb_path.unlink()  # remove intermediate GLB

    size_kb = obj_path.stat().st_size // 1024
    print(f"  saved       : {obj_path} ({size_kb} KB)")
    return obj_path


def main() -> None:
    urls = sys.argv[1:]
    if not urls:
        print("usage: python main.py <ikea-url> [<ikea-url> ...]")
        print("example: python main.py https://www.ikea.com/us/en/p/skadis-pegboard-wood-10347171/")
        sys.exit(1)

    output_dir = Path("models")
    errors = []
    for url in urls:
        print(f"\n{url}")
        try:
            process(url, output_dir)
        except Exception as e:
            print(f"  error: {e}", file=sys.stderr)
            errors.append(url)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
