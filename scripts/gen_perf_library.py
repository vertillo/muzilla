"""Synthesizes a scratch library for the 100k-track performance pass
(docs/product-spec.md). Not shipped in the wheel — a dev-only tool.

Copies the committed 1s `silence.mp3` fixture N times and retags each
copy via the real tags/writer.py write path (the same code the app
uses, not a hand-rolled mutagen script), producing:

- a realistic ~50/50 album/singleton mix, per the project's own
  defining constraint (docs/product-spec.md's flat-library premise)
- era-varying tag completeness: a fraction of tracks are missing
  genre/label/catalog_number/isrc, simulating older, sparser rips
- deliberate near-duplicate titles across a handful of "artists," to
  give the grouping cascade and matching something to actually chew on
  rather than 100k trivially-distinct singletons

Run: `python scripts/gen_perf_library.py --count 100000 --out /path/to/scratch`

The source fixture is a real, pre-tagged test fixture (tests/fixtures/
audio/tag_fixtures.py's COMMON dict) — every field this generator does
NOT explicitly overwrite is silently inherited identically by every
copy. Two of those (mb_release_id, track_total) were found to corrupt
the grouping cascade's results this way and are now explicitly cleared/
set below. composer/disc_no/bpm/comment are left baked-in (identical
across all tracks) because nothing in the grouping cascade reads them —
but if a future extension of this script exercises matching or other
logic that does, check this list again before trusting the results.
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
# names sharing only a numeric suffix are adversarial input for the
# grouping cascade's fuzzy string-distance clustering (domain/
# normalize.py's string_dist scores "Artist 34" vs "Artist 89" as
# very close, since they differ only in a low-entropy numeric tail),
# which single-link-clusters transitively -- found by actually running
# the cascade against the first version of this generator, which
# collapsed all 100k tracks into one giant album group instead of
# ~200 artists' worth of distinct ones. Real artist names don't share
# a common textual prefix, so the generator shouldn't manufacture data
# that only breaks because it's unrealistic in that specific way.
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
# Each of the 200 artist names uses a distinct word from _NAME_PART_A
# AND a distinct word from _NAME_PART_B -- no two artist names share
# either word (unlike a full cross-product sample, which can still
# pick "Ember Garden" and "Amber Garden" and share "Garden"). Two
# names sharing a single word both scored well under the cascade's
# fuzzy-match threshold in practice, and single-link clustering
# chains merges transitively, so even one shared-word pair anywhere
# in the set risks collapsing many otherwise-distinct artists into
# one giant cluster -- confirmed by testing pairwise string_dist
# across the earlier (cross-product-sampled) version of this list.
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
                # The source fixture (tests/fixtures/audio/silence.mp3) is a
                # real, pre-tagged test fixture with several baked-in values
                # (tag_fixtures.py's COMMON dict) that every copy silently
                # inherited until these two lines existed -- both found by
                # actually running the grouping cascade against generated
                # data, neither by inspection:
                # - mb_release_id: made Stage 1 (group by mb_release_id)
                #   merge all 100k tracks into one "release".
                # - track_total (baked in as 10): every track agreeing on
                #   the same fixed track_total, regardless of its real
                #   cluster size, made _apply_partial_album_flag flag
                #   nearly every real album as "partial_album" (expected
                #   10, actual 2-4), rather than "album".
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
