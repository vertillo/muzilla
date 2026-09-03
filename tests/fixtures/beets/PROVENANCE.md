# Beets upstream corpus provenance

- **Upstream project:** [beetbox/beets](https://github.com/beetbox/beets)
- **Pinned tag:** `v2.10.0`
- **Pinned commit:** `5f6b2d35d186fb9f6402dfb7ec904b6c8383a6cd`
- **Upstream path:** `test/test_template.py`
- **Raw URL:** `https://raw.githubusercontent.com/beetbox/beets/v2.10.0/test/test_template.py`
- **License:** MIT — Copyright 2016, Adrian Sampson (preserved verbatim in vendored file header)
- **SHA-256 (vendored file):** `4b65d8e6400b0a957c630f9f164b997ebb96082d9319813e326b211bf6b2a6d4`
- **Vendored path in this repo:** `tests/fixtures/beets/test_template_v2.10.0.py.txt` (byte-identical to upstream raw file; `.txt` suffix keeps the vendored Python fixture out of lint/type-check while preserving exact content and license header)
- **Retrieval:** `curl -fsSL https://raw.githubusercontent.com/beetbox/beets/v2.10.0/test/test_template.py -o tests/fixtures/beets/test_template_v2.10.0.py.txt`
- **Verification:** `sha256sum tests/fixtures/beets/test_template_v2.10.0.py.txt` must equal SHA-256 above; `tests/paths/test_beets_compat.py::test_beets_compat_vendored_fixture_integrity` asserts this.

No beets runtime dependency is introduced. No network is used at test time. This file is the canonical, license-preserving upstream fixture; `tests/paths/test_beets_compat.py` is the deterministic translation/mapping that executes against muzilla's engine.
