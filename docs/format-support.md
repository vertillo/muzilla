# Format support

Classification derived from implementation evidence (tags/reader.py, tags/writer.py, pipeline/scan.py) and disposable-fixture verification.

## Supported

Verified via disposable audio fixtures (tests/fixtures/audio/silence.*) for read, write, Apply and Undo:

- MP3 (.mp3) — ID3
- FLAC (.flac) — Vorbis Comments
- Ogg Vorbis (.ogg) — Vorbis Comments
- Opus (.opus) — Vorbis Comments
- M4A/MP4 (.m4a) — MP4 atoms
- WAV (.wav) — ID3/RIFF
- AIFF (.aiff, .aif) — ID3

Each format is covered by tests/tags/test_reader.py, tests/tags/test_writer.py, and changes applier integration (per-file temp write, fsync, no-clobber replace).

## Best-effort

None at this time. This category is reserved for formats with partial verification (e.g., read-only support) once disposable round-trip evidence exists.

## Unsupported

Explicitly not advertised as supported until read/write/Apply/Undo evidence exists. The application must not infer support from an extension and must reject unverified reviewed writes:

- WavPack (.wv)
- WMA/ASF (.wma, .asf)
- DSF (.dsf, .dff)

Scan excludes unsupported extensions from AUDIO_EXTENSIONS; a file with such extension is not treated as audio and will not be catalogued. If an unsupported file is presented for reviewed write, the writer raises TagWriteError/TagReadError and the Apply path fails closed without clobbering or silently accepting.

## Verification

Run:

```
uv run pytest tests/tags/test_reader.py tests/tags/test_writer.py tests/tags/test_format_support.py -q
```

All supported formats have round-trip fixtures; unsupported formats have explicit rejection tests.
