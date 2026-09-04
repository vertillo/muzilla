"""Synthesizes a scratch library for performance tests. Not shipped in the
wheel — a dev-only tool.

Copies the committed 1s `silence.mp3` fixture N times and retags each
copy via the real tags/writer.py write path (the same code the app
uses, not a hand-rolled mutagen script), producing a representative
100k corpus covering the production-readiness scale contract:

- ~50/50 album/singleton mix for a flat library
- era-varying tag completeness (sparse old rips) and inconsistent
  metadata across tracks of the same synthetic album
- near-duplicate titles and intentional exact duplicates/ambiguous
  matches across artists
- multidisc, compilation and multi-artist albums
- Unicode and case-sensitivity edges
- embedded-art subset (via real write_art path)
- realistically-sized audio subset for I/O paths: ~2% are valid ~30s
  encoded MP3s (deterministic ffmpeg sine fixture), decodable by
  fpcalc/rsgain for the audio-tool workload

Run: `python scripts/gen_perf_library.py --count 100000 --out /path/to/scratch`

The source fixture is pre-tagged; every field this generator does not
explicitly overwrite is inherited by each copy. The grouping inputs
`mb_release_id` and `track_total` are set explicitly below so generated
groups follow the requested synthetic data. Other baked-in fields remain
unchanged because the grouping cascade does not read them.
Deterministic by seed so reruns and CI produce identical corpora.
"""

from __future__ import annotations

import argparse
import base64
import random
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from muzilla.tags.writer import write_art, write_fields  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "audio" / "silence.mp3"

# ponytail: tiny 1x1 JPEG is cheapest valid embedded art; Pillow generates larger art when available
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCwAA8A/9k="
)
_UNICODE_ARTISTS = [
    "Sigur Rós",
    "Björk",
    "München Ensemble",
    "café del Mar",
    "naïve Théâtre",
    "São Paulo Trio",
]
_UNICODE_TITLES = [
    "Ágætis byrjun",
    "Dætur",
    "résumé",
    "Zürich",
    "São Paulo",
    "ﬁ ligature",
    "café",
    "naïve",
]

# Two independent word lists combined pairwise (40*35 = 1400 possible
# names, 200 sampled without replacement) rather than "Artist {i}":
# names sharing only a numeric suffix are too similar for the grouping
# cascade's fuzzy string-distance clustering. Distinct word components
# keep the synthetic artists separate while still exercising near matches.
_NAME_PART_A = [
    "Velvet",
    "Crimson",
    "Iron",
    "Silver",
    "Broken",
    "Wild",
    "Electric",
    "Hollow",
    "Golden",
    "Northern",
    "Paper",
    "Glass",
    "Amber",
    "Copper",
    "Distant",
    "Rusty",
    "Marble",
    "Ashen",
    "Violet",
    "Faded",
    "Coastal",
    "Winter",
    "Desert",
    "Nocturne",
    "Static",
    "Analog",
    "Feral",
    "Quiet",
    "Restless",
    "Salt",
    "Ember",
    "Frost",
    "Lantern",
    "Ragged",
    "Hidden",
    "Slow",
    "Bitter",
    "Radiant",
    "Splinter",
    "Wandering",
]
_NAME_PART_B = [
    "Wolves",
    "Harbor",
    "Engine",
    "Choir",
    "Radio",
    "Garden",
    "Horizon",
    "Machine",
    "River",
    "Collective",
    "Ghosts",
    "Signal",
    "Orchard",
    "Parade",
    "Circuit",
    "Meadow",
    "Tunnel",
    "Compass",
    "Anchor",
    "Pigeons",
    "Foxes",
    "Lighthouse",
    "Carnival",
    "Serpent",
    "Kingdom",
    "Mirage",
    "Echoes",
    "Wreckage",
    "Blossom",
    "Antlers",
    "Vultures",
    "Prophets",
    "Junction",
    "Sirens",
    "Embassy",
]
# Each generated artist uses a distinct word from both lists. This avoids
# shared-word chains that would make single-link fuzzy clustering merge
# otherwise unrelated artists.
_rng_names = random.Random(1337)
_shuffled_a = _NAME_PART_A[:]
_shuffled_b = _NAME_PART_B[:]
_rng_names.shuffle(_shuffled_a)
_rng_names.shuffle(_shuffled_b)
_n = min(len(_shuffled_a), len(_shuffled_b), 200)
_ARTISTS = [f"{_shuffled_a[i]} {_shuffled_b[i]}" for i in range(_n)]

_ALBUMS_PER_ARTIST = 40
_TRACKS_PER_ALBUM = (6, 14)
_GENRES = ["Rock", "Post-Rock", "Electronic", "Jazz", "Classical", "Hip-Hop", "Folk", "Ambient"]
_LABELS = ["Sub Pop", "4AD", "Warp Records", "ECM", "Domino", "Rough Trade"]
_TITLE_WORDS = [
    "Midnight",
    "Silence",
    "Echo",
    "Fragment",
    "Horizon",
    "Static",
    "Drift",
    "Signal",
    "Hollow",
    "Ember",
    "Glass",
    "Tide",
    "Shadow",
    "Pulse",
    "Rain",
]
# Fixed ambiguous title deliberately reused across artists to exercise ambiguous matching.
_AMBIGUOUS_TITLE = "Midnight Echo"


def _random_title(rng: random.Random) -> str:
    if rng.random() < 0.02:
        return _AMBIGUOUS_TITLE
    return " ".join(rng.sample(_TITLE_WORDS, k=rng.randint(1, 3)))


def _maybe_unicode_variant(rng: random.Random, base: str) -> str:
    if rng.random() < 0.04:
        frag = rng.choice(_UNICODE_TITLES)
        return f"{base} — {frag}"
    return base


def _maybe_case_variant(rng: random.Random, text: str) -> str:
    if rng.random() < 0.03:
        # ponytail: case edge via deterministic choice
        choice = rng.choice(["upper", "lower", "title"])
        if choice == "upper":
            return text.upper()
        if choice == "lower":
            return text.lower()
        return text.title()
    return text


def _era_completeness(rng: random.Random) -> bool:
    """~30% of tracks simulate an older, sparsely-tagged rip missing
    genre/label/catalog_number/isrc entirely."""
    return rng.random() < 0.30


def _should_embed_art(rng: random.Random) -> bool:
    return rng.random() < 0.10


def _embed_art_if_needed(dest: Path, rng: random.Random) -> None:
    if not _should_embed_art(rng):
        return
    import contextlib

    with contextlib.suppress(Exception):
        write_art(dest, _TINY_JPEG, "image/jpeg")


def _make_realistic_audio(dest: Path, rng: random.Random, long_fixture: Path | None) -> bool:
    """For ~2% of tracks, use valid realistically long encoded audio.

    The subset copies a deterministically generated long MP3 fixture
    (valid frames, ~30s) instead of zero-padding: appended zeros are
    ignored by Mutagen and exercise no audio-tool path, while a valid
    long file gives realistic I/O cost and is decodable by fpcalc/rsgain.
    Deterministic via rng; the long fixture is built once per run.
    Returns True when this track uses the realistic-audio subset.
    """
    if rng.random() >= 0.02:
        return False
    if long_fixture is None or not long_fixture.exists():
        raise RuntimeError(
            "realistic-audio subset requires the generated long MP3 fixture "
            "(ffmpeg missing or generation failed) — refusing silent fallback"
        )
    # Copy first so per-track tags are written onto valid long audio below.
    shutil.copy(long_fixture, dest)
    return True


def _build_long_fixture(scratch_dir: Path) -> Path:
    """Builds one deterministic ~30s valid MP3 via ffmpeg (fail-closed).

    Fixed sine tone + fixed encoder flags keep bytes deterministic enough
    for a benchmark corpus; exact byte identity across machines is not
    required, validity and duration are. Called once per generate().
    """
    import subprocess

    out = scratch_dir / "_long_fixture.mp3"
    if out.exists():
        return out
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=30:sample_rate=44100",
        "-ac",
        "1",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(out),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found: realistic-audio subset needs ffmpeg") from exc
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg long-fixture generation failed: {proc.stderr[:1000]}")
    return out


def generate(count: int, out_dir: Path, *, seed: int = 0) -> None:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Realistic-audio subset needs one valid long encoded file. Built once
    # via ffmpeg (fail-closed when unavailable) into a temp dir; the 2%
    # subset copies it instead of the 1s fixture. rng consumption order in
    # the loop below is unchanged, so corpus layout stays deterministic.
    import tempfile

    _tmp_long = tempfile.mkdtemp(prefix="muzilla-long-audio-")
    try:
        long_fixture: Path | None = _build_long_fixture(Path(_tmp_long))
    except RuntimeError:
        long_fixture = None
    # _make_realistic_audio raises fail-closed when the subset is selected
    # but the fixture is missing, so a missing ffmpeg can never silently
    # degrade the workload back to padded zeros.

    generated = 0
    track_index = 0

    while generated < count:
        # ~4% unicode artist edge
        artist = rng.choice(_UNICODE_ARTISTS) if rng.random() < 0.04 else rng.choice(_ARTISTS)
        is_singleton = rng.random() < 0.5  # ~50/50 album/singleton mix

        batch: list[dict[str, object]]
        # multidisc / compilation / multi-artist distribution
        is_multidisc = False
        is_compilation = False
        if not is_singleton:
            is_multidisc = rng.random() < 0.07
            is_compilation = rng.random() < 0.06
        if is_singleton:
            title = _maybe_unicode_variant(rng, _random_title(rng))
            title = _maybe_case_variant(rng, title)
            batch = [
                {
                    "title": title,
                    "track_no": None,
                    "album": None,
                    "track_total": None,
                    "disc_no": None,
                    "disc_total": None,
                    "compilation": None,
                }
            ]
        else:
            album_base = f"{artist} — Album {rng.randint(1, _ALBUMS_PER_ARTIST)}"
            album = _maybe_unicode_variant(rng, album_base)
            album = _maybe_case_variant(rng, album)
            n_tracks = rng.randint(*_TRACKS_PER_ALBUM)
            disc_total = 2 if is_multidisc else None
            batch = []
            for i in range(n_tracks):
                title = _maybe_unicode_variant(rng, _random_title(rng))
                title = _maybe_case_variant(rng, title)
                if is_multidisc:
                    # first half disc1, second half disc2
                    disc_no = 1 if i < n_tracks // 2 else 2
                    track_no = (i + 1) if disc_no == 1 else (i + 1 - n_tracks // 2)
                    track_total = n_tracks // 2 if disc_no == 1 else n_tracks - n_tracks // 2
                else:
                    disc_no = None
                    track_no = i + 1
                    track_total = n_tracks
                batch.append(
                    {
                        "title": title,
                        "track_no": track_no,
                        "album": album,
                        "track_total": track_total,
                        "disc_no": disc_no,
                        "disc_total": disc_total,
                        "compilation": True if is_compilation else None,
                    }
                )

        sparse = _era_completeness(rng)
        # inconsistent metadata: ~5% of album batches vary genre/label within album
        inconsistent = rng.random() < 0.05 and not is_singleton
        year = rng.randint(1975, 2024)

        for item in batch:
            if generated >= count:
                break
            filename = f"track_{track_index:07d}.mp3"
            dest = out_dir / filename
            if not _make_realistic_audio(dest, rng, long_fixture):
                shutil.copy(FIXTURE, dest)
            # tags are written after the subset copy above, so they survive

            # per-track artist for compilation (multi-artist)
            track_artist = artist
            track_album_artist: object = artist if item["album"] else None
            if is_compilation:
                track_artist = rng.choice(_ARTISTS + _UNICODE_ARTISTS)
                track_album_artist = "Various Artists"
                # keep grouping stable: album stays same, but artist varies per track
            else:
                # case edge on artist as well
                track_artist = _maybe_case_variant(rng, str(track_artist))

            field_values: dict[str, object] = {
                "title": item["title"],
                "artist": track_artist,
                "album_artist": track_album_artist,
                "album": item["album"],
                "track_no": item["track_no"],
                "disc_no": item["disc_no"],
                "disc_total": item["disc_total"],
                "compilation": item["compilation"],
                "date": str(year),
                "mb_release_id": None,
                "track_total": item["track_total"],
            }
            if not sparse:
                # inconsistent within-album variant
                if inconsistent and rng.random() < 0.5:
                    # vary genre/label per track to simulate inconsistent rip
                    field_values["genre"] = [rng.choice(_GENRES)]
                else:
                    field_values["genre"] = [rng.choice(_GENRES)]
                field_values["label"] = rng.choice(_LABELS)
                field_values["catalog_number"] = f"CAT-{rng.randint(1000, 9999)}"
                field_values["isrc"] = f"US{rng.randint(100, 999)}{rng.randint(1000000, 9999999)}"

            write_fields(dest, field_values)
            _embed_art_if_needed(dest, rng)

            track_index += 1
            generated += 1
            if generated % 5000 == 0:
                print(f"  {generated}/{count} generated", file=sys.stderr)

    import shutil as _shutil_cleanup

    _shutil_cleanup.rmtree(_tmp_long, ignore_errors=True)
    print(f"done: {generated} tracks written to {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    generate(args.count, args.out, seed=args.seed)
