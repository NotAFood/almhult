#!/usr/bin/env python3
"""Download IKEA 3D models and convert to OBJ for use in SweetHome3D."""

import re
import sys
from pathlib import Path

import httpx
import trimesh


def extract_slug(url: str) -> str:
    match = re.search(r'/p/([^/]+)/', url)
    if not match:
        raise ValueError(f"Could not extract product slug from URL: {url}")
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


def glb_to_obj(glb_path: Path, product_dir: Path) -> None:
    """Convert GLB to OBJ + MTL + textures, all written into product_dir."""
    loaded = trimesh.load(str(glb_path), force="scene")
    if isinstance(loaded, trimesh.Scene):
        geometries = list(loaded.geometry.values())
        if not geometries:
            raise ValueError("Scene contains no geometry")
        mesh = trimesh.util.concatenate(geometries)
    else:
        mesh = loaded
    # GLB/GLTF units are meters; SweetHome3D reads OBJ as centimeters
    mesh.apply_scale(100)
    mesh.export(str(product_dir / "model.obj"))


def process(product_url: str, output_dir: Path) -> Path:
    slug = extract_slug(product_url)
    product_dir = output_dir / slug
    product_dir.mkdir(parents=True, exist_ok=True)
    print(f"  slug       : {slug}")

    response = httpx.get(product_url, follow_redirects=True)
    response.raise_for_status()

    glb_url = find_best_glb_url(response.text)
    print(f"  model url  : {glb_url}")

    glb_path = product_dir / "model.glb"

    print(f"  downloading...")
    download_glb(glb_url, glb_path)
    print(f"  converting to OBJ...")
    glb_to_obj(glb_path, product_dir)
    glb_path.unlink()

    for asset in sorted(product_dir.iterdir()):
        size_kb = asset.stat().st_size // 1024
        print(f"  saved       : {asset} ({size_kb} KB)")
    return product_dir


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="almhult",
        description="Download IKEA 3D models and convert to OBJ for SweetHome3D.",
    )
    parser.add_argument("urls", nargs="+", metavar="URL", help="IKEA product page URL(s)")
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=Path("models"),
        metavar="DIR",
        help="directory to save OBJ files (default: ./models)",
    )
    args = parser.parse_args()

    errors = []
    for url in args.urls:
        print(f"\n{url}")
        try:
            process(url, args.output_dir)
        except Exception as e:
            print(f"  error: {e}", file=sys.stderr)
            errors.append(url)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
