# Review Muzilla: verifiche Docker del 4 ottobre 2026

Appendice al [rapporto completo e piano](code-review-2026-10-04.md).
È evidenza del candidato, non una certificazione permanente della repository.

## Risultato

Build, native tools, smoke Compose e cold backup/restore passano. Il benchmark
esistente su 100.000 file dichiara PASS senza soglie fallite; la misura dei
massimi ha il limite R29. La prova aggiuntiva fallisce su entrambi i reset quando
esiste una review: HTTP 500, retry 500, manutenzione persistente e startup exit 3
(R30/P1). Il candidato non è approvabile per produzione.

L’audit Python dell’immagine non segnala vulnerabilità note. L’immagine contiene
30 versioni diverse dal lockfile: R25 è ora dimostrato. R26 riguarda il lockfile
con urllib3 2.7.0; questa immagine contiene 2.8.0.

## Candidato e ambiente

| Identità | Valore |
| --- | --- |
| Commit | `e70065df8eacf8f4de90ff862e070469599144a5` |
| Tree | `d857a259d0002ba09ab83f372a6a2f4d5bc830ba` |
| SHA-256 contesto `git archive` | `3dedb30fc368b64b55e8da8c5e442c7b3c0e746daeee73e8bb5c9065db9c0d65` |
| Immagine immutabile usata | `sha256:785e3a1eb31195d7474622e5a13362ee42a3cea99057c3d4d4dbf31154639061` |
| Tag locale della build | `muzilla:review-e70065df8eac-74772` |
| Build | exit 0, 23.57 s |
| Benchmark | 2026-10-04 15:28:26–15:35:02 UTC; processo incl. preparazione 458,73 s |
| Host | Apple M1 Pro/T6000, 10 core, 32 GiB; Darwin 25.6.0 arm64, SSD APFS |
| Docker Engine / Compose | 29.6.2 / 5.3.1 |
| VM Docker Desktop | Linux 6.12.76-linuxkit aarch64, 10 CPU, circa 23,43 GiB |
| Limiti Compose | 2 CPU, memoria 2 GiB, swap limit 2 GiB |
| Dataset | 100.000, seed 0; libreria generata e provider mock |

Il benchmark verifica tree pulito, label revision/build-context e ID del container
corrispondente all’immagine. Le label non sono una firma del contenuto: la verifica
vale per questa build eseguita e identificata. La documentazione di review non
modifica il codice del candidato e non viene inclusa nell’immagine.

## Isolamento e metodo

Clone locale pulito del commit sotto `/private/tmp`; nessuna copia di `.env`,
`data/`, librerie musicali o segreti personali. Nuovi volumi, progetti Compose
univoci e bind temporanei. MusicBrainz nel benchmark è il mock locale; tutti gli
altri provider sono disabilitati. Negli smoke e nella prova browser aggiuntiva sono
disabilitati tutti i provider. Token e password sono valori fittizi per il test.

La build usa il Dockerfile candidato. Gli unittest container sono eseguiti
direttamente. Un wrapper esterno aggiunge soltanto la configurazione provider
deterministica agli smoke Compose; gli assert restano invariati. Il wrapper del
benchmark riutilizza la build appena creata dopo averne verificato ID e label:
nessuna modifica di dataset, workflow o soglie. Serve a usare una sola immagine
immutabile per tutti i gate senza ripetere la build.

Il socket Docker richiede esecuzione fuori dal sandbox: i primi tentativi limitati
non sono difetti del prodotto. Il harness browser esterno è stato corretto per
applicare il negativo CSRF a Settings, endpoint sensibile, e rileggere la porta
casuale dopo restart. Ogni run usa fixture nuove; nessun assert del prodotto è
stato indebolito. Nessun fix del prodotto è stato eseguito.

## Comandi e artefatti

Build nel clone pulito:

```bash
docker build --progress plain -f docker/Dockerfile \
  -t muzilla:review-e70065df8eac-74772 \
  --label org.opencontainers.image.revision=e70065df8eacf8f4de90ff862e070469599144a5 \
  --label muzilla.source-tree=d857a259d0002ba09ab83f372a6a2f4d5bc830ba \
  --label muzilla.build-context=3dedb30fc368b64b55e8da8c5e442c7b3c0e746daeee73e8bb5c9065db9c0d65 .
```

Entry point effettivamente esercitati:

```text
MUZILLA_TEST_IMAGE=<ID> python -m unittest -v tests.container.test_runtime
MUZILLA_TEST_IMAGE=<ID> python -m unittest -v tests.container.test_build_guard
tests/container/compose_smoke.py: main() tramite run_smokes.py
tests/container/backup_restore_smoke.py: main() tramite run_smokes.py
scripts/perf_benchmark.py --count 100000 --seed 0 --out <scratch>/perf
                         --image-tag <ID>, tramite run_perf.py
uvx pip-audit --no-deps --disable-pip -r <inventario-image.txt>
             --format json -o <scratch>/logs/runtime-audit.json
run_browser_image.py + browser_image.cjs: flow aggiuntivo autenticato
```

I wrapper sono temporanei esterni, non comandi di prodotto. Non interpretare gli
entry point come eseguiti senza override. Per ripetere gli smoke aggiungere al
Compose temporaneo `MUZILLA_PROVIDERS__<PROVIDER>__ENABLED=false` per MUSICBRAINZ,
DISCOGS, ITUNES, DEEZER, SPOTIFY, ACOUSTID. Il benchmark ha il suo mock/overlay.

Log, wrapper e fixture integrali sono sotto `/private/tmp/muzilla-docker-review-fatkaf2q`. Lo scratch può essere rimosso
dal sistema; questa appendice conserva identità, esiti, misure, confronto versioni,
limiti e riproduzione dei finding anche senza quei file.

## Native tools, Compose e backup

| Prova | Risultato verificato |
| --- | --- |
| `tests.container.test_runtime` | 2 test PASS, 0,755 s; librerie rsgain e analisi come UID 1000 |
| `tests.container.test_build_guard` | 1 test PASS; build senza TagLib fallisce con `libtag.so.2 => not found` |
| Compose smoke | PASS, 15,77 s; ready/capabilities, UID 1000, limiti, cap_drop ALL, no-new-privileges, musica read-only |
| Persistenza Compose | Settings/token dopo recreate; catalog reset conserva entrambi e checksum musica |
| Backup smoke | PASS, 18,39 s; stop clean exit 0, archivio /data e SHA-256 esterni, volume destinazione nuovo |
| Negativi restore | Checksum corrotto e volume non vuoto rifiutati; hash destinazione/sorgente/musica invariati |
| Restore positivo | Manifest file/bytes identico prima dell’avvio; stessa immagine, catalogo e token dopo recreate |
| Tool nel benchmark | 20/20 fpcalc in 2,1 s; rsgain exit 0, 10 righe in 0,4 s |

Il solo fpcalc dello smoke è debole: fixture silenziosa e helper con `check=False`
che ignora stderr/exit. La prova funzionale usata per dichiarare fpcalc operativo
è quella del benchmark su audio valido di 30 s nella stessa immagine.

Il backup smoke contiene catalogo minimo e Settings/segreto; non crea review/journal
complessi. Restore parziale e avvio deliberato di immagine sbagliata non sono stati
simulati. L’uguaglianza dell’ID positivo non sostituisce questi negativi di readiness.

## Benchmark: risultati e soglie

Manifest originale `benchmark/thresholds.json`, versione 1.0.0, riferimento
`3b52f2d05fe73f4225bba0771e7151a0afe7e171`, SHA-256 `c17aeb1c923f7ee67bb4905ad4a04842239ce93272619027be694e00b8848257`.
Nessuna soglia è stata cambiata dopo la misurazione.

| Metrica | Soglia originale | Valore registrato | Esito |
| --- | --- | --- | --- |
| `muzilla_rss_mib_peak` | <= 2048 | 1169.4 | PASS harness; limite R29 |
| `sqlite_connections_max` | <= 20 | 7 | PASS harness; limite R29 |
| `fd_count_max` | <= 1024 | 21 | PASS harness; limite R29 |
| `cold_scan_throughput_tracks_per_sec` | >= 30 | 803.1 | PASS |
| `incremental_scan_p95_ms` | <= 5000 | 1475.1 | PASS |
| `catalog_search_p95_ms` | <= 800 | 54.6 | PASS |
| `catalog_filters_p95_ms` | <= 800 | 183.4 | PASS |
| `grouping_duration_s` | <= 120 | 52.01 | PASS |
| `matching_p95_ms` | <= 2000 | 1001.1 | PASS |
| `review_generation_p95_ms` | <= 5000 | 257.9 | PASS |
| `review_navigation_p95_ms` | <= 500 | 3.5 | PASS |
| `apply_p95_ms_per_bundle` | <= 10000 | 1249.7 | PASS |
| `undo_p95_ms` | <= 10000 | 948.1 | PASS |
| `cancel_detection_ms` | <= 2000 | 144.7 | PASS |
| `api_error_rate` | == 0 | 0 | PASS |
| `playwright_apply_undo_success` | == 1 | 1 | PASS |

Cold scan 124,51 s, grouping 52,01 s; catalog list p95 69,1 ms e facets 184,1 ms.
30 campioni di matching/review generation/incremental scan/cancel; 30 bundle
Apply e 30 Undo di 10 tracce. Cancellazione: 30 risposte 200, ciascuno stato finale
cancelled, massimo 144,7 ms. Matching soltanto sul mock MusicBrainz. Incremental
scan su singolo file con mtime modificato, non rescan totale. Nessun 5xx nel
workflow benchmark. Playwright applica e ripristina dalla SPA un ulteriore bundle
di 10 tracce; stato applied/undone e titoli per file verificati.

Dataset con album/singoli, tag poveri/incoerenti, multidisc, compilazioni,
Unicode/case, art e titoli ambigui. 1.971 MP3 decodificabili di circa 30 s a
128 kb/s; gli altri derivano dalla fixture corta. Non dimostra budget su file
multi-GiB, NAS/HDD o librerie interamente lossless/tracce lunghe, né correttezza
sui negativi di drift/recovery/concorrenza.

`peak_rss_mib` deriva da `docker stats MemUsage`, non da RSS processo diretto;
viene pubblicato prima della prova browser finale senza aggiornamento successivo.
FD/SQLite sono campioni singoli. Il harness restituisce `threshold_pass=true`
e `threshold_failures=[]`; il gate completo dei **picchi** resta da rendere
probante (R29). Limiti reali 2 GiB/2 CPU; nessun OOM osservato. Non chiamare 7 FD
SQLite un massimo misurato di connessioni.

## API distribuita e audit

OpenAPI immagine/sorgente: schemi e route API identici. Il confronto JSON grezzo
ha una sola differenza: route SPA `/{full_path}` nell’immagine con frontend
compilato, assente nel clone senza dist. Nessuna divergenza del contratto dati
consumato dalla UI. La SPA distribuita è stata usata nelle prove browser.

Inventario `importlib.metadata`: 54 distribuzioni. Audit delle 52 dipendenze
fissate dall’inventario, esclusi progetto e tooling pip/setuptools: exit 0,
`No known vulnerabilities found`. È audit Python, non delle CVE di ogni pacchetto
Debian/native library. Audit npm runtime iniziale verde. Non certificano assenza
di vulnerabilità ignote.

Le 30 versioni differenti dal lockfile:

| Pacchetto | uv.lock | Immagine |
| --- | --- | --- |
| Mako | 1.3.12 | 1.4.3 |
| MarkupSafe | 3.0.3 | 3.0.4 |
| Pygments | 2.20.0 | 2.21.0 |
| RapidFuzz | 3.14.5 | 3.14.6 |
| SQLAlchemy | 2.0.51 | 2.1.3 |
| alembic | 1.18.5 | 1.20.0 |
| annotated-doc | 0.0.4 | 0.0.5 |
| anyio | 4.14.2 | 4.15.1 |
| argon2-cffi-bindings | 25.1.0 | 26.1.0 |
| cffi | 2.1.0 | 2.1.1 |
| charset-normalizer | 3.4.9 | 3.5.2 |
| click | 8.4.2 | 8.5.0 |
| fastapi | 0.140.1 | 0.142.2 |
| idna | 3.18 | 3.20 |
| msgpack | 1.2.1 | 1.2.3 |
| numpy | 2.5.1 | 2.5.3 |
| pydantic | 2.13.4 | 2.13.5 |
| pydantic-settings | 2.14.2 | 2.15.0 |
| pydantic_core | 2.46.4 | 2.46.5 |
| python-dotenv | 1.2.2 | 1.2.4 |
| rich | 14.3.4 | 15.0.0 |
| scipy | 1.18.0 | 1.18.1 |
| starlette | 1.3.1 | 1.7.0 |
| typer | 0.27.0 | 0.27.2 |
| typing-inspection | 0.4.2 | 0.4.4 |
| urllib3 | 2.7.0 | 2.8.0 |
| uvicorn | 0.51.0 | 0.54.0 |
| uvloop | 0.22.1 | 0.23.0 |
| watchfiles | 1.2.0 | 1.3.0 |
| websockets | 16.1.1 | 17.2 |

Lockfile con advisory e immagine attuale corretta per urllib3 sono esiti distinti.
R25/R26 richiedono installazione congelata, aggiornamento deliberato del lock e
audit sullo stesso inventario: pip può cambiare nuovamente runtime sullo stesso SHA.

## Prova autenticata e riproduzione dei reset

Su librerie/volumi nuovi per ciascuno scope:

1. Avviare l’immagine con provider disabilitati e autenticazione fittizia; login SPA.
2. Verificare GET tracks anonimo 401 e PUT Settings senza CSRF/origin corretto 403.
   Scansionare `/music` con un MP3 temporaneo e ID3 iniziale.
3. Salvare token Discogs gestito disabilitato. Creare una review manuale di titolo,
   accettare operazioni; Apply solo tramite bottone UI e conferma. Verificare
   risultato per-file applied.
4. Restart, rileggere la porta, verificare sessione/review/token. Undo tramite UI
   e conferma; run/per-file undone, titolo catalogo/file e path originale.
5. Registrare SHA-256 del file e inviare uno dei payload seguenti con cookie valido,
   Origin corretto, CSRF corrente e Idempotency-Key.

```json
{"scope":"factory","confirmation":"FACTORY RESET MUZILLA","password":"<password fittizia corrente>"}
```

```json
{"scope":"catalog_and_activity","confirmation":"RESET CATALOG AND ACTIVITY"}
```

Endpoint rispettivamente `/api/settings/reset/factory` e `/api/settings/reset/catalog`.

6. Retry stessa chiave, tentare nuova review, restart e ispezione stato/log container.

| Controllo | Factory | Catalog |
| --- | --- | --- |
| Auth/CSRF/origin, scan, Apply UI | PASS | PASS |
| Restart con sessione/review/segreto | PASS | PASS |
| Undo UI, titolo catalogo/file e path | PASS | PASS |
| Primo reset con review | 500 | 500 |
| Retry stessa chiave | 500 | 500 |
| Nuova mutazione dopo errore | 503 | 503 |
| SHA-256 musicale durante reset fallito | invariato | invariato |
| Startup dopo errore | exited, exit 3 | exited, exit 3 |
| Eccezioni JavaScript pagina | nessuna | nessuna |

Traceback della stessa immagine, entrambi gli scope:

```text
api/routers/settings.py:308 → reset_service.execute_prepared_reset(...)
services/reset.py:391 → session.execute(delete(model))
sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError)
review bundle cannot delete its current revision
[SQL: DELETE FROM proposal_revisions]
```

Il trigger della migrazione 0011 impedisce cancellazione della revisione corrente
se il bundle non è preparing; reset cancella revisione prima del bundle. Lock già
persistito e worker quiescente; retry e startup ripetono la stessa sequenza.
È una causa del candidato, non del mock. Lo smoke senza review non copre il caso.
Nessun fix o cancellazione manuale delle righe per ottenere un PASS.

## Integrità degli artefatti originali

Hash dei file originali nello scratch; questo Markdown non li modifica:

| Artefatto | SHA-256 |
| --- | --- |
| `perf_result.json` | `b8c27ac9a2c928a6a5780f448ad39789625c6a3155f711cd1bff02119cb93f31` |
| `latencies.ndjson` | `71ab5e5501642b330c99306315a163a2ebf772d31962c0a5f32850a85d0b09b8` |
| `thresholds.json` | `c17aeb1c923f7ee67bb4905ad4a04842239ce93272619027be694e00b8848257` |
| `Dockerfile` | `eb0e0d66c64f5f272667df615e48250e0cf4c23fbeddda1797e4bdb5eaad485b` |
| `docker-compose.yml` | `475d814af8a8340c512e6ec36027a9dbe1d394a7ad56137d50a580303e582534` |
| `docker-compose.perf.yml` | `833f8a761dd2bb32d7bdf365643fc6f90267cc53fcff1e901694a7a59ef60571` |
| `perf_benchmark.py` | `1f833c5979154767f6ca02ed057aa6f451369fb805a8b3879003fe092e294e36` |
| `gen_perf_library.py` | `d85efe58dbe1faa0a68869df27b1b04cd23a27c3113da7bb8d79ff527c5a91c3` |
| `compose.log` | `508a17083c61128b56d22e797856ef8a04f7c962ce7cef7a3d1922c0faa6cde3` |

## Limiti residui e decisione

R01–R28 aperti; R25 rafforzato, R26 precisato, R29/P2 e R30/P1 aggiunti al piano.
Il candidato non supera la readiness complessiva.

Non si dichiarano test di power loss reale, Linux x86_64, NAS/HDD, crash ad ogni
checkpoint, restore parziale/immagine errata, audit CVE completo del sistema
operativo o upgrade post-release mai esercitati. I gate generici iniziali non sono
ripetuti: il codice resta uguale; i gate runtime sono sulla nuova immagine.
I reset hanno riproduzioni applicative e richiedono regressioni su DB migrato.

Creati solo rapporto e appendice sotto docs. Nessun fix, aggiornamento normativo
matrix/spec/readiness, commit/push/deployment. I cleanup hanno eliminato container,
reti e volumi Compose della review. Immagine e scratch restano consultabili;
nessun pruning globale o modifica di risorse personali.
