#!/usr/bin/env python3
"""Build the reproducible SECOND COAT observer-overlay failure loop.

Only the two approved anchor renders in ``assets/final`` and the transition
material in ``assets/source`` are used. The anchor files are opened read-only
and are never modified. Pillow precomposes every opacity operation into RGB
frames before GIF encoding.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageSequence


# ---------------------------------------------------------------------------
# User-adjustable parameters
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
FINAL_ASSETS = ROOT / "assets" / "final"
SOURCE_ASSETS = ROOT / "assets" / "source"
CIVIC_PATH = FINAL_ASSETS / "SECOND_COAT_Civic_Render.png"
CLASSIFIED_PATH = FINAL_ASSETS / "SECOND_COAT_Classified_Render.png"
OUTPUT_PATH = FINAL_ASSETS / "SECOND_COAT_Overlay_Glitch.gif"
SUBMISSION_PATH = FINAL_ASSETS / "SECOND_COAT_Overlay_Glitch_Submission.gif"

RANDOM_SEED = 442085
STABLE_HOLD_MS = 5_000
TRANSITION_MS = 2_000
OPACITY_RANGE = (0.12, 0.85)
TRANSITION_INTENSITY = 0.78

MASTER_COLORS = 256
MASTER_DITHER = Image.Dither.FLOYDSTEINBERG
SUBMISSION_TARGET_BYTES = 5_500_000  # "approximately 5 MB"
SUBMISSION_SPECS = (
    # width, colors for anchors, colors for transition frames
    (960, 192, 80),
    (840, 176, 72),
    (720, 160, 64),
    (640, 144, 56),
)

EXCLUDED_NAME_PARTS = (
    "thumbnail",
    "thumb",
    "contact",
    "sheet",
    "SECOND_COAT_Overlay_Glitch",
    "preview",
    "qc",
)


@dataclass
class TransitionFrame:
    image: Image.Image
    duration_ms: int
    opacities: list[float]
    assets: list[str]
    direction: str
    progress: float
    exact_target: bool = False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_transition_assets() -> list[Path]:
    """Recursively find relevant PNGs while excluding anchors and outputs."""
    anchors = {CIVIC_PATH.resolve(), CLASSIFIED_PATH.resolve()}
    found: list[Path] = []
    for path in sorted(SOURCE_ASSETS.rglob("*.png")):
        if path.resolve() in anchors:
            continue
        lowered = path.name.lower()
        if any(part.lower() in lowered for part in EXCLUDED_NAME_PARTS):
            continue
        try:
            with Image.open(path) as image:
                width, height = image.size
                if width < 500 or height < 500:
                    continue
        except OSError:
            continue
        found.append(path)
    if not found:
        raise RuntimeError("No usable transition PNG files were found.")
    return found


def load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as source:
        return source.convert("RGB")


def cover_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize and center-crop an overlay to cover the canvas."""
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def irregular_durations(
    rng: random.Random,
    total_ms: int,
    count: int,
    *,
    final_ms: int | None = None,
) -> list[int]:
    """Return nonuniform 10 ms durations whose sum is exactly total_ms."""
    working_count = count - (1 if final_ms is not None else 0)
    remaining = total_ms - (final_ms or 0)
    durations = [40] * working_count
    # One perceptual "packet slip" is deliberately brief.
    flash_index = rng.randrange(working_count)
    durations[flash_index] = 30
    units = (remaining - sum(durations)) // 10
    while units:
        available = [
            i
            for i, value in enumerate(durations)
            if i != flash_index and value < 240
        ]
        if not available:
            raise RuntimeError("Duration constraints cannot satisfy requested total.")
        index = rng.choice(available)
        durations[index] += 10
        units -= 1
    rng.shuffle(durations)
    if len(set(durations)) < 4:
        raise RuntimeError("Transition timing variation is unexpectedly weak.")
    if final_ms is not None:
        durations.append(final_ms)
    assert sum(durations) == total_ms
    assert all(value % 10 == 0 for value in durations)
    return durations


def opacity_for_insert(rng: random.Random) -> float:
    """Weighted opacity: subliminal, readable, and occasional forceful inserts."""
    lower, upper = OPACITY_RANGE
    roll = rng.random()
    if roll < 0.24:
        value = rng.uniform(lower, 0.25)
    elif roll < 0.86:
        value = rng.uniform(0.26, 0.62)
    else:
        value = rng.uniform(0.63, upper)
    return round(value, 3)


def stepped_progress(index: int, count: int, rng: random.Random, reverse: bool) -> float:
    """Uneven progress with brief reversions, avoiding a generic crossfade."""
    nominal = (index + 1) / (count + 1)
    jitter = rng.uniform(-0.13, 0.10)
    if index in {2, 7}:
        jitter -= rng.uniform(0.10, 0.20)
    if reverse:
        # Withdrawal is reluctant early, then the civic layer reasserts sharply.
        nominal = nominal**1.22
    else:
        nominal = nominal**0.88
    return max(0.04, min(0.96, nominal + jitter))


def paste_anchor_fragments(
    canvas: Image.Image,
    source: Image.Image,
    target: Image.Image,
    progress: float,
    rng: random.Random,
    reverse: bool,
) -> None:
    """Replace irregular document bands without disturbing canvas geometry."""
    width, height = canvas.size
    target_fragments = 2 + round(5 * progress * TRANSITION_INTENSITY)
    for _ in range(target_fragments):
        horizontal = rng.random() < 0.76
        if horizontal:
            band_h = rng.randint(max(18, height // 90), max(32, height // 7))
            y = rng.randint(0, height - band_h)
            x0 = rng.randint(0, width // 5)
            x1 = rng.randint(max(x0 + width // 4, width // 2), width)
            box = (x0, y, x1, y + band_h)
            shift = rng.randint(-28, 28)
        else:
            band_w = rng.randint(max(14, width // 80), max(28, width // 8))
            x = rng.randint(0, width - band_w)
            y0 = rng.randint(0, height // 4)
            y1 = rng.randint(max(y0 + height // 5, height // 2), height)
            box = (x, y0, x + band_w, y1)
            shift = rng.randint(-20, 20)

        fragment = target.crop(box)
        destination = (box[0] + shift, box[1])
        alpha = int(255 * rng.uniform(max(0.32, progress * 0.62), min(1.0, 0.62 + progress * 0.38)))
        mask = Image.new("L", fragment.size, alpha)
        canvas.paste(fragment, destination, mask)

    # A few fragments of the outgoing state hang or briefly reappear.
    remnant_strength = (1.0 - progress) if not reverse else min(1.0, 1.18 - progress)
    remnant_count = rng.randint(1, 3)
    for _ in range(remnant_count):
        band_h = rng.randint(max(18, height // 110), max(28, height // 13))
        y = rng.randint(0, height - band_h)
        x0 = rng.randint(0, width // 3)
        x1 = rng.randint(max(x0 + width // 5, width // 2), width)
        fragment = source.crop((x0, y, x1, y + band_h))
        alpha = int(255 * rng.uniform(0.18, max(0.20, 0.66 * remnant_strength)))
        canvas.paste(fragment, (x0 + rng.randint(-35, 35), y), Image.new("L", fragment.size, alpha))


def paste_asset_insert(
    canvas: Image.Image,
    asset: Image.Image,
    opacity: float,
    rng: random.Random,
    progress: float,
) -> None:
    """Insert one supplied asset using a clipped overlay-failure topology."""
    width, height = canvas.size
    prepared = asset
    if rng.random() < 0.25:
        prepared = prepared.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    mask = Image.new("L", canvas.size, 0)
    draw = ImageDraw.Draw(mask)
    mode = rng.choices(("bands", "fragment", "tiles", "vein"), (42, 30, 18, 10), k=1)[0]

    if mode == "bands":
        for _ in range(rng.randint(1, 4)):
            band_h = rng.randint(max(18, height // 100), max(32, height // 8))
            y = rng.randint(0, height - band_h)
            inset = rng.randint(0, width // 5)
            draw.rectangle((inset, y, width - rng.randint(0, width // 5), y + band_h), fill=255)
    elif mode == "fragment":
        fragment_w = rng.randint(width // 4, int(width * 0.82))
        fragment_h = rng.randint(height // 8, int(height * 0.58))
        x = rng.randint(-width // 16, width - fragment_w + width // 16)
        y = rng.randint(0, height - fragment_h)
        draw.rectangle((x, y, x + fragment_w, y + fragment_h), fill=255)
    elif mode == "tiles":
        tile_h = rng.randint(max(18, height // 90), max(30, height // 18))
        for row in range(rng.randint(2, 6)):
            y = rng.randint(0, height - tile_h)
            x = rng.randint(0, width // 3)
            draw.rectangle((x, y, min(width, x + rng.randint(width // 6, width // 2)), y + tile_h), fill=255)
    else:
        # A sparse, displaced scan seam rather than a full-screen flash.
        x = rng.randint(width // 8, width * 7 // 8)
        seam_w = rng.randint(max(12, width // 100), max(22, width // 28))
        draw.rectangle((x, 0, min(width, x + seam_w), height), fill=255)

    # Strength grows loosely with displacement, but timing remains discontinuous.
    adjusted = max(OPACITY_RANGE[0], min(OPACITY_RANGE[1], opacity * (0.82 + progress * 0.30)))
    mask = mask.point(lambda value: round(value * adjusted))
    canvas.paste(prepared, (rng.randint(-12, 12), rng.randint(-10, 10)), mask)


def add_channel_slip(
    canvas: Image.Image,
    reference: Image.Image,
    rng: random.Random,
    strength: float,
) -> None:
    """Apply a narrow RGB registration error derived only from an anchor."""
    width, height = canvas.size
    band_h = rng.randint(max(12, height // 125), max(24, height // 36))
    y = rng.randint(0, height - band_h)
    band = reference.crop((0, y, width, y + band_h))
    red, green, blue = band.split()
    offset = max(2, round((5 + rng.randint(0, 15)) * strength))
    shifted_red = Image.new("L", band.size)
    shifted_red.paste(red, (offset, 0))
    shifted_blue = Image.new("L", band.size)
    shifted_blue.paste(blue, (-offset, 0))
    slipped = Image.merge("RGB", (shifted_red, green, shifted_blue))
    alpha = int(255 * rng.uniform(0.20, 0.50))
    canvas.paste(slipped, (rng.randint(-12, 12), y), Image.new("L", band.size, alpha))


def build_transition(
    source: Image.Image,
    target: Image.Image,
    assets: list[tuple[str, Image.Image]],
    rng: random.Random,
    direction: str,
    *,
    final_exact_target: bool,
) -> list[TransitionFrame]:
    frame_count = rng.randint(15, 17)
    durations = irregular_durations(
        rng,
        TRANSITION_MS,
        frame_count,
        final_ms=100 if final_exact_target else None,
    )
    glitch_count = frame_count - (1 if final_exact_target else 0)
    reverse = direction == "classified_to_civic"
    built: list[TransitionFrame] = []

    for index in range(glitch_count):
        progress = stepped_progress(index, glitch_count, rng, reverse)
        # Abrupt base-state swaps make this substitution failure, not a dissolve.
        threshold = 0.70 if not reverse else 0.61
        use_target_base = progress > threshold and rng.random() < (0.35 + progress * 0.55)
        canvas = (target if use_target_base else source).copy()
        paste_anchor_fragments(canvas, source, target, progress, rng, reverse)

        insert_count = rng.choices((1, 2, 3), (42, 42, 16), k=1)[0]
        chosen = rng.sample(assets, k=min(insert_count, len(assets)))
        opacities: list[float] = []
        names: list[str] = []
        for name, asset in chosen:
            opacity = opacity_for_insert(rng)
            if opacities and opacity in opacities:
                opacity = min(OPACITY_RANGE[1], opacity + 0.031)
            paste_asset_insert(canvas, asset, opacity, rng, progress)
            opacities.append(opacity)
            names.append(name)

        if rng.random() < 0.54 * TRANSITION_INTENSITY:
            add_channel_slip(canvas, target if use_target_base else source, rng, TRANSITION_INTENSITY)

        built.append(
            TransitionFrame(
                image=canvas,
                duration_ms=durations[index],
                opacities=opacities,
                assets=names,
                direction=direction,
                progress=round(progress, 3),
            )
        )

    if final_exact_target:
        built.append(
            TransitionFrame(
                image=target.copy(),
                duration_ms=durations[-1],
                opacities=[],
                assets=[],
                direction=direction,
                progress=1.0,
                exact_target=True,
            )
        )

    assert sum(frame.duration_ms for frame in built) == TRANSITION_MS
    return built


def quantize_frame(image: Image.Image, colors: int, dither: Image.Dither) -> Image.Image:
    return image.quantize(
        colors=colors,
        method=Image.Quantize.MEDIANCUT,
        dither=dither,
    )


def save_gif(
    path: Path,
    images: list[Image.Image],
    durations: list[int],
    *,
    colors: list[int] | int,
    dither: Image.Dither,
) -> None:
    if isinstance(colors, int):
        palette_sizes = [colors] * len(images)
    else:
        palette_sizes = colors
    encoded = [
        quantize_frame(image, palette_size, dither)
        for image, palette_size in zip(images, palette_sizes, strict=True)
    ]
    encoded[0].save(
        path,
        save_all=True,
        append_images=encoded[1:],
        duration=durations,
        loop=0,
        disposal=1,
        optimize=True,
        interlace=False,
    )


def collapse_transition(
    frames: list[TransitionFrame],
    *,
    preserve_last: bool,
) -> list[TransitionFrame]:
    """Halve submission transition frames while retaining exact total time."""
    ending: list[TransitionFrame] = []
    work = frames
    if preserve_last:
        ending = [frames[-1]]
        work = frames[:-1]
    collapsed: list[TransitionFrame] = []
    for index in range(0, len(work), 2):
        group = work[index : index + 2]
        representative = group[-1]
        collapsed.append(
            TransitionFrame(
                image=representative.image,
                duration_ms=sum(item.duration_ms for item in group),
                opacities=representative.opacities,
                assets=representative.assets,
                direction=representative.direction,
                progress=representative.progress,
                exact_target=representative.exact_target,
            )
        )
    result = collapsed + ending
    assert sum(item.duration_ms for item in result) == TRANSITION_MS
    return result


def resize_sequence(images: Iterable[Image.Image], width: int) -> list[Image.Image]:
    resized: list[Image.Image] = []
    for image in images:
        height = round(width * image.height / image.width)
        resized.append(image.resize((width, height), Image.Resampling.LANCZOS))
    return resized


def gif_metadata(path: Path) -> dict[str, object]:
    with Image.open(path) as gif:
        durations = []
        frames = []
        for frame in ImageSequence.Iterator(gif):
            durations.append(int(frame.info.get("duration", 0)))
            frames.append(frame.convert("RGB"))
        return {
            "dimensions": list(gif.size),
            "frame_count": len(frames),
            "durations_ms": durations,
            "duration_ms": sum(durations),
            "loop": gif.info.get("loop"),
            "size_bytes": path.stat().st_size,
            "first_equals_final": ImageChops.difference(frames[0], frames[-1]).getbbox() is None,
            "decoded_frames": frames,
        }


def verify_output(
    path: Path,
    expected_size: tuple[int, int],
    expected_frames: int,
    *,
    civic_hold_index: int,
    classified_hold_index: int,
    first_transition_count: int,
    return_transition_count: int,
) -> dict[str, object]:
    metadata = gif_metadata(path)
    assert tuple(metadata["dimensions"]) == expected_size
    assert metadata["duration_ms"] == 14_000
    assert metadata["loop"] == 0
    assert metadata["frame_count"] == expected_frames
    durations = metadata["durations_ms"]
    assert durations[civic_hold_index] == STABLE_HOLD_MS
    assert sum(durations[1 : 1 + first_transition_count]) == TRANSITION_MS
    assert durations[classified_hold_index] == STABLE_HOLD_MS
    assert (
        sum(
            durations[
                classified_hold_index + 1 :
                classified_hold_index + 1 + return_transition_count
            ]
        )
        == TRANSITION_MS
    )
    assert metadata["first_equals_final"]
    decoded = metadata.pop("decoded_frames")
    assert all(frame.getbbox() is not None for frame in decoded)
    return metadata


def print_report(
    master_report: dict[str, object],
    submission_report: dict[str, object] | None,
    assets: list[Path],
    transitions: list[TransitionFrame],
    anchor_hashes: dict[str, str],
) -> None:
    opacity_values = [
        opacity
        for transition in transitions
        for opacity in transition.opacities
    ]
    report = {
        "anchors": {
            str(CIVIC_PATH.relative_to(ROOT)): anchor_hashes[CIVIC_PATH.name],
            str(CLASSIFIED_PATH.relative_to(ROOT)): anchor_hashes[CLASSIFIED_PATH.name],
        },
        "transition_assets": [str(path.relative_to(ROOT)) for path in assets],
        "random_seed": RANDOM_SEED,
        "timeline_ms": {
            "civic_hold": STABLE_HOLD_MS,
            "civic_to_classified": TRANSITION_MS,
            "classified_hold": STABLE_HOLD_MS,
            "classified_to_civic": TRANSITION_MS,
            "total": 14_000,
        },
        "insert_opacity_observed": {
            "minimum": min(opacity_values),
            "maximum": max(opacity_values),
            "unique_values": len(set(opacity_values)),
        },
        "master": master_report,
        "submission": submission_report,
    }
    print(json.dumps(report, indent=2))


def main() -> None:
    for required in (CIVIC_PATH, CLASSIFIED_PATH):
        if not required.is_file():
            raise FileNotFoundError(f"Required approved render not found: {required}")

    anchor_hashes = {
        CIVIC_PATH.name: sha256(CIVIC_PATH),
        CLASSIFIED_PATH.name: sha256(CLASSIFIED_PATH),
    }
    civic = load_rgb(CIVIC_PATH)
    classified = load_rgb(CLASSIFIED_PATH)
    if civic.size != (1200, 1500) or classified.size != civic.size:
        raise RuntimeError(
            f"Approved anchors must both be 1200x1500; got {civic.size} and {classified.size}."
        )

    asset_paths = discover_transition_assets()
    assets = [
        (str(path.relative_to(ROOT)), cover_image(load_rgb(path), civic.size))
        for path in asset_paths
    ]
    rng = random.Random(RANDOM_SEED)
    first_transition = build_transition(
        civic,
        classified,
        assets,
        rng,
        "civic_to_classified",
        final_exact_target=False,
    )
    return_transition = build_transition(
        classified,
        civic,
        assets,
        rng,
        "classified_to_civic",
        final_exact_target=True,
    )

    # Source-truth checks happen before lossy GIF palette encoding.
    assert ImageChops.difference(civic, load_rgb(CIVIC_PATH)).getbbox() is None
    assert ImageChops.difference(classified, load_rgb(CLASSIFIED_PATH)).getbbox() is None
    assert ImageChops.difference(return_transition[-1].image, civic).getbbox() is None

    master_images = (
        [civic]
        + [item.image for item in first_transition]
        + [classified]
        + [item.image for item in return_transition]
    )
    master_durations = (
        [STABLE_HOLD_MS]
        + [item.duration_ms for item in first_transition]
        + [STABLE_HOLD_MS]
        + [item.duration_ms for item in return_transition]
    )
    classified_hold_index = 1 + len(first_transition)
    save_gif(
        OUTPUT_PATH,
        master_images,
        master_durations,
        colors=MASTER_COLORS,
        dither=MASTER_DITHER,
    )
    master_report = verify_output(
        OUTPUT_PATH,
        civic.size,
        len(master_images),
        civic_hold_index=0,
        classified_hold_index=classified_hold_index,
        first_transition_count=len(first_transition),
        return_transition_count=len(return_transition),
    )

    submission_report: dict[str, object] | None = None
    if OUTPUT_PATH.stat().st_size > SUBMISSION_TARGET_BYTES:
        compact_first = collapse_transition(first_transition, preserve_last=False)
        compact_return = collapse_transition(return_transition, preserve_last=True)
        compact_images = (
            [civic]
            + [item.image for item in compact_first]
            + [classified]
            + [item.image for item in compact_return]
        )
        compact_durations = (
            [STABLE_HOLD_MS]
            + [item.duration_ms for item in compact_first]
            + [STABLE_HOLD_MS]
            + [item.duration_ms for item in compact_return]
        )
        compact_classified_index = 1 + len(compact_first)

        for width, anchor_colors, transition_colors in SUBMISSION_SPECS:
            resized = resize_sequence(compact_images, width)
            palette_sizes = []
            for index in range(len(resized)):
                is_anchor = index in {
                    0,
                    compact_classified_index,
                    len(resized) - 1,
                }
                palette_sizes.append(anchor_colors if is_anchor else transition_colors)
            save_gif(
                SUBMISSION_PATH,
                resized,
                compact_durations,
                colors=palette_sizes,
                dither=Image.Dither.FLOYDSTEINBERG,
            )
            expected_size = (width, round(width * civic.height / civic.width))
            submission_report = verify_output(
                SUBMISSION_PATH,
                expected_size,
                len(resized),
                civic_hold_index=0,
                classified_hold_index=compact_classified_index,
                first_transition_count=len(compact_first),
                return_transition_count=len(compact_return),
            )
            submission_report["anchor_palette_colors"] = anchor_colors
            submission_report["transition_palette_colors"] = transition_colors
            if SUBMISSION_PATH.stat().st_size <= SUBMISSION_TARGET_BYTES:
                break
    elif SUBMISSION_PATH.exists():
        SUBMISSION_PATH.unlink()

    # Confirm the approved input files remain byte-identical.
    assert sha256(CIVIC_PATH) == anchor_hashes[CIVIC_PATH.name]
    assert sha256(CLASSIFIED_PATH) == anchor_hashes[CLASSIFIED_PATH.name]

    print_report(
        master_report,
        submission_report,
        asset_paths,
        first_transition + return_transition,
        anchor_hashes,
    )


if __name__ == "__main__":
    main()
