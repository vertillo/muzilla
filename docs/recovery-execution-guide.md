# Recovery execution guide

Guida operativa per proseguire il recovery di Muzilla dopo l'audit del 2026-07-31.
Contiene l'ordine di lavoro, la scelta modello/effort e prompt pronti da usare in Codex.
Il dettaglio architetturale resta in [recovery-plan.md](recovery-plan.md); non duplicarlo
in nuovi file di piano per ogni slice.

## Risposta breve: cosa fare adesso

Il prossimo lavoro è **Slice 1 — Docker e capability readiness**. Deve correggere
`BUG-REPLAYGAIN-001` e aggiungere una verifica nell'immagine che impedisca di distribuire
un container “healthy” con `rsgain` non eseguibile.

Usare:

- **Modello:** `gpt-5.6-sol`;
- **Reasoning effort:** High;
- **Modalità:** una chat dedicata alla sola slice;
- **Prompt:** [Prompt 1](#prompt-1--docker-replaygain-e-readiness).

Non iniziare contemporaneamente ReviewBundle, matching o redesign frontend. La baseline
Docker deve diventare affidabile prima che ReplayGain entri nella pipeline unificata.

## Fonti e metodo di prompting

La raccomandazione modelli è stata verificata il 2026-07-31 con il resolver e il manuale
Codex ufficiali:

- [`gpt-5.6-sol` migration guide](https://developers.openai.com/api/docs/guides/upgrading-to-gpt-5p6-sol)
- [GPT-5.6 Sol prompting guidance](https://developers.openai.com/api/docs/guides/prompt-guidance-gpt-5p6)
- [Codex model selection](https://learn.chatgpt.com/docs/models)
- [Codex prompting](https://learn.chatgpt.com/docs/prompting)
- [`AGENTS.md` instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

Le linee guida ufficiali raccomandano prompt outcome-first con contesto, vincoli, criteri
di successo e verifiche, lasciando al modello la scelta del percorso tecnico. Per questo
i prompt sotto indicano il risultato e i confini, ma non prescrivono patch riga per riga.

Le regole ripetibili sono in `AGENTS.md`. I custom prompt Markdown di Codex sono
deprecati in favore di skill; per questo questi prompt restano un playbook condiviso
nella repo, non vengono installati in `~/.codex/prompts`.

## Scelta del modello

### `gpt-5.6-sol`

Usarlo per lavoro ambiguo, ad alto rischio o con più sottosistemi:

- modello dati e migrazioni;
- concorrenza/cancellazione;
- file operations, recovery e security;
- matching e ranking;
- architettura provider/segreti;
- UX complessa e review finale di una slice critica.

Effort consigliato:

- **High:** implementazione complessa ma con acceptance chiara;
- **Extra High / xhigh:** lifecycle, migrazioni, matching e apply bundle;
- **Max:** solo se una slice critica resta irrisolta dopo una prima analisi High/xhigh o
  per una review finale particolarmente difficile. Non è il default.

### `gpt-5.6-terra`

Usarlo per lavoro ben delimitato e quotidiano:

- test di regressione locali dopo che il contratto è deciso;
- refactor meccanici;
- aggiornamenti di documentazione/matrice;
- componenti frontend con design già specificato;
- cleanup di route e codice legacy;
- triage di failure CI riproducibili.

Effort consigliato:

- **Medium:** default per task chiari;
- **High:** quando il task attraversa backend e frontend o richiede debugging;
- **Low:** solo trasformazioni meccaniche con test deterministici.

### Cosa evitare

- Non scegliere Sol+Max per ogni task: prima migliorare criteri di successo e verifica.
- Non usare Ultra/multi-agent sulle stesse file senza worktree separati. Le slice core
  hanno dipendenze sequenziali e beneficiano di una singola ownership.
- Non cambiare modello a metà slice per “sbloccare” un test senza prima capire il failure.
- Non usare un modello veloce per approvare operazioni file, migrazioni o security senza
  una review Sol.
- Non affidare a un nuovo modello un mega-prompt che ripete audit e piano: indicare i file
  autorevoli e lasciare che li legga.

## Matrice modello per slice

| Slice | Tema | Modello | Effort | Perché |
|---:|---|---|---|---|
| 1 | Docker, ReplayGain, readiness | Sol | High | Native packaging e falso health positivo. |
| 2 | Cancel, retry, provider resilience | Sol | xhigh | Concorrenza, stato persistito e partial semantics. |
| 3 | Settings, secret, provider state | Sol | xhigh | Confine security/config/runtime. |
| 4 | ReviewBundle e contratti API | Sol | xhigh | Migrazione del modello centrale. |
| 5 | Matching v2 | Sol | xhigh | Ranking, provider contract e falsi positivi. |
| 6 | Ricerca manuale e URL | Sol | High | Riuso matching più validazione/SSRF. |
| 7 | Proposal composer/review unificata | Sol | xhigh | Integrazione di più task senza monolite. |
| 8 | Apply bundle e catalog consistency | Sol | xhigh | Massimo rischio sui file e crash recovery. |
| 9 | Shell, Inbox e Review UX | Sol | High | Architettura frontend e qualità visuale. |
| 10 | Catalog, single-track, cleanup dominio | Terra | High | Contratti già stabiliti, lavoro ampio ma concreto. |
| 11 | Reset e release hardening | Sol | xhigh | Operazione distruttiva, auth e acceptance finale. |
| Review di una slice S1 | Audit diff senza modifiche | Sol | High | Controllo indipendente del risultato. |
| Fix meccanico/test/documentazione | Scope ristretto | Terra | Medium | Migliore rapporto velocità/profondità. |

## Come usare i prompt

1. Aprire una nuova chat nella root del repository per ogni slice.
2. Selezionare modello ed effort indicati.
3. Incollare soltanto il prompt della slice, non tutto questo documento.
4. Lasciare che l'agente legga `AGENTS.md` e i documenti citati.
5. Non accettare il risultato se manca una verifica richiesta o se l'issue matrix non è
   aggiornata.
6. Per slice S1 o che modificano file/migrazioni/security, aprire poi una seconda chat con
   il [prompt di review](#prompt-di-review-della-slice).
7. Creare commit solo dopo la review, su richiesta esplicita, con una slice per commit o
   una piccola sequenza di commit verdi quando la migrazione lo richiede.

Restare nella stessa chat finché l'outcome è lo stesso. Aprirne una nuova quando si passa
alla slice successiva; non trascinare tutta la cronologia del progetto in una chat unica.

## Contratto comune di ogni prompt

Ogni slice deve produrre:

- riproduzione o test rosso del problema;
- comportamento atteso e failure semantics;
- modifica minima coerente con l'architettura target;
- test al layer corretto, incluse negative assertion;
- comandi di verifica realmente eseguiti;
- aggiornamento delle righe coinvolte in `docs/issues-matrix.md`;
- elenco file modificati, migrazioni, rischi residui e follow-up;
- nessun commit, push, release o modifica a dati reali salvo richiesta separata.

Se emerge un difetto adiacente, aggiungerlo alla matrice con un nuovo ID e continuare solo
se è indispensabile alla slice. Altrimenti lasciarlo come dipendenza, senza espandere lo
scope.

## Prompt 1 — Docker, ReplayGain e readiness

Modello: **Sol**, effort **High**.

```text
Implementa la Slice 1 del recovery di Muzilla: Docker, ReplayGain e capability readiness.

Prima leggi AGENTS.md, docs/recovery-plan.md (Slice 1), docs/recovery-audit.md e le righe
BUG-REPLAYGAIN-001/TEST-CONTRACT-001 in docs/issues-matrix.md. Verifica nuovamente il
failure nell'immagine corrente, poi aggiungi un test rosso che dimostri che rsgain e le
sue shared library devono funzionare nel runtime distribuito.

Successo significa:
- l'immagine contiene solo le runtime dependency native necessarie e rsgain --version
  funziona davvero;
- la build fallisce se ldd segnala una libreria mancante o il binario non parte;
- liveness e capability/readiness non dichiarano ReplayGain disponibile quando non lo è;
- esiste almeno uno smoke test container/Compose pertinente;
- non vengono indeboliti non-root, cap_drop, no-new-privileges, resource limits o health;
- docs/issues-matrix.md riporta implementazione e test reali.

Mantieni lo scope sulla slice: non iniziare ReviewBundle, matching o redesign. Non usare
la libreria musicale reale e non modificare file utente preesistenti. Esegui le verifiche
pertinenti dentro l'immagine, oltre ai test statici/unitari coinvolti. Non creare commit.

Nel risultato finale riporta root cause, file modificati, comandi/esiti, dimensione o
tradeoff dell'immagine e rischi residui.
```

## Prompt 2 — Cancellazione e resilienza provider

Modello: **Sol**, effort **xhigh**.

```text
Implementa la Slice 2 del recovery: cancellazione cooperativa dei job e resilienza dei
provider, limitandoti a BUG-JOBS-001 e BUG-LYRICS-001.

Leggi AGENTS.md, la Slice 2 in docs/recovery-plan.md, le root cause nell'audit e le righe
della matrice. Parti da test che usano gli handler reali: l'E2E non deve più accettare
succeeded dopo una cancellazione che dovrebbe interrompere il lavoro.

Successo significa:
- pending job cancellato immediatamente e running job in stato visibile cancelling;
- token/checkpoint legge stato persistito senza usare un oggetto ORM obsoleto;
- scan/import/enrichment controllano ai confini definiti e ripuliscono i temporanei;
- non si interrompe a metà il commit atomico di un singolo file;
- 408, 429, 5xx e timeout sono transient con retry bounded/backoff/jitter; 404 è not found;
- outcome per item e Retry failed distinguono assenza da errore;
- i dati parziali seguono la semantica documentata e la matrice viene aggiornata.

Non ridisegnare ancora ReviewBundle e non introdurre un broker esterno. Conserva lease,
JobEvent e worker in-process finché i test non ne dimostrano l'insufficienza. Esegui test
unitari, integrazione ed E2E pertinenti. Non creare commit.
```

## Prompt 3 — Settings, secret e stato provider

Modello: **Sol**, effort **xhigh**.

```text
Implementa la Slice 3 del recovery: configurazione provider effettiva, secret persistenti
e stati provider comprensibili.

Leggi AGENTS.md, la Slice 3 del recovery plan e OPS-SETTINGS-001, OPS-SECRETS-001,
OPS-PROVIDERS-001/002 nella matrice. Riproduci con un test che un token/enabled salvato
oggi non entra nel ProviderSet neppure dopo restart.

Successo significa:
- un resolver unico fonde base config, override non-secret e secret reference;
- Save produce client effettivi con swap/reload atomico oppure uno stato restart-required
  realmente onorato; preferisci live reload se resta semplice e testabile;
- i token non sono nel frontend, log, export ordinario o DB in chiaro;
- il meccanismo secret ha key/authority fuori dal database esportato e permessi stretti;
- gli stati disabled, not configured, checking, operational, temporary unavailable e
  invalid credentials sono distinti, con last checked e test connection;
- restart e reinstallazione supportata preservano la configurazione;
- migration, threat model e test negativi sono documentati nella matrice.

Non modificare bootstrap auth/storage per farli dipendere dal DB. Non stampare secret nei
tool output. Non creare commit.
```

## Prompt 4 — ReviewBundle e contratti tipizzati

Modello: **Sol**, effort **xhigh**.

```text
Implementa la foundation della Slice 4: ReviewBundle, revisioni idempotenti e contratti
Operation tipizzati, senza migrare ancora tutti i flussi.

Leggi AGENTS.md, il modello target in docs/recovery-plan.md e DOMAIN-CHANGES-001,
BUG-CHANGES-001, BUG-REVIEW-001, BUG-APPLY-001, DOMAIN-ART-001 e CONTRACT-API-001.
Definisci prima state machine, invarianti e strategia di migrazione one-way.

Successo significa:
- ReviewBundle ha identità stabile e una sola revisione corrente;
- refresh/selezione identica è idempotente e non crea una nuova inbox row;
- current, proposed e attempted restano distinti;
- Operation è una union discriminata OpenAPI e il frontend usa tipi generati;
- WriteLyrics conserva text/synced e non può diventare [object Object];
- stage art non cambia lo stato corrente; apply con zero accepted non diventa applied;
- l'applier esistente è riusato dietro un adapter temporaneo;
- migrazioni, contract test e compatibilità legacy sono verificati.

Evita dual-write prolungato e non riscrivere l'applier. Non iniziare Matching v2 o la
nuova UI oltre al minimo necessario a provare il contratto. Non creare commit.
```

## Prompt 5 — Matching v2

Modello: **Sol**, effort **xhigh**.

```text
Implementa la Slice 5: Matching v2 dietro gli adapter provider esistenti.

Leggi AGENTS.md, Slice 5, BUG-MATCHING-001, BUG-REVIEW-002, UI-CANDIDATES-001 e
BUG-DEEZER-001. Prima amplia il corpus con casi realistici, incluso Piki - Twilight
Twilight contro Kawai Kawai, stringhe ripetute, parentesi, remix/anthem, metadati
mancanti e durata.

Successo significa:
- parser filename separato con confidence e fallback ai tag;
- query track/release coerenti per provider, incluso title/artist/ISRC quando disponibili;
- retrieve bounded, dedup, hydrate della shortlist e solo poi rank;
- track_count dichiarato, non derivato da una tracklist assente;
- score con segnali/penalità spiegabili e soglia assoluta di rifiuto;
- provider failed è distinto da zero results;
- candidati non correlati non sono proposti soltanto perché primi;
- contract fixture esercitano search summary → hydrate → rank.

Non introdurre ML o dipendenze speculative. Misura il nuovo corpus e riporta casi
ambigui, non soltanto il punteggio medio. Aggiorna la matrice. Non creare commit.
```

## Prompt 6 — Ricerca manuale e candidato da URL

Modello: **Sol**, effort **High**.

```text
Implementa la Slice 6: ricerca manuale multi-provider e candidato da URL dentro una
ReviewBundle, riusando Matching v2.

Leggi AGENTS.md, Slice 6, FEATURE-SEARCH-001/002 e la UX relativa. Definisci una query
comune, capability provider, paginazione, dedup e import idempotente nella review.

Successo significa:
- form/API per titolo, artista, album e campi opzionali con provider selection;
- risultati progressivi distinguono zero hit, non configurato e fallimento;
- URL registry allow-listed riconosce provider/tipo/ID e fa fetch by ID;
- nessun fetch/proxy arbitrario verso l'URL utente e test SSRF negativi;
- URL duplicato seleziona il candidate esistente;
- journey contract/E2E copre query manuale e URL supportati;
- la review mantiene ID e history coerenti.

Non duplicare scoring, cache o adapter. Non aggiungere provider nuovi. Aggiorna la
matrice e non creare commit.
```

## Prompt 7 — Proposal composer e review unificata

Modello: **Sol**, effort **xhigh**.

```text
Implementa la Slice 7: un ProposalComposer che raccolga metadata, rename/path, cover,
lyrics e ReplayGain nella stessa ReviewBundle, mantenendo task tecnici separati.

Leggi AGENTS.md, Slice 7, PRODUCT-PIPELINE-001/002, PRODUCT-REVIEW-001,
PRODUCT-FILENAME-001 e BUG-FILE-001. Parti da contract test dell'aggregazione e dei
fallimenti parziali.

Successo significa:
- un solo bundle/revision contiene operation tipizzate per tutte le sezioni;
- la review è apribile mentre enrichment opzionali sono pending/falliti;
- retry di una sezione non crea nuove review;
- rename usa i tag proposti e default $artist - $title con collision preview;
- cover supporta keep/select/remove e mostra provider/dimensioni;
- lyrics/RG restano analisi non mutanti fino all'apply;
- default automatici rispettano priority/rate/cache e sono configurabili;
- nessun enrichment changeset separato viene esposto nel nuovo flusso.

Non accorpare tutto in un job o una transazione globale. Aggiorna matrice e docs solo se
il contratto target cambia. Non creare commit.
```

## Prompt 8 — Apply bundle e consistenza catalogo

Modello: **Sol**, effort **xhigh**; usare Max solo per una review successiva se restano
failure di recovery non spiegati.

```text
Implementa la Slice 8: apply controllato di una ReviewBundle e consistenza del catalogo.

Leggi AGENTS.md, Slice 8, BUG-FILE-001 e BUG-DASHBOARD-001. Conserva applier, backup,
blobstore e ApplyJournal; estendili soltanto dove serve al manifest del bundle.

Successo significa:
- SourceSnapshot/precondition rileva file cambiati dopo la review;
- per ogni file tag+lyrics+art+RG sono scritti su temporaneo, poi replace e move;
- path containment, collision, case-only rename, fsync, backup e journal restano validi;
- result per file distingue applied, failed, skipped e partial bundle;
- retry non ripete side effect riusciti e recovery dopo crash è deterministico;
- apply→catalog→rescan non produce missing spurio;
- undo e failure injection sono coperti;
- nessuna promessa di atomicità globale fra più file.

Usa solo fixture audio temporanee. Non toccare la libreria reale. Aggiorna la matrice e
non creare commit.
```

## Prompt 9 — Shell, Inbox e Review UX

Modello: **Sol**, effort **High**.

```text
Implementa la Slice 9 del redesign: AppShell, Inbox Revisioni e Review detail sul nuovo
contratto, in incrementi verificabili ma nello stesso outcome.

Leggi AGENTS.md e docs/ux-redesign.md integralmente, oltre alla Slice 9 e agli ID
UI-SIDEBAR-001, UI-SHORTCUTS-001, BUG-NAV-001, BUG-SHORTCUTS-001, UX-CHANGES-* e
UX-REVIEW-*.

Successo significa:
- una sola shell 100dvh con sidebar/drawer e un content scroll owner;
- inbox searchable/filterable con filename/path/cover/confidence/error e riga apribile;
- review file-first, sezioni tipizzate, candidate compare e action bar non sovrapposta;
- returnTo, Back, Close, previous/next/next-unreviewed preservano filtro e posizione;
- J/K spostano focus e viewport e non intercettano editor/modal;
- stati essenziali non dipendono da colore, icona, hover o tooltip;
- desktop/tablet/mobile, zoom 200%, focus e safe-area sono verificati;
- UI legacy resta raggiungibile solo tramite adapter/redirect temporaneo necessario.

Riusa token e primitive esistenti. Renderizza e ispeziona il risultato, esegui Vitest,
typecheck/build e Playwright pertinenti. Non aggiungere decorazione o feature fuori UX
spec. Aggiorna la matrice e non creare commit.
```

## Prompt 10 — Catalogo, singola traccia e cleanup dominio

Modello: **Terra**, effort **High**. Usare Sol High per la review finale se vengono toccati
grouping constraints o file actions.

```text
Implementa la Slice 10: Catalogo usabile, workflow singola traccia e cleanup dei concetti
tecnici nella IA.

Leggi AGENTS.md, Slice 10 e le sezioni Catalogo/Dashboard/Attività in ux-redesign.md.
Copri UI-CATALOG-001, UI-ICONS-001, UX-CATALOG-001/002, PRODUCT-SCAN-001,
DOMAIN-DUPLICATES-001, DOMAIN-GROUPS-001, PRODUCT-GROUPS-001,
PRODUCT-BULK-EDIT-001 e PRODUCT-RETENTION-001.

Successo significa:
- data-grid con header sort, larghezze/overflow leggibili, status testuali e mobile list;
- track detail separa rileggi file, cerca candidate, edit via review e analizza di nuovo;
- Dashboard missing apre gli item identificabili;
- duplicate evidence è esplicita;
- Groups sparisce dalla nav e reassign arbitrario non è più una feature pubblica;
- grouping incerto usa il resolver vincolato della review;
- bulk edit viene rimosso dopo verifica consumer;
- system/retention jobs sono nascosti o spiegati come policy.

Non rimuovere engine grouping o retention interni. Aggiorna route test, redirect, E2E e
matrice. Non creare commit.
```

## Prompt 11 — Reset e hardening finale

Modello: **Sol**, effort **xhigh**.

```text
Implementa la Slice 11: reset sicuro, cleanup legacy e acceptance di release.

Leggi AGENTS.md, Slice 11, SETTINGS-RESET-001, OPS-SECRETS-001 e i criteri globali del
brief riportati nei documenti recovery. Scrivi prima threat model e test distruttivi su
workspace/volume isolati.

Successo significa:
- Reset catalogo/attività preserva settings e secret;
- Factory reset elimina anche config/secret con re-auth, frase forte e scope esplicito;
- worker viene quiesced e nuovi enqueue/apply sono bloccati durante il reset;
- Origin/CSRF, idempotenza e audit sono verificati;
- nessuna operazione elimina o riscrive i file musicali: testare hash/bind isolato;
- cleanup DB/cache/blob rispetta riferimenti e restart/migrazioni;
- adapter, route e UI legacy vengono rimossi solo dopo copertura sostitutiva;
- full static/unit/integration/E2E e Docker Compose restart/persistence passano;
- README, CONTRIBUTING, recovery docs e matrice descrivono il comportamento reale.

Non eseguire il reset su dati utente. Non fare release, tag, push o publish. Riporta ogni
gap non verificabile e non dichiarare completezza soltanto perché i test sono numerosi.
```

## Prompt di review della slice

Modello: **Sol**, effort **High**. Usarlo in una chat separata dopo ogni slice critica.

```text
Esegui una review indipendente delle modifiche non committate della slice appena conclusa.
Non modificare file.

Leggi AGENTS.md, la slice pertinente in docs/recovery-plan.md, gli ID coinvolti in
docs/issues-matrix.md e il diff completo. Verifica il comportamento nel codice, non
fidarti del riepilogo della chat precedente.

Controlla in particolare:
- root cause realmente rimossa e non mascherata;
- requisiti negativi e failure/partial/cancel/restart semantics;
- regressioni, race, idempotenza, migration e security;
- corrispondenza fra API generata e frontend;
- qualità dei test: devono fallire sulla baseline sbagliata e non consolidare il bug;
- comandi dichiarati rispetto a quelli realmente necessari;
- documentazione/matrice coerenti con lo stato effettivo.

Riporta prima i finding ordinati per severità con file e riga. Se non trovi finding,
dillo esplicitamente e indica i rischi residui o le verifiche non eseguite. Non proporre
una nuova architettura fuori dalla slice e non creare commit.
```

## Prompt per correggere finding di review

Modello: **Terra High** per finding locali; **Sol High/xhigh** per race, migration,
security o file operations.

```text
Correggi soltanto i finding confermati della review allegata per la slice corrente.
Leggi AGENTS.md e verifica ogni finding nel codice prima di modificarlo. Aggiungi o
rafforza il test che avrebbe dovuto rilevarlo, applica la correzione minima e riesegui i
check pertinenti. Non espandere lo scope, non riscrivere test verdi senza motivo e non
creare commit. Aggiorna docs/issues-matrix.md solo se stato o rischio cambiano.
```

## Prompt di handoff/commit opzionale

Usarlo solo dopo review pulita, quando si desidera esplicitamente un commit.

```text
Prepara il handoff finale della slice corrente. Controlla git status e il diff, separa le
modifiche della slice da file utente preesistenti, esegui l'ultimo gate pertinente e
aggiorna la matrice se necessario. Poi crea uno o più Conventional Commit piccoli e
coerenti soltanto per i file della slice; non includere modifiche estranee, non fare push,
tag, release o publish. Riporta commit, test e working tree residuo.
```

## Stato e manutenzione del playbook

- La fonte dello stato è `docs/issues-matrix.md`, non questa guida.
- Se una slice viene divisa, modificare qui il prompt e il mapping anziché creare
  `slice-N-plan.md`.
- Se una decisione architetturale cambia, aggiornare prima `recovery-plan.md`, poi prompt
  e matrice.
- Verificare nuovamente i nomi modello nella documentazione ufficiale se questa guida
  viene usata dopo un cambio rilevante di Codex; non sostituire stringhe modello alla
  cieca.
- Quando un prompt produce ripetutamente lo stesso errore, correggere `AGENTS.md` o questo
  playbook con una regola concreta e verificabile.
