# almhult

Download 3D models from IKEA product pages and convert them to OBJ format for use in [SweetHome3D](https://www.sweethome3d.com/) and other 3D design tools.

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
| `-o`, `--output-dir DIR` | `./models` | Directory to save OBJ files |

**Examples:**

```sh
# Download a single product
uv run almhult https://www.ikea.com/us/en/p/skadis-pegboard-wood-10347171/

# Download multiple products
uv run almhult \
  https://www.ikea.com/us/en/p/skadis-pegboard-wood-10347171/ \
  https://www.ikea.com/us/en/p/kallax-shelf-unit-white-20275814/

# Save to a custom directory
uv run almhult -o ~/Desktop/ikea-models https://www.ikea.com/us/en/p/skadis-pegboard-wood-10347171/
```

Each product is saved into its own slug folder containing the full OBJ asset set:

```
models/
└── skadis-pegboard-wood-10347171/
    ├── model.obj
    └── model.mtl
```

## Importing into SweetHome3D

1. Open SweetHome3D
2. **Furniture → Import furniture...**
3. Select the `.obj` file
4. Set the real-world dimensions (check the IKEA product page for measurements)
5. Assign a category and name, then click **Finish**

**A note on scale and positioning:** Models are exported in centimeters to match SweetHome3D's coordinate system, but depending on the product the scale or elevation may still need manual adjustment after import — particularly for wall-mounted items (pegboards, shelves, cabinets) which may appear at floor level. Use your best judgement and cross-reference the IKEA product dimensions when placing models.

## A note on responsible use

This tool fetches product pages and 3D model assets from IKEA's public website. Please use it considerately:

- **Do not scrape at scale.** This is intended for downloading a handful of models for personal space-planning, not bulk or automated harvesting.
- **Do not redistribute the downloaded models.** IKEA's 3D assets are their property.
- **Respect robots.txt and rate limits.** Add delays between requests if downloading many products in sequence.

IKEA provides these models to help customers visualize products — use them for that purpose.
