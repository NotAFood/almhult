#!/usr/bin/env python3
"""Download IKEA 3D models and convert to USD or OBJ."""

import re
import sys
import tempfile
from pathlib import Path

import httpx
import trimesh
from pxr import Sdf, Usd, UsdGeom, UsdShade, UsdUtils


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


def _bind_texture(stage, usd_mesh, material, material_index: int, texture_dir: Path) -> None:
    """Write out the mesh's base color texture (if any) and bind it as a UsdPreviewSurface."""
    image = getattr(material, "baseColorTexture", None)
    material_path = f"{usd_mesh.GetPath()}/material_{material_index}"
    usd_material = UsdShade.Material.Define(stage, material_path)

    pbr_shader = UsdShade.Shader.Define(stage, f"{material_path}/PBRShader")
    pbr_shader.CreateIdAttr("UsdPreviewSurface")
    pbr_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.5)
    pbr_shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    usd_material.CreateSurfaceOutput().ConnectToSource(pbr_shader.ConnectableAPI(), "surface")

    if image is not None:
        texture_dir.mkdir(exist_ok=True)
        texture_path = texture_dir / f"material_{material_index}.png"
        image.convert("RGB").save(texture_path)

        uv_reader = UsdShade.Shader.Define(stage, f"{material_path}/UVReader")
        uv_reader.CreateIdAttr("UsdPrimvarReader_float2")
        uv_reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
        uv_reader.CreateOutput("result", Sdf.ValueTypeNames.Float2)

        uv_texture = UsdShade.Shader.Define(stage, f"{material_path}/DiffuseTexture")
        uv_texture.CreateIdAttr("UsdUVTexture")
        uv_texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
            f"textures/{texture_path.name}"
        )
        uv_texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
            uv_reader.ConnectableAPI(), "result"
        )
        rgb_output = uv_texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        pbr_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            rgb_output
        )
    else:
        color = getattr(material, "main_color", None)
        if color is not None:
            rgb = tuple(float(c) / 255 for c in color[:3])
            pbr_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(rgb)

    usd_mesh.GetPrim().ApplyAPI(UsdShade.MaterialBindingAPI)
    UsdShade.MaterialBindingAPI(usd_mesh).Bind(usd_material)


def _build_usd_stage(glb_path: Path, usd_path: Path, texture_dir: Path) -> None:
    """Write a USD stage with materials/textures for the GLB's geometry to usd_path."""
    loaded = trimesh.load(str(glb_path), force="scene")
    geometries = (
        loaded.geometry if isinstance(loaded, trimesh.Scene) else {"mesh": loaded}
    )
    if not geometries:
        raise ValueError("Scene contains no geometry")

    stage = Usd.Stage.CreateNew(str(usd_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    root = UsdGeom.Xform.Define(stage, "/Model")
    stage.SetDefaultPrim(root.GetPrim())

    for index, mesh in enumerate(geometries.values()):
        usd_mesh = UsdGeom.Mesh.Define(stage, f"/Model/mesh_{index}")
        usd_mesh.CreatePointsAttr(mesh.vertices.tolist())
        usd_mesh.CreateFaceVertexCountsAttr([3] * len(mesh.faces))
        usd_mesh.CreateFaceVertexIndicesAttr(mesh.faces.flatten().tolist())
        usd_mesh.CreateNormalsAttr(mesh.vertex_normals.tolist())
        usd_mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)

        visual = mesh.visual
        uv = getattr(visual, "uv", None)
        if uv is not None:
            primvar = UsdGeom.PrimvarsAPI(usd_mesh).CreatePrimvar(
                "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex
            )
            primvar.Set([(u, v) for u, v in uv])

        material = getattr(visual, "material", None)
        if material is not None:
            _bind_texture(stage, usd_mesh, material, index, texture_dir)

    stage.GetRootLayer().Save()


def glb_to_usd(glb_path: Path, product_dir: Path) -> None:
    """Convert GLB to a loose USD stage + textures/ dir, written into product_dir."""
    _build_usd_stage(glb_path, product_dir / "model.usdc", product_dir / "textures")


def glb_to_usdz(glb_path: Path, product_dir: Path) -> None:
    """Convert GLB to a single self-contained .usdz package in product_dir."""
    with tempfile.TemporaryDirectory() as build_dir:
        build_dir = Path(build_dir)
        usd_path = build_dir / "model.usdc"
        _build_usd_stage(glb_path, usd_path, build_dir / "textures")

        usdz_path = product_dir / "model.usdz"
        if not UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(usd_path)), str(usdz_path)):
            raise RuntimeError("Failed to create USDZ package")


def process(product_url: str, output_dir: Path, fmt: str) -> Path:
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
    print(f"  converting to {fmt.upper()}...")
    if fmt == "usdz":
        glb_to_usdz(glb_path, product_dir)
    elif fmt == "usd":
        glb_to_usd(glb_path, product_dir)
    else:
        glb_to_obj(glb_path, product_dir)

    for asset in sorted(product_dir.iterdir()):
        size_kb = asset.stat().st_size // 1024
        print(f"  saved       : {asset} ({size_kb} KB)")
    return product_dir


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="almhult",
        description="Download IKEA 3D models and convert to USD or OBJ.",
    )
    parser.add_argument("urls", nargs="+", metavar="URL", help="IKEA product page URL(s)")
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=Path("models"),
        metavar="DIR",
        help="directory to save converted models (default: ./models)",
    )
    parser.add_argument(
        "-f", "--format",
        choices=("usdz", "usd", "obj"),
        default="usdz",
        help="output format (default: usdz; usd for a loose stage + textures/, obj for SweetHome3D)",
    )
    args = parser.parse_args()

    errors = []
    for url in args.urls:
        print(f"\n{url}")
        try:
            process(url, args.output_dir, args.format)
        except Exception as e:
            print(f"  error: {e}", file=sys.stderr)
            errors.append(url)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
