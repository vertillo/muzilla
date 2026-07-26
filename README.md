# muzilla

A self-hosted music **metadata** manager — like [beets](https://github.com/beetbox/beets), but scoped deliberately to metadata: tags, album art, lyrics, genres, ReplayGain, and acoustic fingerprints. Unlike [MusicBrainz Picard](https://picard.musicbrainz.org/), muzilla fetches from multiple sources at once (MusicBrainz, Discogs, Deezer) and merges them with visible provenance. Unlike beets, it ships a real browser GUI, not just a CLI.

muzilla never plays audio, never manages a listening library, and never touches your files until you review a diff and explicitly apply it.

## Status

Early development — not yet usable. See [`docs/PLAN.md`](docs/PLAN.md) for the full architecture and phased roadmap.

## Core idea

Browse your catalog → select tracks → fetch metadata or edit manually → review a field-level diff, with every value's source visible and overridable → accept → apply (undoable).

## Stack

Python 3.12 / FastAPI / SQLite core, with a Typer CLI and a React GUI served from the same container. See `docs/PLAN.md` for the full rationale.

## Development

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
muzilla serve --reload
```

## License

MIT — see [LICENSE](LICENSE).
