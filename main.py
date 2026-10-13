#!/usr/bin/env python3
"""Download IKEA 3D models and convert to USD or OBJ."""

import json
import re
import struct
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import DracoPy
import httpx
import numpy as np
import trimesh
from pxr import Sdf, Usd, UsdGeom, UsdShade, UsdUtils

STORAGEONE_PATTERN = re.compile(
    r'/addon-app/storageone/(?P<range>[a-z]+)/[^/]+/[^/]+/(?P<country>[a-z]{2})/(?P<language>[a-z]{2})/'
)
DEXF_ITEMS_URL = (
    "https://api.dexf.ikea.com/webplanner/v1/query/items/retailunit/{retail_unit}/locale/{locale}"
)
DRACO_EXTENSION = "KHR_draco_mesh_compression"
# IKEA's CDN rejects requests without a browser-like user agent
HEADERS = {"User-Agent": "Mozilla/5.0"}


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


def find_dexf_api_key(planner_url: str, planner_html: str, range_name: str) -> str:
    """Read the range's public DEXF API key out of the planner's own script bundle."""
    match = re.search(r'src="([^"]*/dexfApiKey-[^"]+\.js)"', planner_html)
    if not match:
        raise ValueError("Could not locate the planner's API key script")
    response = httpx.get(urljoin(planner_url, match.group(1)), headers=HEADERS, follow_redirects=True)
    response.raise_for_status()
    # The bundle lists production keys first, then the non-production set
    key = re.search(rf'\b{range_name}:`([0-9a-f-]{{36}})`', response.text)
    if not key:
        raise ValueError(f"No API key found for planner range '{range_name}'")
    return key.group(1)


def pick_asset(assets: list[dict], range_name: str, floor: bool) -> dict | None:
    """Pick one GLB asset: the floor-centred variant if asked for, else the planner's own."""
    models = [a for a in assets if a.get("fileTypeName") == "gltf-binary"]
    if not models:
        return None
    # Pivot "ccb" is centred in x/z with its base on the floor; "ccc" origins vary per model
    floor_centred = [a for a in models if a.get("pivot") == "ccb"] if floor else []
    own = [a for a in models if a.get("rangeFamily") == range_name]
    return (floor_centred or own or models)[0]


def storageone_slug(item: dict) -> str:
    content = item["content"]
    article = item["itemId"].split("-")[1]
    words = " ".join(str(content.get(k) or "") for k in ("name", "typeName", "validDesignText"))
    name = re.sub(r'[^a-z0-9]+', "-", words.lower()).strip("-")
    return f"{name}-{article}" if name else article


def find_storageone_models(planner_url: str, floor: bool) -> list[tuple[str, str]]:
    """Resolve a StorageOne planner URL to (slug, GLB URL) pairs.

    With a productId= parameter that is the one part; without, every part in the range.
    """
    parsed = urlparse(planner_url)
    location = STORAGEONE_PATTERN.search(parsed.path)
    if not location:
        raise ValueError(f"Not a StorageOne planner URL: {planner_url}")
    product_ids = parse_qs(parsed.query).get("productId")
    range_name = location["range"]

    page = httpx.get(planner_url, headers=HEADERS, follow_redirects=True)
    page.raise_for_status()
    api_key = find_dexf_api_key(str(page.url), page.text, range_name)

    if product_ids:
        item_filter = {"filter.itemId": f"ART-{product_ids[0]},SPR-{product_ids[0]}"}
    else:
        item_filter = {"filter.appId": f"storageone{range_name}"}
    response = httpx.get(
        DEXF_ITEMS_URL.format(
            retail_unit=location["country"].upper(),
            locale=f"{location['language']}-{location['country'].upper()}",
        ),
        params={**item_filter, "fields": "asset,child"},
        headers={**HEADERS, "dexf-api-key": api_key},
        timeout=120,
    )
    response.raise_for_status()
    items = [item for item in response.json()["data"] if item.get("content")]
    if not items:
        raise ValueError(f"Nothing found in the {range_name} planner for {planner_url}")

    picked = [
        (item, pick_asset(item["content"].get("asset") or [], range_name, floor))
        for item in items
    ]
    models = [(item, asset) for item, asset in picked if asset]
    if not models:
        children = [c["itemId"].split("-")[1] for c in items[0]["content"].get("child") or []]
        hint = f"; it is a combination of {', '.join(children)}" if children else ""
        subject = f"Product {product_ids[0]}" if product_ids else f"The {range_name} planner"
        raise ValueError(f"{subject} has no 3D model of its own{hint}")

    if product_ids:
        item, asset = models[0]
        alternatives = [a["url"] for a in item["content"]["asset"] if a["url"] != asset["url"]]
        if alternatives:
            print(f"  alt models : {', '.join(alternatives)}")
    else:
        print(f"  range      : {len(models)} of {len(items)} {range_name} articles have a model")
    if floor:
        fallbacks = [storageone_slug(item) for item, asset in models if asset.get("pivot") != "ccb"]
        if fallbacks:
            print(f"  no floor-centred model, using default for: {', '.join(fallbacks)}")

    return [(storageone_slug(item), asset["url"]) for item, asset in models]


def decompress_draco_glb(data: bytes) -> bytes:
    """Return an equivalent GLB with every Draco-compressed primitive expanded.

    IKEA's planner assets are mostly Draco-compressed, which trimesh cannot read.
    """
    json_length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + json_length])
    if DRACO_EXTENSION not in gltf.get("extensionsUsed", []):
        return data
    binary_start = 20 + json_length
    binary_length = struct.unpack_from("<I", data, binary_start)[0]
    binary = data[binary_start + 8:binary_start + 8 + binary_length]

    chunks = [binary + b"\0" * (-len(binary) % 4)]

    def add_accessor(array: np.ndarray, kind: str, component_type: int, target: int) -> int:
        raw = np.ascontiguousarray(array).tobytes()
        gltf["bufferViews"].append({
            "buffer": 0,
            "byteOffset": sum(len(c) for c in chunks),
            "byteLength": len(raw),
            "target": target,
        })
        chunks.append(raw + b"\0" * (-len(raw) % 4))
        accessor = {
            "bufferView": len(gltf["bufferViews"]) - 1,
            "componentType": component_type,
            "count": len(array),
            "type": kind,
        }
        if kind == "VEC3":
            accessor["min"], accessor["max"] = array.min(0).tolist(), array.max(0).tolist()
        gltf["accessors"].append(accessor)
        return len(gltf["accessors"]) - 1

    for mesh in gltf["meshes"]:
        for primitive in mesh["primitives"]:
            draco = primitive.get("extensions", {}).pop(DRACO_EXTENSION, None)
            if draco is None:
                continue
            view = gltf["bufferViews"][draco["bufferView"]]
            start = view.get("byteOffset", 0)
            decoded = DracoPy.decode(binary[start:start + view["byteLength"]])

            # 5126 = float32, 5125 = uint32; 34962 = vertex buffer, 34963 = index buffer
            points = np.asarray(decoded.points, dtype=np.float32).reshape(-1, 3)
            attributes = {"POSITION": add_accessor(points, "VEC3", 5126, 34962)}
            for semantic, values, kind, width in (
                ("NORMAL", decoded.normals, "VEC3", 3),
                ("TEXCOORD_0", decoded.tex_coord, "VEC2", 2),
            ):
                if values is not None and len(values):
                    array = np.asarray(values, dtype=np.float32).reshape(-1, width)
                    attributes[semantic] = add_accessor(array, kind, 5126, 34962)
            primitive["attributes"] = attributes
            faces = np.asarray(decoded.faces, dtype=np.uint32).reshape(-1)
            primitive["indices"] = add_accessor(faces, "SCALAR", 5125, 34963)
            if not primitive["extensions"]:
                del primitive["extensions"]

    for key in ("extensionsUsed", "extensionsRequired"):
        remaining = [e for e in gltf.get(key, []) if e != DRACO_EXTENSION]
        if remaining:
            gltf[key] = remaining
        else:
            gltf.pop(key, None)

    merged = b"".join(chunks)
    gltf["buffers"] = [{"byteLength": len(merged)}]
    payload = json.dumps(gltf, separators=(",", ":")).encode()
    payload += b" " * (-len(payload) % 4)
    return b"".join([
        struct.pack("<4sII", b"glTF", 2, 28 + len(payload) + len(merged)),
        struct.pack("<I4s", len(payload), b"JSON"),
        payload,
        struct.pack("<I4s", len(merged), b"BIN\0"),
        merged,
    ])


def download_glb(url: str, dest: Path) -> None:
    with httpx.Client(follow_redirects=True, headers=HEADERS) as client:
        response = client.get(url)
        response.raise_for_status()
        dest.write_bytes(decompress_draco_glb(response.content))


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


def find_models(product_url: str, floor: bool) -> list[tuple[str, str]]:
    """Resolve a product or planner URL to the (slug, GLB URL) pairs it refers to."""
    if STORAGEONE_PATTERN.search(product_url):
        return find_storageone_models(product_url, floor)
    slug = extract_slug(product_url)
    response = httpx.get(product_url, follow_redirects=True)
    response.raise_for_status()
    return [(slug, find_best_glb_url(response.text))]


def process(slug: str, glb_url: str, output_dir: Path, fmt: str) -> Path:
    product_dir = output_dir / slug
    product_dir.mkdir(parents=True, exist_ok=True)
    print(f"  slug       : {slug}")
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
    parser.add_argument(
        "urls", nargs="+", metavar="URL",
        help="IKEA product page URL(s), or planner URL(s): with a productId= parameter "
             "for one part, without for every part in that planner's range",
    )
    parser.add_argument(
        "--floor",
        action="store_true",
        help="for planner URLs, prefer the floor-centred model variant (origin at the "
             "centre of the base) when IKEA provides one",
    )
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
            models = find_models(url, args.floor)
        except Exception as e:
            print(f"  error: {e}", file=sys.stderr)
            errors.append(url)
            continue
        for slug, glb_url in models:
            try:
                process(slug, glb_url, args.output_dir, args.format)
            except Exception as e:
                print(f"  error: {slug}: {e}", file=sys.stderr)
                errors.append(slug)

    if errors:
        print(f"\n{len(errors)} failed: {', '.join(errors)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
