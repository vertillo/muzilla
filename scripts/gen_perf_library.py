"""Synthesizes a scratch library for performance tests. Not shipped in the
wheel — a dev-only tool.

Copies the committed 1s `silence.mp3` fixture N times and retags each
copy via the real tags/writer.py write path (the same code the app
uses, not a hand-rolled mutagen script), producing:

- a realistic ~50/50 album/singleton mix for a flat library
- era-varying tag completeness: a fraction of tracks are missing
  genre/label/catalog_number/isrc, simulating older, sparser rips
- deliberate near-duplicate titles across a handful of "artists," to
  give the grouping cascade and matching something to actually chew on
  rather than 100k trivially-distinct singletons

Run: `python scripts/gen_perf_library.py --count 100000 --out /path/to/scratch`

The source fixture is pre-tagged; every field this generator does not
explicitly overwrite is inherited by each copy. The grouping inputs
`mb_release_id` and `track_total` are set explicitly below so generated
groups follow the requested synthetic data. Other baked-in fields remain
unchanged because the grouping cascade does not read them.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from muzilla.tags.writer import write_fields  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "audio" / "silence.mp3"

# Two independent word lists combined pairwise (40*35 = 1400 possible
# names, 200 sampled without replacement) rather than "Artist {i}":
# names sharing only a numeric suffix are too similar for the grouping
# cascade's fuzzy string-distance clustering. Distinct word components
# keep the synthetic artists separate while still exercising near matches.
_NAME_PART_A = [
    "Velvet", "Crimson", "Iron", "Silver", "Broken", "Wild", "Electric", "Hollow",
    "Golden", "Northern", "Paper", "Glass", "Amber", "Copper", "Distant", "Rusty",
    "Marble", "Ashen", "Violet", "Faded", "Coastal", "Winter", "Desert", "Nocturne",
    "Static", "Analog", "Feral", "Quiet", "Restless", "Salt", "Ember", "Frost",
    "Lantern", "Ragged", "Hidden", "Slow", "Bitter", "Radiant", "Splinter", "Wandering",
]
_NAME_PART_B = [
    "Wolves", "Harbor", "Engine", "Choir", "Radio", "Garden", "Horizon", "Machine",
    "River", "Collective", "Ghosts", "Signal", "Orchard", "Parade", "Circuit",
    "Meadow", "Tunnel", "Compass", "Anchor", "Pigeons", "Foxes", "Lighthouse", "Carnival",
    "Serpent", "Kingdom", "Mirage", "Echoes", "Wreckage", "Blossom", "Antlers", "Vultures",
    "Prophets", "Junction", "Sirens", "Embassy",
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
    "Midnight", "Silence", "Echo", "Fragment", "Horizon", "Static", "Drift",
    "Signal", "Hollow", "Ember", "Glass", "Tide", "Shadow", "Pulse", "Rain",
]


def _random_title(rng: random.Random) -> str:
    return " ".join(rng.sample(_TITLE_WORDS, k=rng.randint(1, 3)))


def _era_completeness(rng: random.Random) -> bool:
    """~30% of tracks simulate an older, sparsely-tagged rip missing
    genre/label/catalog_number/isrc entirely."""
    return rng.random() < 0.30


def generate(count: int, out_dir: Path, *, seed: int = 0) -> None:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    track_index = 0

    while generated < count:
        artist = rng.choice(_ARTISTS)
        is_singleton = rng.random() < 0.5  # ~50/50 album/singleton mix

        batch: list[dict[str, object]]
        if is_singleton:
            batch = [
                {"title": _random_title(rng), "track_no": None, "album": None, "track_total": None}
            ]
        else:
            album = f"{artist} — Album {rng.randint(1, _ALBUMS_PER_ARTIST)}"
            n_tracks = rng.randint(*_TRACKS_PER_ALBUM)
            batch = [
                {
                    "title": _random_title(rng),
                    "track_no": i + 1,
                    "album": album,
                    "track_total": n_tracks,
                }
                for i in range(n_tracks)
            ]

        sparse = _era_completeness(rng)
        year = rng.randint(1975, 2024)

        for item in batch:
            if generated >= count:
                break
            filename = f"track_{track_index:07d}.mp3"
            dest = out_dir / filename
            shutil.copy(FIXTURE, dest)

            field_values: dict[str, object] = {
                "title": item["title"],
                "artist": artist,
                "album_artist": artist if item["album"] else None,
                "album": item["album"],
                "track_no": item["track_no"],
                "date": str(year),
                # The fixture contains a release id and track count. Replace
                # both so grouping uses the synthetic artist/album structure.
                "mb_release_id": None,
                "track_total": item["track_total"],
            }
            if not sparse:
                field_values["genre"] = [rng.choice(_GENRES)]
                field_values["label"] = rng.choice(_LABELS)
                field_values["catalog_number"] = f"CAT-{rng.randint(1000, 9999)}"
                field_values["isrc"] = f"US{rng.randint(100, 999)}{rng.randint(1000000, 9999999)}"

            write_fields(dest, field_values)

            track_index += 1
            generated += 1
            if generated % 5000 == 0:
                print(f"  {generated}/{count} generated", file=sys.stderr)

    print(f"done: {generated} tracks written to {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    generate(args.count, args.out, seed=args.seed)
