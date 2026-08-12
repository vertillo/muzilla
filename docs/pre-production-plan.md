# Piano di chiusura production di Muzilla

## Decisione

Muzilla è ancora **pre-production**, non staging-ready. Il repository è pulito e i gate attuali sono verdi — backend 1224 pass/6 skip, coverage 91%, frontend 110 test, 37 E2E, OpenAPI allineata, architettura import-linter valida e immagine Docker dell’HEAD verificata — ma la suite non copre correttamente il journey ReviewBundle dichiarato.

Blocker confermati:

- L’import automatico produce `ReviewBundle`, mentre API e pagina import cercano solo `ChangeSet`; un match valido può risultare “Nothing to review”.
- La cancellazione import persiste task `cancelled`, ma schema OpenAPI e frontend non accettano quello stato.
- Se nessun candidato supera la soglia non viene creata una review `needs_attention`, impedendo la ricerca manuale direttamente dal journey import.
- La revisione automatica non conserva identità completa, confidence e spiegazione del candidato.
- Cover, retry delle sezioni, editing tipizzato, esito per file, retry apply e refresh dello stato non sono operativi nella ReviewBundle UI.
- L’undo della ReviewBundle non è esposto: esiste solo un helper interno che genera ChangeSet inversi.
- La ricerca Catalogo passa testo grezzo a FTS5: `AC-DC`, `a:b`, `"` e `OR` causano 500.
- L’inbox carica tutte le review in memoria e la UI rende soltanto la prima pagina.
- Slice 16 chiude il drift gestito di `alembic check` (FTS virtual/shadow e unique legacy)
  e aggiorna React Router a 7.18.2; i gate locali risultano puliti.

Serve una sequenza di cinque slice verticali, non una singola patch.

## Sequenza di implementazione

### Slice 12 — Ricerca Catalogo fail-safe

**Esecutore:** `gpt-5.6-terra`, Medium. Nessuna review separata dopo test e gate mirati.

- Introdurre un encoder FTS5 letterale condiviso: input utente trattato sempre come testo, mai come operatori FTS; trim vuoto equivale a nessun filtro.
- Applicarlo a catalogo e facet search, traducendo eventuali errori DB residui in risposta controllata, mai 500.
- Aggiungere test DB/API/E2E per trattini, doppi apici, due punti, parole riservate, Unicode e input vuoto.
- Chiudere `BUG-CATALOG-001` nella matrice.

### Slice 13 — Import → ReviewBundle, evidenza candidato e inbox scalabile

**Esecutore/review:** `gpt-5.6-terra`, High; una review Terra High.

- Migrazione additiva `0017`:
  - `review_bundles.import_session_id`, FK/index;
  - `proposal_revisions.candidate_snapshot`, `match_explanation` e `confidence`, immutabili e inclusi nel digest;
  - read model `review_inbox_entries` più FTS5, ricostruibile e aggiornato nella stessa transazione di revisioni, task e stato.
- `CandidateSelection` deve conservare provider/ref/tipo, titolo, artista, album, anno, durata, posizione, track count, thumbnail, confidence band, segnali, penalità e motivi di rifiuto. I valori sono calcolati server-side; manual search e URL possono avere confidence nulla ma label esplicita “Selezione manuale”.
- Creare una ReviewBundle stabile per ogni scope importato prima del matching. Zero risultati o provider failure producono `needs_attention` con outcome distinti, non assenza silenziosa.
- Estendere `ImportSessionDetailOut` con `review_bundle_ids`; mantenere `changeset_ids` deprecato per una sola release di compatibilità. Aggiungere `cancelled` a `ImportTaskOut`.
- Portare ImportReview su `/reviews/:id`, mostrando review pronte, in preparazione o da completare.
- Rendere `GET /reviews` una query DB keyset reale e aggiungere `GET /reviews/{id}/neighbors` per previous/next/next-unreviewed oltre la prima pagina. Il frontend usa paginazione “Carica altri”.
- Testare: match valido, candidato rifiutato, zero hit, provider fallito, cancel+GET senza 500, restart, idempotenza e import con oltre 100 review.

### Slice 14 — Review operativa e failure semantics visibili

**Esecutore/review:** `gpt-5.6-terra`, High; una review Terra High. Se fosse necessario modificare writer/journal, fermare quello scope e spostarlo alla Slice 15.

- Visualizzare una CandidateCard completa dal nuovo snapshot, incluse spiegazione e confidence.
- Esporre cover corrente e candidate con `Mantieni`, `Usa`, `Rimuovi` e upload JPEG/PNG tramite gli endpoint validati esistenti.
- Aggiungere `Riprova` soltanto per task terminali retryable; `not_found` offre ricerca/manual input, non retry automatico.
- Aggiungere editing tipizzato di `set_tag` e `write_lyrics`. L’endpoint crea una revisione immutabile successiva, preserva le decisioni sibling e marca accepted l’operazione modificata; per lyrics il server imposta provenance manuale e richiede timestamp validi quando `synced=true`.
- Dopo apply, seguire job e ReviewBundle fino allo stato terminale, mostrare esito per file e operazione, errori sanificati e retry dei soli file falliti. Nessun successo deve essere inferito dal solo enqueue.
- Aggiornare Dashboard per mostrare ReviewBundle e sessioni utente; i ChangeSet restano solo history/compatibilità.
- Coprire cover/upload, retry task, edit tag/lyrics, apply success/partial/failure, refresh/restart e navigazione oltre la prima pagina.

### Slice 15 — Undo persistente della ReviewBundle

**Esecutore/review:** `gpt-5.6-sol`, High; review Sol High obbligatoria. `xhigh` soltanto se failure injection lascia ambiguo un caso di perdita dati o crash recovery.

- Migrazione `0018` con `ReviewUndoRun`: bundle/apply run sorgente, idempotency key, manifest congelato, stato, risultato per file, errore e job associati.
- Aggiungere `POST /reviews/{id}/undo` con `apply_run_id`, `Idempotency-Key`, Origin/CSRF e conferma UI; includere gli undo run nel dettaglio ReviewBundle.
- Riutilizzare `build_undo_changesets_for_run`, applier e journal esistenti. Nessun secondo writer.
- Eseguire gli inversi in ordine side-effect inverso; osservare Cancel solo tra file; retry deve saltare file già ripristinati.
- Fallire chiuso su journal scaduto, file cambiato, collisione, recovery incerta o doppio undo. Partial undo resta esplicito e ritentabile.
- Nuovo E2E primario: import con match valido → ReviewBundle → enrichment → decisioni → apply → verifica tag/path/catalogo → restart → undo → ripristino esatto. Usare solo fixture temporanee; aggiungere failure injection per crash, partial e retry.

Dopo questa slice Muzilla può essere classificato **staging-ready**, ma non ancora production-ready.

### Slice 16 — Gate di release e dichiarazione production

**Esecutore:** Terra High per migrazioni/CI; Luna Medium, o Terra Medium, per aggiornamenti meccanici di lockfile, matrice e documentazione. Una review Terra High del diff funzionale.

- Correggere `alembic check`: escludere soltanto FTS virtual/shadow gestiti esplicitamente e allineare la rappresentazione dei vincoli unique. Il test deve restare capace di rilevare drift reale.
- Aggiornare React Router a 7.18.2, rigenerare lockfile e richiedere audit runtime puliti. La correzione è meccanica; l’advisory riguarda RSC, ma non c’è motivo di distribuire una versione segnalata quando esiste una patch compatibile.
- Rendere CI riusabile da release; la release deve rifiutare un SHA non verificato. Il workflow publish costruisce l’immagine del tag, esegue runtime/Compose smoke sull’immagine esatta e la pubblica solo dopo i gate.
- Verificare clean install e upgrade `0016 → head`, restart durante apply/undo, persistenza config/secret, backup/restore isolato e hash della musica invariato durante reset.
- Mantenere per una release soltanto ChangeSet read/history/undo e bookmark storici; nessun nuovo producer o entry primaria deve usarli.
- Aggiornare [issues-matrix.md](/Users/asant/Desktop/muzilla/docs/issues-matrix.md), recovery guide, README e CONTRIBUTING soltanto dopo tutti i gate. Non creare tag, release o push senza richiesta esplicita.

**Checkpoint verificato (2026-08-12):** i gate deterministici locali hanno superato
`alembic check`, l’audit runtime React Router 7.18.2, l’upgrade `0016 → head`, runtime e
Compose sull’immagine candidata esatta, il backup/restore freddo isolato di `/data` con
checksum e destinazione vuota, e l’intera suite E2E (39/39). Il teardown delle fixture
temporanee è stato corretto. Il workflow di release è riusabile e rifiuta SHA non
verificati, ma non sono ancora stati eseguiti CI remota, tag/release o pubblicazione di
un’immagine, deployment o certificazione production; questi richiedono un’esecuzione
autorizzata successiva.

Il mapping modelli segue sia la policy critica del repository sia la [guida ufficiale OpenAI](https://developers.openai.com/api/docs/guides/latest-model): Terra bilancia capacità e costo, Luna è adatto al lavoro meccanico, mentre Sol resta limitato al nucleo undo/file-recovery.

## Gate e classificazione finale

Production-ready richiede, sullo stesso SHA e sulla stessa immagine candidata:

- backend completo: Ruff, mypy, import-linter, pytest con coverage;
- frontend lint, typecheck, test, build e generate-and-diff OpenAPI;
- E2E dei journey import positivo/negativo, manual/URL, review completa, apply/partial/retry/undo, cancel import, restart, settings/secret e reset;
- Docker runtime, capability e Compose isolato;
- migrazioni clean install/upgrade/downgrade previsto e `alembic check` pulito;
- `pip-audit` e `npm audit --omit=dev` senza finding runtime non accettati;
- nessun test su libreria, `.env`, `data/`, `music/` o secret reali;
- matrice senza S0/S1/S2 aperti sul journey supportato.

Classificazione:

- **Ora:** pre-production.
- **Dopo Slice 15:** staging-ready per test su copie/fixture isolate.
- **Dopo Slice 16, gate locali e una run remota autorizzata:** production-ready per deployment Docker single-user documentato, con backup e reverse proxy/auth configurati.
- Qualunque failure non spiegato su apply, undo, migrazione, secret o reset riporta lo stato a pre-production.

## Follow-up post-produzione

- Resize/persistenza colonne Catalogo e facet ad altissima cardinalità.
- Bulk reject su selezione esplicita e relativo Undo toast.
- Confidence/quality normalizzata per i duplicati.
- Rename interno di `TrackGroup` e cleanup finale dei simboli legacy.
- Policy retention completa in Settings.
- Gestione mutante di `cover.jpg` esterni; nella prima release restano informativi.
- Rimozione definitiva dell’adapter ChangeSet dopo la release di transizione e la scadenza della history necessaria.
