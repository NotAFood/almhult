# almhult

Download 3D models from IKEA product pages and convert them to a single self-contained [.usdz](https://openusd.org/) package (default), a loose USD stage, or OBJ — for sharing, USD-compatible DCC tools, [SweetHome3D](https://www.sweethome3d.com/), and other 3D design tools.

> **Provided as-is for personal use and legitimate purposes** — primarily planning living spaces with IKEA products. Please read the usage note below before use.

---

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)

## Installation

```sh
git clone https://github.com/NotAFood/almhult.git
cd almhult
uv sync
```

This installs the `almhult` command into the project's virtual environment.

## Usage

```sh
uv run almhult <ikea-url> [<ikea-url> ...]
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `-o`, `--output-dir DIR` | `./models` | Directory to save converted models |
| `-f`, `--format {usdz,usd,obj}` | `usdz` | Output format; `usd` for a loose stage + `textures/`, `obj` for SweetHome3D |
| `--floor` | off | For planner URLs, prefer the floor-centred model variant when IKEA provides one |

**Examples:**

```sh
# Download a single product as USDZ (default) — one file, ready to share
uv run almhult https://www.ikea.com/us/en/p/skadis-pegboard-wood-10347171/

# Download as a loose USD stage instead of a packaged .usdz
uv run almhult -f usd https://www.ikea.com/us/en/p/skadis-pegboard-wood-10347171/

# Download as OBJ for SweetHome3D
uv run almhult -f obj https://www.ikea.com/us/en/p/skadis-pegboard-wood-10347171/

# Download multiple products
uv run almhult \
  https://www.ikea.com/us/en/p/skadis-pegboard-wood-10347171/ \
  https://www.ikea.com/us/en/p/kallax-shelf-unit-white-20275814/

# Download a part from one of IKEA's storage planners (see "Planner URLs" below)
uv run almhult "https://www.ikea.com/addon-app/storageone/kallax/web/latest/us/en/?productId=70286645#/planner"

# Save to a custom directory
uv run almhult -o ~/Desktop/ikea-models https://www.ikea.com/us/en/p/skadis-pegboard-wood-10347171/
```

Each product is saved into its own slug folder:

```
models/
└── skadis-pegboard-wood-10347171/
    ├── model.glb    ← original IKEA source file
    └── model.usdz    ← self-contained USD package (default format)
```

With `-f usd`, `model.usdz` is replaced by a loose `model.usdc` + `textures/` directory. With `-f obj`, it's replaced by `model.obj`, `model.mtl`, and the texture PNGs alongside it.

## Planner URLs

IKEA's storage planners (`/addon-app/storageone/<range>/...`) are also accepted. With a `productId=` parameter — the 8-digit article number — you get that one part; without it you get every part in that planner's range that has a model. Quote the URL so your shell doesn't interpret the `?` and `#`.

```sh
# Every KALLAX planner part, floor-centred where available
uv run almhult --floor "https://www.ikea.com/addon-app/storageone/kallax/web/latest/us/en/#/planner"
```

Supported ranges: AURDAL, BESTÅ, BILLY, BOAXEL, BROR, EKET, ELVARLI, GULDDRAGARE, IVAR, JONAXEL, KALLAX, KNOXHULT, LÅDMAKARE, LÄSTARE, METOD, PAX, PLATSA, SMÅSTAD and TROFAST.

Things to know:

- Planner models are individual parts (a frame, a drawer insert, a door), not whole designs. Combination article numbers have no model of their own; almhult lists the parts they are made of so you can fetch those instead.
- Not every article in a planner has a model — small accessories often don't.
- Planner models are lower-poly than product-page models, and their origin varies: some sit on the floor, others are centred on their bounding box.
- When IKEA offers more than one model for a part, almhult takes the planner's own and prints the others as `alt models`. Pass `--floor` to take the floor-centred variant instead (origin at the centre of the base, usually a single merged mesh); parts without one fall back to the default and are listed.
- `model.glb` is saved with Draco compression removed, so it opens in tools without a Draco decoder.

## Importing USD

`.usdz` is a single-file zip package (geometry + materials + textures) — open it directly in `usdview`, Blender, Omniverse, iOS AR Quick Look, or any other USD-aware tool. The packaged stage is written in meters, Y-up, with materials bound via `UsdPreviewSurface`.

## Importing into SweetHome3D

SweetHome3D does not read USD, so use `-f obj`:

1. Open SweetHome3D
2. **Furniture → Import furniture...**
3. Select the `.obj` file
4. Set the real-world dimensions (check the IKEA product page for measurements)
5. Assign a category and name, then click **Finish**

**A note on scale and positioning:** OBJ models are exported in centimeters to match SweetHome3D's coordinate system, but depending on the product the scale or elevation may still need manual adjustment after import — particularly for wall-mounted items (pegboards, shelves, cabinets) which may appear at floor level. Use your best judgement and cross-reference the IKEA product dimensions when placing models.

## A note on responsible use

This tool fetches product pages and 3D model assets from IKEA's public website. Please use it considerately:

- **Do not scrape at scale.** This is intended for downloading a handful of models for personal space-planning, not bulk or automated harvesting.
- **Do not redistribute the downloaded models.** IKEA's 3D assets are their property.
- **Respect robots.txt and rate limits.** Add delays between requests if downloading many products in sequence.

IKEA provides these models to help customers visualize products — use them for that purpose.
