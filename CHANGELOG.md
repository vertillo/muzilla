# CHANGELOG

<!-- version list -->

## v1.1.1 (2026-09-05)

### Bug Fixes

- **release**: Gate exact generated release SHA with reusable CI before publish
  ([`ca82e1d`](https://github.com/anphetamina/muzilla/commit/ca82e1dd927ecc8714a996efbaf7775b55b1f087))


## v1.1.0 (2026-09-05)

### Bug Fixes

- Address reviewer P1 for cancellation (fingerprint post-lookup check, undo retryable, test
  tautology)
  ([`941a5b5`](https://github.com/anphetamina/muzilla/commit/941a5b5ff8d1cb47876162f7b5d4a5719594cca9))

- Complete SETTINGS-SECRETS-001, PIPELINE-CACHE-001, ART-COVER-SOURCE-001 — idempotency, cache
  reconstruction, enrich_art cancel tolerance
  ([`8796d81`](https://github.com/anphetamina/muzilla/commit/8796d81df9630ee28a1bf182856fb80b5c8405f9))

- Dashboard test work_unit
  ([`bfccc75`](https://github.com/anphetamina/muzilla/commit/bfccc75ec5795a045fc7f50430cdb55a80d86a29))

- Restore matrix rows for finalization (required before green gates)
  ([`2436076`](https://github.com/anphetamina/muzilla/commit/243607635b65e669ba467b5f3a3693792c86e88f))

- Reviewer BLOCKs for 0019 migration and apply handler docstring
  ([`a796cd9`](https://github.com/anphetamina/muzilla/commit/a796cd9ff296ca026560813a711642cf9ab40e5c))

- **cache**: Ensure stale URL import returns ReleaseCandidate via payload conversion
  ([`76bd2ae`](https://github.com/anphetamina/muzilla/commit/76bd2ae18ec8c8d1643c4694b66c7c45d9ff7b50))

- **cache**: Ensure URL import cache returns ReleaseCandidate and handles offline/stale correctly
  ([`0cacf73`](https://github.com/anphetamina/muzilla/commit/0cacf736a862044ec675ace4bd423371de5ebb9e))

- **cache**: Ensure URL import stale fallback returns ReleaseCandidate via full reconstruction
  ([`7787080`](https://github.com/anphetamina/muzilla/commit/7787080f628dfddccbedef5057f666771a93bc9d))

- **cache**: Ensure URL import via gateway maintains idempotency with full candidate
  ([`f999db0`](https://github.com/anphetamina/muzilla/commit/f999db0e4bd41a6eaaa2f3b5b50d22070499ebba))

- **cache**: Reconstruct full ReleaseCandidate from cached asdict for URL import stale fallback
  ([`8177695`](https://github.com/anphetamina/muzilla/commit/8177695fbbbe4fafd98d14fdef58400c5bc74971))

- **cache**: Reconstruct full ReleaseCandidate without importing matching layer (lint-imports)
  ([`bc29948`](https://github.com/anphetamina/muzilla/commit/bc29948824a73bfcc5b806648c66221e8cced775))

- **cache**: Reconstruct LyricsResult/ArtRef from cache, add fresh-hit art/lyrics E2E tests
  ([`c070a20`](https://github.com/anphetamina/muzilla/commit/c070a20c427f76d913700b4699fece71c43b20a5))

- **cache**: Remove direct provider fallback, wire URL import through gateway
  ([`e21cbe3`](https://github.com/anphetamina/muzilla/commit/e21cbe3e3c35c8da5fe85bab30a7cede4c6ed48f))

- **cache**: Wire all direct provider calls through durable gateway
  ([`3270f53`](https://github.com/anphetamina/muzilla/commit/3270f53f30708740c379f9ec56b60b86769b379f))

- **cache,api**: Wire enrichment art through cached_get_art and commit generated types
  ([`5a05770`](https://github.com/anphetamina/muzilla/commit/5a05770f2f93600ccfe5d872af5f1f9fbbfe9499))

- **cache,art**: Wire enrichment art via gateway, fix ArtRef/Lyrics deserialization, implement
  Deezer get_art
  ([`5f21ff2`](https://github.com/anphetamina/muzilla/commit/5f21ff24b1fa6aad3a31e345f58fa827f7384b65))

- **ci**: Repair backend gate — legacy-route absence contract and ffmpeg
  ([`1312090`](https://github.com/anphetamina/muzilla/commit/1312090e8394e27a821fdd76fd800f73d94cbdca))

- **e2e**: Derace mock/app startup with fail-fast logs and disjoint ports
  ([`f855932`](https://github.com/anphetamina/muzilla/commit/f8559320e0d6e6153c66678b29e22b4f39bf9c74))

- **e2e**: Fresh-port restart and bind-specific EADDRINUSE detection
  ([`11aee35`](https://github.com/anphetamina/muzilla/commit/11aee35c5288797df3193c91807660336ebb2a82))

- **e2e**: Require bind/listen signature for EADDRINUSE collision classification
  ([`3eb9634`](https://github.com/anphetamina/muzilla/commit/3eb9634a18d76fa6f879ea7e8edc62e424f42d21))

- **e2e**: Use OS-allocated ports with EADDRINUSE-only retry
  ([`4262a18`](https://github.com/anphetamina/muzilla/commit/4262a182401c35a2f35350a433daaf50e3dadb09))

- **enrich_art**: Count all-provider failures as errored not not_found
  ([`f6bcceb`](https://github.com/anphetamina/muzilla/commit/f6bcceb5796138dff382dd0588047a4c840a0be1))

- **frontend**: Bump override floors for js-yaml, brace-expansion, nanoid (npm audit clean)
  ([`d0824b6`](https://github.com/anphetamina/muzilla/commit/d0824b6b3c9137cec823af250d59c63f6a8dc807))

- **frontend**: Show candidate identity details
  ([`6bcf688`](https://github.com/anphetamina/muzilla/commit/6bcf6886caf6e77e4147069eccbf249d4533e01b))

- **jobs**: Add cooperative cancellation and provider resilience
  ([`72e77ea`](https://github.com/anphetamina/muzilla/commit/72e77ea17becde994cadf22988392806169364c1))

- **matching**: Persist provider-cache writes on interactive read paths
  ([`68d3c2a`](https://github.com/anphetamina/muzilla/commit/68d3c2a39e3d04a5e67334387cec2ff7c18ae9a6))

- **paths**: Block destination collisions safely
  ([`f699670`](https://github.com/anphetamina/muzilla/commit/f699670330012457801805647f822fbe54be40b9))

- **release**: Attach release checkout to main with exact source_sha guard
  ([`fb6b463`](https://github.com/anphetamina/muzilla/commit/fb6b46354d67742993218a433ecb98ceab0b6836))

- **release**: Authenticate origin for semantic-release with RELEASE_TOKEN
  ([`ca95a71`](https://github.com/anphetamina/muzilla/commit/ca95a718b87d532283c3d8b18f3c90312fd7fdb8))

- **release**: Authenticate semantic-release as x-access-token
  ([`4bc51fe`](https://github.com/anphetamina/muzilla/commit/4bc51fef655a020c56a8377892f2ef796fc62a26))

- **release**: Authorize reusable publish caller, drop secret inheritance, scope CI read-only
  ([`d5f3ca3`](https://github.com/anphetamina/muzilla/commit/d5f3ca386711e0f34f7deeef56a097f5c3baefa6))

- **release**: Checkout with GITHUB_TOKEN, push with RELEASE_TOKEN
  ([`b90ca16`](https://github.com/anphetamina/muzilla/commit/b90ca16a75e6be470d1fc359d3a7fabc163b480f))

- **release**: Publish via reusable workflow with GITHUB_TOKEN, drop PAT
  ([`c22d2d8`](https://github.com/anphetamina/muzilla/commit/c22d2d898c1729b3fa563319e4f1fcb7a5f41fbc))

- **release**: Validate local origin/main without unauthenticated fetch
  ([`fea8940`](https://github.com/anphetamina/muzilla/commit/fea89405ec7bb202f61d0c07af46ab990513f8b9))

- **replaygain**: Enforce runtime capability readiness
  ([`fd8e2e5`](https://github.com/anphetamina/muzilla/commit/fd8e2e561412206e8ff8bd14631cfd4b2cd73b36))

- **review**: Address P0/P1 findings — art undo, cache gateway, API generation, secrets
  factory-reset, browser fixture
  ([`b138d57`](https://github.com/anphetamina/muzilla/commit/b138d57e899857230dde65df68fb7ded38460ab7))

- **review**: Preserve apply and enrichment proposal semantics
  ([`9515075`](https://github.com/anphetamina/muzilla/commit/951507591317cc764ea51523746c3d807ea14d55))

- **review**: Second block — full cache gateway, Ogg art, generated API, deterministic browser
  fixture
  ([`0e7f890`](https://github.com/anphetamina/muzilla/commit/0e7f8906700e8562a0bb48b4ec47537f0af5f8e9))

- **review**: Wire all cache gateways, Ogg/Opus art, non-primary art source
  ([`f36fddb`](https://github.com/anphetamina/muzilla/commit/f36fddbf287fb25e386cce020df8a6427e588bbc))

- **sandbox**: Align system prune test with proxy message
  ([`0216ad6`](https://github.com/anphetamina/muzilla/commit/0216ad63346f998de82264a627f6fe9d4677b388))

- **sandbox**: Close 4 false positives (VIRTUAL_ENV, exist_ok, timeout, sqlite3)
  ([`2286c34`](https://github.com/anphetamina/muzilla/commit/2286c34aac72f412a45068941235d3e4911840b8))

- **sandbox**: Guard --help and isolate MUZILLA_* for gates (Q4+Q5)
  ([`6606953`](https://github.com/anphetamina/muzilla/commit/66069530008848e53f58d67faf770de9e4a046f5))

- **sandbox**: Mount real .env RO and fail-closed on missing dev vars
  ([`b1b024c`](https://github.com/anphetamina/muzilla/commit/b1b024ce333503b1c15ee7773bc27c57e024b9dd))

- **sandbox**: Pi usa muse-spark in sandbox anche con .pi read-only
  ([`133bb13`](https://github.com/anphetamina/muzilla/commit/133bb132d1fcfcea8c66e4af226977ee0da2964d))

- **sandbox**: Pi-native single B perimeter, RO for .env/*.pem/*.key, toolchain allowlist, 30s
  timeout, verification test
  ([`35741a2`](https://github.com/anphetamina/muzilla/commit/35741a24bacbb7f0b634c0f1d0cc9f99f9311117))

- **sandbox**: Raise mem_limit to 12g for OOM 137 (24GB host)
  ([`4113c2f`](https://github.com/anphetamina/muzilla/commit/4113c2f88f6c8d2662084839f756dc19469297d8))

- **sandbox**: Raise tmpfs /tmp 512m→2g and /workspace/data 1g→2g for ENOSPC
  ([`4d6a21c`](https://github.com/anphetamina/muzilla/commit/4d6a21c08ab0af10ba7a7eadbd64e24013feab96))

- **sandbox**: Single B perimeter normalization and repo-scoped RO
  ([`8432bd4`](https://github.com/anphetamina/muzilla/commit/8432bd4228907964ef88f632719670a71efad6ed))

- **scan**: Bound cancellation-blind windows in library scan
  ([`d6e5b86`](https://github.com/anphetamina/muzilla/commit/d6e5b8684387d26bf6cf5911510de4d9a4888cc2))

- **settings**: Address reviewer P1s for SETTINGS-POLICY-001
  ([`360b5ac`](https://github.com/anphetamina/muzilla/commit/360b5acfa4ae705f24bb01464f8540421e17a5bb))

- **test**: Derace fpcalc-absence fingerprint test via seam monkeypatch
  ([`23773e9`](https://github.com/anphetamina/muzilla/commit/23773e973a70aee32d3a3ad591d0723b40efadfe))

- **tests**: Stabilize suite after REVIEW-CONFLICTS / COMPAT and sandbox env
  ([`f6196ae`](https://github.com/anphetamina/muzilla/commit/f6196ae1e919e9c445fc9736e6d5f499df2d07ee))

- **tests**: Suppress pyright/mypy noise and keep flaky reset skipped
  ([`fa5d0b7`](https://github.com/anphetamina/muzilla/commit/fa5d0b7f5290783c2edcc47d87bfb334ec94e46c))

- **tooling**: Make generate-types work without activated venv
  ([`79f49e7`](https://github.com/anphetamina/muzilla/commit/79f49e793836c6f5bb37195f49aab54608189f4c))

### Chores

- Add .agents project skills
  ([`4761f3d`](https://github.com/anphetamina/muzilla/commit/4761f3d3683cebe63392a8e9e873e2d1a7cfa522))

- Add UI/UX design contract and clarify sandbox/commit policies
  ([`084c2f4`](https://github.com/anphetamina/muzilla/commit/084c2f4ee7547c67d70770de3c57de6cd1c95c1a))

- Agents tweaks
  ([`aa2695c`](https://github.com/anphetamina/muzilla/commit/aa2695c2fd85106c579d5a7e2adc95f5822c01f6))

- Clarify autonomous goal delivery semantics
  ([`ca6cc44`](https://github.com/anphetamina/muzilla/commit/ca6cc440280990eb929fbd1855808d13871dd1e7))

- Cleanup tmp/pyrightconfig, add tmp/ to gitignore, fix lens blockers (is True→truthy, int with
  try/except)
  ([`151b449`](https://github.com/anphetamina/muzilla/commit/151b449b1632c97ec961b7c761843ef2f2e5763e))

- Finish repository hygiene cleanup
  ([`1cc057d`](https://github.com/anphetamina/muzilla/commit/1cc057d3cdf881f8d93d096dcac279424eecba39))

- Formatting
  ([`dd5fec8`](https://github.com/anphetamina/muzilla/commit/dd5fec886779b2a0c2ee5b2d4670e31f369467a0))

- Grill me md files ignore
  ([`28f3999`](https://github.com/anphetamina/muzilla/commit/28f39998767027973216f1fd2c514472d5327a35))

- Hardness tweaks
  ([`d7c87b1`](https://github.com/anphetamina/muzilla/commit/d7c87b168aaca2cf7988c168899d5b562c119f8d))

- Pre-production plan
  ([`398a7e0`](https://github.com/anphetamina/muzilla/commit/398a7e094e4d694a633c4a9f156b208a00f06827))

- Refine agent orchestration and browser-tester setup
  ([`e885c3d`](https://github.com/anphetamina/muzilla/commit/e885c3d5fb93d4f546fc2671a65ee00b3c3aa23c))

- Regenerate frontend types after cache/art wiring (upload gone, refresh)
  ([`e11838f`](https://github.com/anphetamina/muzilla/commit/e11838fccba5a01a0b83943828a234e2663cf556))

- Regenerate frontend types after cache/cover changes (upload gone, refresh param)
  ([`8479c9a`](https://github.com/anphetamina/muzilla/commit/8479c9af42efd6794dfc6f3b1c022310ce3155bf))

- Retain autoformatted cache.py (valid, no logic change)
  ([`510c8c3`](https://github.com/anphetamina/muzilla/commit/510c8c3b34a16154f71fdb5e246ad02ccd072592))

- Retain autoformatted cache.py (valid, no logic change)
  ([`105ac17`](https://github.com/anphetamina/muzilla/commit/105ac176a78462968373c24f4a6702533febdec6))

- Retain autoformatted enrich_art/cache (valid, no logic change)
  ([`44c2f2d`](https://github.com/anphetamina/muzilla/commit/44c2f2d7cf008891e4b6ba9fa02d006ec3f8790c))

- Retain autoformatted test_art_lyrics_cache_hit (valid, no logic change)
  ([`b20a7e1`](https://github.com/anphetamina/muzilla/commit/b20a7e1da4ee57326726c4cc9c4bc643aff33038))

- Setup pi-goal autonomous contract and tooling
  ([`4b1727f`](https://github.com/anphetamina/muzilla/commit/4b1727f1bc53070e2e50a0f0f42544f2da7f070e))

- Sync sandbox compose formatting and pi settings
  ([`d1f4da9`](https://github.com/anphetamina/muzilla/commit/d1f4da9ce9837bbbcb21ec0f2f9b912bdb4188a4))

- Track ui-ux-pro-max skill data, pi settings improvements
  ([`c642dce`](https://github.com/anphetamina/muzilla/commit/c642dce4f393bc718e149b7964b93301c284ebca))

- **api**: Regenerate frontend types with pinned openapi-typescript 7.13.0 (formatter-only, no
  schema change)
  ([`51c2a67`](https://github.com/anphetamina/muzilla/commit/51c2a67faf61497aff3e1f0db2693366eef47fe2))

- **api**: Regenerate review types
  ([`29810ab`](https://github.com/anphetamina/muzilla/commit/29810ab800f28e8f3c8a6dfed3abca86013e885a))

- **pi**: Agents tweaks
  ([`97631a3`](https://github.com/anphetamina/muzilla/commit/97631a390e385d0de9ead0e5bd4048e986ae6c0a))

- **pi**: Align oracle with upstream harness
  ([`05c7a60`](https://github.com/anphetamina/muzilla/commit/05c7a603f4c9ecc293a0cd0f27d814e93211f724))

- **pi**: Deleted local sandbox settings
  ([`78a77b3`](https://github.com/anphetamina/muzilla/commit/78a77b32d182cff28a25691796f7cf8041f8a5c5))

- **pi**: Enable pi-lens tools for subagents
  ([`c5f86fc`](https://github.com/anphetamina/muzilla/commit/c5f86fcf2c8d4f94f82cead3270b2982885ac1f6))

- **pi**: Enforce orchestrator and subagent tool boundaries
  ([`75ccdff`](https://github.com/anphetamina/muzilla/commit/75ccdfff476ccec449783d58dd6c86b9f135b4af))

- **pi**: Harness tweaks
  ([`99981f9`](https://github.com/anphetamina/muzilla/commit/99981f9922ad49d1e31b8e4ff65973f228a80cef))

- **pi**: Make researcher repository read-only
  ([`209cd48`](https://github.com/anphetamina/muzilla/commit/209cd482caa0f01abec83df7601eb820bde0f176))

- **pi**: Models update
  ([`f6fba95`](https://github.com/anphetamina/muzilla/commit/f6fba9505f7dd099e8081310ffd367677ed36977))

- **pi**: Narrow browser tester skill inheritance
  ([`afb72b8`](https://github.com/anphetamina/muzilla/commit/afb72b8455dff94dc17eb939e5c541760219df41))

- **pi**: Narrow browser tester skill inheritance
  ([`999ff0e`](https://github.com/anphetamina/muzilla/commit/999ff0e9dc4d03ddd19f1d76fb3aa05da7e12553))

- **pi**: Removed unused pi-goal setting
  ([`8e82598`](https://github.com/anphetamina/muzilla/commit/8e825989b43e2a130a0bf6ea7a88a515f0d7f220))

- **pi**: Sandbox settings
  ([`98c9044`](https://github.com/anphetamina/muzilla/commit/98c9044781c4bc18f7c83d6e6c5574c25fc9a0f7))

- **pi**: Sandbox tweaks
  ([`8ceb446`](https://github.com/anphetamina/muzilla/commit/8ceb44640e2e49a14b46858089212630cedb1f54))

- **pi**: Settings extensions fields removed
  ([`17e4408`](https://github.com/anphetamina/muzilla/commit/17e4408900569f44cbb7bcb6e64b344af1d4a5fb))

- **pi**: Settings tweaks
  ([`cc098ba`](https://github.com/anphetamina/muzilla/commit/cc098ba360a48556540cc3c851112027bdf83090))

- **pi**: Settings tweaks
  ([`88aca77`](https://github.com/anphetamina/muzilla/commit/88aca7778a1ebbc64a4175649fbd6cb000f3af7a))

- **pi**: Settings tweaks
  ([`c5eb957`](https://github.com/anphetamina/muzilla/commit/c5eb957397e674956412e499b0e06bfe5df03c33))

- **pi**: Settings tweaks
  ([`581e1af`](https://github.com/anphetamina/muzilla/commit/581e1af0365cdb1712af3a05831b49caa956e150))

- **pi**: Settings tweaks
  ([`3764a4f`](https://github.com/anphetamina/muzilla/commit/3764a4fd05a52ec6a538dc2b483ec9f56e419acd))

- **slice16**: Certify production release gates
  ([`ec31461`](https://github.com/anphetamina/muzilla/commit/ec31461c2aa0d78f77aa300e1bae3c7704325a09))

### Continuous Integration

- **slice16**: Run candidate branch verification
  ([`fcff7b0`](https://github.com/anphetamina/muzilla/commit/fcff7b0ac968b18a99b6d9a3df555e8707ba1bd4))

### Documentation

- Add guided recovery next steps
  ([`739ec44`](https://github.com/anphetamina/muzilla/commit/739ec44392ba801cc640f0131a581af2ef8016af))

- Clarify import-linter enforcement wording
  ([`6796c2e`](https://github.com/anphetamina/muzilla/commit/6796c2ede7c988063b7a5e06de8af326254311e8))

- Clean stale historical prose
  ([`5496e28`](https://github.com/anphetamina/muzilla/commit/5496e2808576d67ad2fc251592671ce5a4c3a4eb))

- Clean stale historical references
  ([`08a58f7`](https://github.com/anphetamina/muzilla/commit/08a58f7c994975914f79537fc39f543f0597947a))

- Clear completed matrix dependencies
  ([`6cb79ab`](https://github.com/anphetamina/muzilla/commit/6cb79abc69c51df46ace99f7d303abf21fdca611))

- Close JOBS-CANCELLATION-001
  ([`2d46e96`](https://github.com/anphetamina/muzilla/commit/2d46e96afb56d3fbae20f83da00d2883ecbdbba3))

- Complete SETTINGS-POLICY-001
  ([`db864e9`](https://github.com/anphetamina/muzilla/commit/db864e95717148585a0cf585a7eb85a1d318f89a))

- Consolidate product contract and completion backlog
  ([`c9c2a3b`](https://github.com/anphetamina/muzilla/commit/c9c2a3be2d295b530b9fe8c64a05fd25ad559956))

- Correct reconciliation follow-up claims
  ([`aee2b4f`](https://github.com/anphetamina/muzilla/commit/aee2b4faceda5c88c1894926d9967085c71475c4))

- Declare production-ready status after release-readiness evidence
  ([`9cfeb7d`](https://github.com/anphetamina/muzilla/commit/9cfeb7de4ccb82bb93f36783190138c50c58c85b))

- Document env vars required for a bare-metal local run
  ([`346c919`](https://github.com/anphetamina/muzilla/commit/346c9192178066948411a72948e53957c93874bf))

- Fix stale readiness claims (status pending audit, changeset surface, manual-matrix ref)
  ([`3b52f2d`](https://github.com/anphetamina/muzilla/commit/3b52f2d05fe73f4225bba0771e7151a0afe7e171))

- Normalize repository completion backlog
  ([`63f1a52`](https://github.com/anphetamina/muzilla/commit/63f1a52cbd05a5f70acb5c7a6f35688cb3e4ac84))

- Reconcile documentation layer with settled review decisions
  ([`cf34633`](https://github.com/anphetamina/muzilla/commit/cf34633a19b2fcd2f178f0a172dd9089f7dca4b4))

- Reconcile product and readiness guidance
  ([`d2d65f8`](https://github.com/anphetamina/muzilla/commit/d2d65f849531692437b77339761270b67fcba3ad))

- Remove SETTINGS-SECRETS-001, PIPELINE-CACHE-001, ART-COVER-SOURCE-001 — all gates green
  ([`b6b31fb`](https://github.com/anphetamina/muzilla/commit/b6b31fbd212d0f9aec0727b661a21234c3234939))

- **PERF-SCALE-001**: Keep row open — final rerun misses cancel threshold
  ([`31f66cb`](https://github.com/anphetamina/muzilla/commit/31f66cb1ff3b035b67201001024ba1a7a5aa2f49))

- **PERF-SCALE-001**: Remove completed row after exact-candidate evidence
  ([`3d87247`](https://github.com/anphetamina/muzilla/commit/3d872479850911cbbea0ec9947b99ffc21663933))

- **PERF-SCALE-001**: Remove completed row after exact-candidate evidence
  ([`57faa61`](https://github.com/anphetamina/muzilla/commit/57faa61613566f07b65ccd16bcd24f1ae06c533a))

- **PERF-SCALE-001**: Remove completed row after exact-candidate evidence
  ([`ebb9a2a`](https://github.com/anphetamina/muzilla/commit/ebb9a2a929270d0b60f22d7590f1a761b42856b1))

- **PERF-SCALE-001**: Restore row — final-candidate run misses matching_p95 by 3.4ms
  ([`7cfd23e`](https://github.com/anphetamina/muzilla/commit/7cfd23ed36ad7a791e9287c4c9267c08be0c89af))

- **pi**: Clarify checkpoint commit ownership
  ([`5d1a3de`](https://github.com/anphetamina/muzilla/commit/5d1a3dec30a5cdd4b2e31c69c10a0038a7019956))

- **pi**: Make goal orchestration single-writer and skill-aware
  ([`dd459ba`](https://github.com/anphetamina/muzilla/commit/dd459ba1f573de213ade6d76fda3e35e77792815))

- **pi**: Restore repo contract and enforce worker-owned edits
  ([`d240729`](https://github.com/anphetamina/muzilla/commit/d240729687b26209679b04a86b11cb9833298224))

- **recovery**: Add audit and execution playbook
  ([`dcbd52a`](https://github.com/anphetamina/muzilla/commit/dcbd52aae21e24b66d462ed344712737adca8634))

- **recovery**: Close manual and URL search slice
  ([`46eec5a`](https://github.com/anphetamina/muzilla/commit/46eec5a3f16210d2b9c6bac3e91985f4cb47e5eb))

- **recovery**: Close replaygain readiness slice
  ([`d10adb2`](https://github.com/anphetamina/muzilla/commit/d10adb2ff1cff6b789945901bd37ee7cdbbb15ca))

- **recovery**: Record matching slice handoff
  ([`1c7caed`](https://github.com/anphetamina/muzilla/commit/1c7caed8e96d737af7606f0fb4c17d3f233b8687))

- **recovery**: Record slice 13 closure
  ([`29f688a`](https://github.com/anphetamina/muzilla/commit/29f688aa3bd7a3569da929955687a84314bfb542))

- **recovery**: Record Slice 7 contracts and handoff
  ([`235ec95`](https://github.com/anphetamina/muzilla/commit/235ec95df1b5dfeaf2061617826bc9131ed5e77d))

- **release**: Record d0824b6 manual browser evidence and fix manifest wording (no threshold change)
  ([`a21df51`](https://github.com/anphetamina/muzilla/commit/a21df5168575bb214755e1ba79bcaac28e85de84))

- **slice16**: Record remote certification evidence
  ([`cf910a1`](https://github.com/anphetamina/muzilla/commit/cf910a1bf8f95dfc0974ed8ea6c7fe09992101ee))

### Features

- Complete rescan matching formats and native acceptance
  ([`6a5236f`](https://github.com/anphetamina/muzilla/commit/6a5236f8d5f80b13d76142f823453e7c223afe25))

- Deterministic cancellation for all job types (JOBS-CANCELLATION-001)
  ([`398d496`](https://github.com/anphetamina/muzilla/commit/398d496ab6a0b9a963e5bdbae31f1e47eb543705))

- ReviewBundle atomic apply (REVIEW-ATOMICITY-001)
  ([`68eae43`](https://github.com/anphetamina/muzilla/commit/68eae43bae2d9f8f99a02f0fef96f66ac7ea0787))

- ReviewBundle-only migration (COMPAT-CHANGESET-001)
  ([`7b8a33c`](https://github.com/anphetamina/muzilla/commit/7b8a33c5337adb578fe28f337645b2fa2e1a8357))

- Whole-bundle drift block and deterministic concurrent conflicts (REVIEW-CONFLICTS-001)
  ([`4bf2a53`](https://github.com/anphetamina/muzilla/commit/4bf2a5332d569b1f28fa71db920ea0fda1f7deda))

- **cache,art**: PIPELINE-CACHE-001 + ART-COVER-SOURCE-001 — versioned cache gateway,
  offline/refresh/stale, remote-only art
  ([`ded9e11`](https://github.com/anphetamina/muzilla/commit/ded9e110bcaeaa387bcaa06e5f4a8fb19c9319bb))

- **catalog**: Add searchable URL-backed facets
  ([`c00ab25`](https://github.com/anphetamina/muzilla/commit/c00ab25927c67d7ac04b4e14c858acd211bdd02a))

- **catalog**: Add track detail review actions
  ([`ad87be1`](https://github.com/anphetamina/muzilla/commit/ad87be1965ea5c710bde7ba1a6b2d066d32ffd08))

- **catalog**: Persist accessible column widths
  ([`cdfeb44`](https://github.com/anphetamina/muzilla/commit/cdfeb449a83b053df7cd517d83a77420b3b9c459))

- **changes**: Add review bundle apply flow
  ([`2b5b126`](https://github.com/anphetamina/muzilla/commit/2b5b12641f64aee1027303017d920b2999e99bb8))

- **frontend**: Add review candidate search
  ([`cb0b848`](https://github.com/anphetamina/muzilla/commit/cb0b848ffadfaf307a76283bbaf126abde5aeee7))

- **frontend**: Scale review inbox navigation
  ([`243f280`](https://github.com/anphetamina/muzilla/commit/243f2808dc7927b6727afad3cf9ea1b7e4351c2d))

- **matching**: Implement matching v2 pipeline
  ([`dccaf6b`](https://github.com/anphetamina/muzilla/commit/dccaf6b2e358292e6893f9e3adae628a257e57fb))

- **matching**: Keep coherent provenance and guarded manual refresh (MATCHING-PROVENANCE-001)
  ([`88a5c62`](https://github.com/anphetamina/muzilla/commit/88a5c62db3aba06332505d1bedbb74fc1fc8ae3c))

- **matching**: Provenance, coherent identity and manual refresh guard (MATCHING-PROVENANCE-001)
  ([`f85cbf9`](https://github.com/anphetamina/muzilla/commit/f85cbf946873d8c59eb331b540d4ecfbc69802bf))

- **matching**: Settle strong/ambiguous/reject/skip contract (MATCHING-DECISION-001)
  ([`437c2f4`](https://github.com/anphetamina/muzilla/commit/437c2f4867ba2e5375ef83a34c96ca2955d5b9ea))

- **review**: Add constrained grouping resolver
  ([`223bf80`](https://github.com/anphetamina/muzilla/commit/223bf8009c84c8435449875e26d9f613d08b9385))

- **review**: Add ReviewBundle foundation and contracts
  ([`41d198e`](https://github.com/anphetamina/muzilla/commit/41d198e0966fb5f2c7f74c63788250b78a332f11))

- **review**: Add unified enrichment proposal flow
  ([`70b042d`](https://github.com/anphetamina/muzilla/commit/70b042dda2d9a8136239f3c8bfe6964606859b0e))

- **review**: Add validated cover candidates API
  ([`e1cda6b`](https://github.com/anphetamina/muzilla/commit/e1cda6b97614fb76a50856e0e160f2ba9157082b))

- **review**: Expose review bundle lifecycle
  ([`763db8b`](https://github.com/anphetamina/muzilla/commit/763db8b9022b2b092f8c24fc593ff9367f700730))

- **review**: Expose ReviewBundle endpoint
  ([`3077c1f`](https://github.com/anphetamina/muzilla/commit/3077c1f0d1d74c57a585f4ee722a93f3c7b02d54))

- **reviews**: Complete bulk and edge UX
  ([`f68402a`](https://github.com/anphetamina/muzilla/commit/f68402add76eaf6a9a66e0fec2c06e9ad3f1a159))

- **reviews**: Persist import bundles and inbox projection
  ([`9d574c4`](https://github.com/anphetamina/muzilla/commit/9d574c45b364ede68d91f811bedfe0b9a0532c9f))

- **sandbox**: Add Docker Pi sandbox (isolated pi, RO .pi, gates)
  ([`d667a1a`](https://github.com/anphetamina/muzilla/commit/d667a1aa37862c37e6780e9f38210804b30cfc25))

- **sandbox**: Make sandbox self-healing e integra (grill Q1-Q10, senza registro)
  ([`7134db2`](https://github.com/anphetamina/muzilla/commit/7134db2ff456d7be502fa061750f7182cf1d79ad))

- **sandbox**: Pi-native global/locale split con helper enforce, guardrails tighten-only e smoke
  globale
  ([`3982701`](https://github.com/anphetamina/muzilla/commit/398270123d12057035fa13fb85e445c79588ab28))

- **sandbox**: Single source manifest and integrity docs (Q11-Q15)
  ([`1090ffc`](https://github.com/anphetamina/muzilla/commit/1090ffcac28385ae94dfeff64a8036a5dee6f21c))

- **search**: Add manual and URL candidate import
  ([`5c58b91`](https://github.com/anphetamina/muzilla/commit/5c58b916275b826e6ece502716b3d35aeca0a2f4))

- **settings**: Add safe catalog and factory reset
  ([`8afac10`](https://github.com/anphetamina/muzilla/commit/8afac102b3dc30dc282f5b8249af56d0d5c617ad))

- **settings**: Expose enrichment and filename policy (SETTINGS-POLICY-001)
  ([`3186b1d`](https://github.com/anphetamina/muzilla/commit/3186b1df796c521c2975c989446ae0f91d8112d3))

- **settings-secrets**: External secret precedence, UI badge, 409 guard, versioned cache stub and
  offline flag
  ([`bdc2f77`](https://github.com/anphetamina/muzilla/commit/bdc2f77e4e332c8e5c604ae3837a76491a8be8d7))

- **slice10**: Complete track review workflow
  ([`b18b3e0`](https://github.com/anphetamina/muzilla/commit/b18b3e09a937fedafb66cc72a95788e098ffc4ca))

- **slice10**: Update catalog and review UI
  ([`12042e5`](https://github.com/anphetamina/muzilla/commit/12042e5005c483ada0572bccc615dc30d06d6682))

- **slice14**: Complete operational review flow
  ([`9abd26c`](https://github.com/anphetamina/muzilla/commit/9abd26cba9658a3b9954415648d46bb6c2160ef0))

- **slice15**: Persist review bundle undo
  ([`9444f65`](https://github.com/anphetamina/muzilla/commit/9444f65fc61bd6172a13cccb8f7cb1e310b1cad3))

- **slice3**: Resolve provider settings and health state
  ([`e10ddef`](https://github.com/anphetamina/muzilla/commit/e10ddef64b2b6c3a39e550f43104a3a54b0b2373))

- **ui**: Add review inbox and detail flow
  ([`f29fdbd`](https://github.com/anphetamina/muzilla/commit/f29fdbda89d1581d2c3a33b6f817186c6f34a01e))

### Performance Improvements

- Freeze 100k threshold manifest for release candidate (targets unchanged)
  ([`be3a2b7`](https://github.com/anphetamina/muzilla/commit/be3a2b7c09b378a4304fba2e0272f1580d71dbc8))

- **PERF-SCALE-001**: Reviewer P1 repairs on exact-image harness
  ([`46327f6`](https://github.com/anphetamina/muzilla/commit/46327f61d8cfc0fc66a3cc7d7789ecb549fe89b1))

- **PERF-SCALE-001**: Strict cancellation harness correction per oracle
  ([`855bac1`](https://github.com/anphetamina/muzilla/commit/855bac1c0c2ed686fba5e91936cd567b5ae00b1b))

### Refactoring

- **groups**: Remove retired workspace routes
  ([`52cebeb`](https://github.com/anphetamina/muzilla/commit/52cebebffe16e5171a0a5e98cc9cc2171dc7d037))

### Testing

- **ci**: Isolate SPA static fixtures
  ([`30e5aeb`](https://github.com/anphetamina/muzilla/commit/30e5aeb84b8f3c0e2948bdae504c2fadd1346290))

- **e2e**: Cover manual and URL candidate journeys
  ([`31d6534`](https://github.com/anphetamina/muzilla/commit/31d65344b366471def9877a3e58f2dd96eee33ed))

- **e2e**: Derace scan-cancel test (in-flight gate, poll-safe cancel assertion)
  ([`5c01f48`](https://github.com/anphetamina/muzilla/commit/5c01f48bd8d2be4d281375c413ec713b9423bd63))

- **e2e**: Reject unrelated import candidates
  ([`ce870df`](https://github.com/anphetamina/muzilla/commit/ce870dfb8c6b606d6251cc570e711b224ead8aad))

- **migrations**: Enforce candidate URL alias constraints
  ([`280b8f2`](https://github.com/anphetamina/muzilla/commit/280b8f23030b53a88e39f53f0ff51f704527a1f7))

- **sandbox**: Cover top-level Mounts and Network/Ipc/UTS host (P2)
  ([`ae96de3`](https://github.com/anphetamina/muzilla/commit/ae96de3d41ed3260c4a0adf65b0e06e738452c68))


## v1.0.0 (2026-07-29)

- Initial Release
