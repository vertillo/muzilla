# Recovery execution guide

Guida operativa per proseguire il recovery di Muzilla dopo l'audit del 2026-07-31.
Contiene l'ordine di lavoro, la scelta modello/effort e prompt pronti da usare in Codex.
Il dettaglio architetturale resta in [recovery-plan.md](recovery-plan.md); non duplicarlo
in nuovi file di piano per ogni slice.

## Risposta breve: cosa fare adesso

Il prossimo lavoro è **Slice 2 — Job cancellation e provider resilience**. La baseline
Docker/ReplayGain della Slice 1 è chiusa nella matrice; ora occorre correggere
`BUG-JOBS-001` e `BUG-LYRICS-001` senza riaprire il redesign di ReviewBundle.

Usare:

- **Modello:** `gpt-5.6-terra`;
- **Reasoning effort:** High;
- **Modalità:** una chat dedicata alla sola slice;
- **Prompt:** [Prompt 2](#prompt-2--cancellazione-e-resilienza-provider).

La Slice 2 attraversa concorrenza e stato persistito, ma non richiede di per sé Sol
secondo la policy budget-first. Dopo i gate usare una sola review Terra High del diff
finale. Sol diventa obbligatorio soltanto se l'implementazione si espande a uno dei
[domini critici](#domini-che-richiedono-sol) o se, dopo test deterministici e
decomposizione, resta un'ambiguità critica non risolta.

Non iniziare contemporaneamente ReviewBundle, matching o redesign frontend. La
cancellazione e la resilienza provider devono essere affidabili prima della pipeline
unificata.

## Fonti e metodo di prompting

La raccomandazione modelli è stata verificata il 2026-08-01 con la documentazione Codex
ufficiale:

- [`gpt-5.6-sol` migration guide](https://developers.openai.com/api/docs/guides/upgrading-to-gpt-5p6-sol)
- [GPT-5.6 Sol prompting guidance](https://developers.openai.com/api/docs/guides/prompt-guidance-gpt-5p6)
- [Codex model selection](https://learn.chatgpt.com/docs/models)
- [Codex prompting](https://learn.chatgpt.com/docs/prompting)
- [`AGENTS.md` instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

Le linee guida ufficiali raccomandano di partire dall'effort più basso che produce il
risultato necessario e di aumentarlo solo per lavoro che richiede più pianificazione o
analisi. Raccomandano inoltre prompt outcome-first con contesto, vincoli, criteri di
successo e verifiche. Per questo i prompt sotto indicano risultato e confini, ma non
prescrivono patch riga per riga.

Le regole ripetibili sono in `AGENTS.md`. I custom prompt Markdown di Codex sono
deprecati in favore di skill; per questo questi prompt restano un playbook condiviso
nella repo, non vengono installati in `~/.codex/prompts`.

## Scelta del modello

Questa guida è **budget-first**: spendere capacità di ragionamento dove riduce un rischio
concreto, non in base al numero della slice o alla severità nominale di un finding.

La riproduzione ottenuta usando realmente l'applicazione è l'evidenza primaria per un bug
visibile. Prima di implementare, trasformarla in un test E2E/integration o, se non è
automatizzabile, conservarne passi e risultato atteso come acceptance manuale. L'analisi
del codice serve a trovare la root cause e casi limite; non sostituisce la riproduzione.

### `gpt-5.6-luna`

Usarlo per lavoro chiaro, ripetibile e verificabile automaticamente:

- aggiornamenti meccanici di documentazione e matrice;
- code generation già configurata, formatting e trasformazioni determinate;
- cleanup locali senza decisioni di comportamento;
- esecuzione e riepilogo di gate già noti.

Effort consigliato:

- **Low:** trasformazione puramente meccanica;
- **Medium:** più file ma risultato esatto e test deterministici.

Non usarlo come esecutore principale per debugging, nuovi contratti, concorrenza,
persistenza o modifiche cross-layer. Se Luna non è disponibile nel proprio ambiente,
usare Terra Low/Medium.

### `gpt-5.6-terra`

È l'esecutore predefinito per le slice e per i finding non critici:

- **Medium:** task locale con test rosso, contratto deciso e un solo boundary;
- **High:** debugging, concorrenza, stato persistito, API+frontend o più boundary;
- **Low:** solo in alternativa a Luna per trasformazioni meccaniche.

Terra High può fare nella stessa chat un piano breve e l'implementazione. Non aprire una
chat Sol separata soltanto per produrre un piano se riproduzione, failure semantics e gate
sono già chiari.

### Domini che richiedono Sol

Usare Sol soltanto per il nucleo della modifica che coinvolge almeno uno di questi casi:

1. possibile perdita o corruzione di dati persistenti;
2. scrittura, replace, move, delete, undo o recovery dei file musicali;
3. secret, auth o altro confine security, inclusa validazione SSRF;
4. migrazione non reversibile o compatibilità dati non recuperabile con rollback sicuro;
5. reset o altra operazione distruttiva.

Quando una slice mescola lavoro critico e ordinario, dividerla: usare Sol per il nucleo
critico e Terra per adapter, UI, test aggiuntivi e documentazione. Non allargare
automaticamente Sol all'intera slice.

Effort consigliato:

- **High:** default per implementazione o review del nucleo critico;
- **Extra High / xhigh:** solo se test, contratto e decomposizione non risolvono ancora
  un'ambiguità su recovery, perdita dati o distruttività;
- **Max:** solo se lo stesso blocker critico resta irrisolto dopo un tentativo High/xhigh,
  dichiarando prima quale domanda richiede più ragionamento.

### Tabella decisionale

| Caso reale | Esecutore | Review prima dell'handoff |
|---|---|---|
| Modifica meccanica, docs, matrice, codegen | Luna Low/Medium | Nessuna review separata; bastano i check pertinenti. |
| Bug locale, test rosso, un boundary | Terra Medium | Nessuna review separata se gate e negative assertion passano. |
| Debugging, concorrenza, persistenza, API+frontend o cross-layer | Terra High | Una review Terra High del diff finale. |
| Uno dei cinque domini critici | Sol High sul solo nucleo critico | Una review Sol High obbligatoria del diff finale. |
| Failure critico ancora ambiguo dopo test/decomposizione | Sol xhigh | Una review Sol High; Max non è automatico. |

Un nuovo comportamento production non richiede da solo Sol. Deve ricadere in un dominio
critico; altrimenti usare Terra e compensare con test al boundary corretto.

### Cosa evitare

- Non usare Sol come planner universale: prima migliorare riproduzione, criteri di
  successo, failure semantics e test.
- Non usare Ultra/multi-agent sulle stesse file senza worktree separati. Le slice core
  hanno dipendenze sequenziali e beneficiano di una singola ownership.
- Non aumentare effort per “sbloccare” un test senza prima riprodurre e isolare il failure.
- Non approvare i cinque domini critici senza una review Sol.
- Non affidare a un nuovo modello un mega-prompt che ripete audit e piano: indicare i file
  autorevoli e lasciare che li legga.
- Non chiedere review dell'intera codebase per concludere una slice.

## Matrice modello per slice

| Slice | Esecuzione budget-first | Review | Nota operativa |
|---:|---|---|---|
| 1 | Terra High se viene riaperta | Terra High; Sol solo se cambia hardening/security | Slice chiusa; non rieseguirla senza nuova evidenza. |
| 2 | Terra High | Una Terra High | Dividere cancellation lifecycle e provider retry se il diff cresce. |
| 3 | Sol High per secret/security; Terra High per stato provider/UI | Una Sol High limitata al nucleo security | Il resto passa con i gate; non aggiungere una seconda review. |
| 4 | Sol High per migrazione non reversibile/data integrity; Terra High per adapter e tipi | Una Sol High limitata al nucleo critico; altrimenti una Terra High | Separare schema/state machine dagli adapter. |
| 5 | Terra High | Una Terra High | Corpus ed eval sono il gate; se non converge, dividere il problema. |
| 6 | Terra High per ricerca; Sol High per URL/SSRF | Sol High limitata al boundary URL/security | Tenere parsing allow-listed separato dalla UI. |
| 7 | Terra High | Una Terra High | Integrare per incrementi verdi, poi una sola review finale. |
| 8 | Sol High; xhigh solo per recovery ancora ambiguo | Una Sol High obbligatoria | File mutation, journal e perdita dati sono critici. |
| 9 | Terra High | Una Terra High se cross-layer; altrimenti gate visuali/E2E | Nessuna review Sol per qualità visuale. |
| 10 | Terra High, Medium per cleanup locali | Terra High solo se cross-layer | Sol soltanto se si modifica davvero l'apply path. |
| 11 | Sol High per reset/security/delete; Terra High per cleanup/docs | Sol High limitata al nucleo distruttivo | xhigh solo se il confine distruttivo resta ambiguo. |

## Modalità guidata e uso dei prompt

La modalità predefinita è **guidata**: i prompt completi di questo documento sono contratti
canonici per l'agente, non testo che l'utente deve copiare a ogni passaggio. Dopo avere
letto `AGENTS.md`, l'agente deve consultare le sezioni pertinenti di questa guida, ricavare
scope, modello, effort e gate, quindi proporre o svolgere il prossimo passo autorizzato.

Per avviare o riprendere il lavoro basta normalmente una richiesta breve, per esempio:

```text
Continua il recovery di Muzilla secondo AGENTS.md e la recovery execution guide.
Determina il prossimo passo dallo stato e dal diff; chiedimi solo per ambiguità bloccanti.
```

Una nuova chat non eredita i messaggi della precedente. Se un finding non è ancora
registrato nella matrice o in un commit, il reviewer deve quindi produrre una consegna
concisa con ID, file/riga, effetto e test richiesto. Non deve riproporre l'intero prompt
della slice o chiedere all'utente di ricostruire il contesto.

Ogni risposta finale di implementazione, review, re-review o handoff deve terminare con un
solo blocco **Prossimo passo** che indichi:

- stato corrente: continuare, correggere, re-review, handoff/commit o slice successiva;
- modello esatto e reasoning effort scelti dalle tabelle di questa guida;
- stessa chat o nuova chat, con una motivazione di una riga;
- budget review consumato e residuo per la slice originale;
- una sola istruzione concisa pronta all'uso quando serve una nuova chat; se si resta
  nella stessa chat, basta chiedere conferma per procedere senza far reincollare prompt.

Non chiedere all'utente di scegliere modello, effort o tipo di review quando la risposta è
già determinata da questa guida. Chiedere soltanto quando manca una scelta di prodotto,
una nuova autorizzazione o un fatto non ricavabile da repository, diff e test.

Flusso operativo:

1. Aprire una nuova chat solo quando cambiano slice, ruolo indipendente di reviewer o
   scope di modello; restare nella stessa chat finché outcome, ruolo e scope coincidono.
2. Per una slice mista, separare il nucleo Sol dal lavoro Terra invece di usare Sol per
   tutto. L'agente deve dichiarare quale scope sta assumendo e lasciare invariato l'altro.
3. Usare i prompt completi sotto come checklist interna. L'utente non deve incollarli se
   ha già indicato slice o finding e ha chiesto di procedere secondo la guida.
4. Produrre nella stessa chat un piano breve e poi continuare. Una chat di planning
   separata è ammessa solo per un dominio critico ancora ambiguo.
5. Non accettare il risultato se manca una verifica richiesta o se l'issue matrix non è
   aggiornata.
6. Aprire una chat di review solo nei casi indicati dalla tabella decisionale. La review
   controlla diff, test e boundary direttamente toccati, non l'intera codebase.
7. Correggere insieme i finding confermati e fare al massimo una
   [re-review mirata](#prompt-di-re-review-dei-finding-corretti), nella stessa chat del
   reviewer quando possibile.
8. Creare commit solo dopo una review pulita, su richiesta esplicita, con una slice per
   commit o una piccola sequenza di commit verdi quando la migrazione lo richiede.

### Parità con CI

Un gate locale è valido solo se non dipende da artefatti ignorati o generati da esecuzioni
precedenti. I test che richiedono output di build devono creare fixture deterministiche;
la build reale resta verificata da E2E e Docker. Per il gate backend di handoff usare lo
stesso comando della CI, inclusa la coverage:

```bash
uv run pytest -q --cov=muzilla --cov-report=term-missing
```

Non trascinare tutta la cronologia del progetto in una chat unica. Quando serve una nuova
chat, passare soltanto la consegna concisa generata nel blocco **Prossimo passo**.

### Stop rule del loop di review

Il budget massimo normale è **una review completa e una re-review mirata per slice**.

- Un finding blocca solo se è confermato, appartiene al diff/scope corrente e invalida
  acceptance, failure semantics, sicurezza o integrità dei dati.
- Un difetto preesistente o adiacente va in `docs/issues-matrix.md`; non blocca la slice
  salvo che la renda insicura o non verificabile.
- Un'ipotesi senza percorso riproducibile o evidenza nel codice non è un finding
  bloccante.
- “Review pulita” significa nessun finding bloccante confermato e in scope, non zero
  osservazioni sull'intero repository.
- Se la re-review trova ancora un nuovo blocker in scope, non avviare un'altra review
  generale: la correzione è diventata una nuova sub-slice. Ridurre o separare il diff,
  ripartire dal test rosso e non committare finché il blocker resta aperto.
- La nuova sub-slice non azzera né riapre il budget di review della slice originale. Dopo
  la correzione applicare la tabella decisionale al suo diff reale: test/docs o fix
  meccanici con gate deterministici passano direttamente all'handoff; solo una modifica
  production che ricade nei casi previsti ottiene la propria review limitata.
- Non raccomandare mai un'altra review completa o re-review della slice originale per
  chiudere una sub-slice nata dalla sua re-review.
- Se una correzione apre un nuovo dominio critico, interrompere il loop corrente e
  trattarla come sub-slice critica con Sol; non nasconderla dentro il fix.

### Cosa fare quando cambia la situazione

| Situazione | Azione |
|---|---|
| L'esecutore Terra scopre prima di modificare che serve toccare un dominio critico | Fermare quello scope, descrivere test e boundary, poi aprire una chat Sol soltanto per il nucleo critico. |
| Un gate fallisce in modo riproducibile | Restare nella chat dell'esecutore e fare triage con lo stesso modello; non aprire una review. |
| La riproduzione reale contraddice test o riepilogo del codice | La slice non è chiusa: correggere il test/contratto finché spiega il comportamento reale. |
| La review trova un finding A locale | Correggerlo con Luna/Terra secondo la matrice, eseguire i check e fare una sola re-review mirata. |
| La review trova un finding A in un dominio critico | Correggere soltanto quel nucleo con Sol High e usare la re-review Sol mirata. |
| La review trova un finding B preesistente/adiacente | Registrarlo nella issue matrix e continuare l'handoff, salvo rischio immediato per sicurezza o integrità. |
| La review produce soltanto finding C | Nessuna correzione o re-review; la slice è pulita. |
| La re-review trova un nuovo blocker introdotto dal fix | Non ripetere l'audit: creare una sub-slice dal test rosso, scegliere modello/effort dalla matrice dei finding e dichiarare se il suo diff richiede davvero una review. Un fix test/docs meccanico passa all'handoff dopo i gate. |

## Contratto comune di ogni prompt

Ogni slice deve produrre:

- riproduzione o test rosso del problema;
- comportamento atteso e failure semantics;
- piano breve nella chat con boundary, sequenza, test e classificazione del rischio; non
  un piano riga per riga e non un nuovo file in `docs/`;
- modifica minima coerente con l'architettura target;
- test al layer corretto, incluse negative assertion;
- comandi di verifica realmente eseguiti;
- aggiornamento delle righe coinvolte in `docs/issues-matrix.md`;
- elenco file modificati, migrazioni, rischi residui e follow-up;
- blocco finale **Prossimo passo** nel formato della modalità guidata;
- nessun commit, push, release o modifica a dati reali salvo richiesta separata.

Se emerge un difetto adiacente, aggiungerlo alla matrice con un nuovo ID e continuare solo
se è indispensabile alla slice. Altrimenti lasciarlo come dipendenza, senza espandere lo
scope.

## Prompt 1 — Docker, ReplayGain e readiness

Modello: **Terra**, effort **High**. La slice è chiusa; riaprirla solo con nuova evidenza.
Usare Sol High soltanto se il nuovo diff cambia un confine security/hardening.

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

Modello: **Terra**, effort **High**. Fare una sola review Terra High del diff finale.

```text
Implementa la Slice 2 del recovery: cancellazione cooperativa dei job e resilienza dei
provider, limitandoti a BUG-JOBS-001 e BUG-LYRICS-001.

Lavora in sequenza e mantieni i gate verdi dopo ogni blocco: lifecycle/cancel persistito;
checkpoint e cleanup negli handler reali; classificazione/retry provider e outcome per
item. Non creare documenti di piano separati.

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

Slice mista: usare **Sol High** per secret store, auth/security e migrazione dei secret;
usare **Terra High** per resolver non-secret, reload dei client, stati provider e UI.
Lavorare i due scope in sequenza: prima il nucleo security, poi il resto. Limitare la
review Sol High al nucleo security e usare i gate sul resto senza una seconda review.

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

Slice mista: usare **Sol High** per schema/migrazione non reversibile e invarianti che
possono corrompere stato persistito; usare **Terra High** per adapter, OpenAPI/frontend e
compatibilità già decisa. Limitare la review Sol High al nucleo critico e usare i gate sul
resto senza una seconda review.

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

Modello: **Terra**, effort **High**. Usare corpus, eval e rejection case come gate. Se il
ranking non converge, dividere query/hydrate/scoring e migliorare l'eval invece di
aumentare automaticamente modello o effort.

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

Slice mista: usare prima **Terra High** per ricerca manuale, paginazione, dedup e UI; usare
poi **Sol High** per registry URL, parsing allow-listed e test SSRF. Limitare la review
Sol High al boundary URL/security.

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

Modello: **Terra**, effort **High**. Integrare in incrementi verdi e fare una sola review
Terra High sul diff finale della slice.

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

### Passo 7A — Correggere stato persistito e readiness apply

**Nuova chat. Modello: Sol, effort High.** Questo passo è separato perché corregge i
finding A della review che toccano l'integrità delle decisioni persistite e la readiness
del percorso che conduce all'apply. Non ampliare lo scope a Slice 8 né scrivere file
musicali.

```text
Correggi soltanto i finding A1 e A3 della review della Slice 7.

Leggi AGENTS.md, Slice 7, PRODUCT-PIPELINE-001/002, PRODUCT-REVIEW-001,
PRODUCT-FILENAME-001, BUG-FILE-001 e il report di review. Verifica i finding nel codice
prima di modificare file. Parti da test rossi al boundary corretto.

Finding da correggere:
- quando cover/lyrics/ReplayGain aggiungono una sezione alla stessa ReviewBundle, le
  decisioni gia' prese sulle operation invariate non devono tornare `pending`; conserva
  decisione e proposta o definisci una migrazione esplicita e testata della decisione
  nella revisione successiva;
- al termine del matching le sezioni opzionali devono risultare TaskAttempt `pending`
  nello stesso bundle prima che il worker inizi; completion, failure, cancel e retry
  devono aggiornare quel bundle senza crearne uno nuovo;
- prima dell'apply congela la revisione e restituisce il warning/contratto previsto se
  esistono task pending/running, senza perdere le operation gia' accettate. Non creare
  una transazione globale e non bloccare per sempre apply di task `not_found` o failure
  gia' esplicitamente gestiti.

Copri almeno: decisione accepted -> completion di ciascuna sezione -> decisione
preservata; review immediata dopo match -> pending visibile; retry/cancel/failure;
apply con pending e apply dopo esito terminale. Riesegui i test review/job pertinenti e
i check statici necessari. Non correggere cover upload, codegen frontend o test legacy in
questo passo. Non creare commit.
```

### Passo 7B — Completare il confine security della cover

**Nuova chat, dopo 7A. Modello: Sol, effort High.** L'upload e la validazione di asset
sono un confine security; Sol resta limitato a questo nucleo e non estende lo scope
all'apply della Slice 8.

```text
Correggi soltanto il finding A2 della review della Slice 7, assumendo che 7A sia gia'
completato. Leggi AGENTS.md, Slice 7 e PRODUCT-REVIEW-001. Verifica il finding nel codice
prima di modificare file e parti da test rossi al boundary security corretto.

La cover deve esporre AssetCandidate selezionabili con provider, dimensioni e thumbnail,
mantenere keep/select/remove e fornire upload sicuro con limiti MIME/dimensione. Verifica
ownership/associazione del blob alla review e non accettare input binario o riferimenti
arbitrari che aggirino la validazione. Nessuna scelta, fetch o upload deve scrivere file
musicali prima dell'apply.

Non introdurre un secondo producer o un changeset enrichment visibile nel nuovo flusso.
Esegui i test security/provider/blob pertinenti. Non correggere `metadata_auto`, codegen
frontend o i test handler legacy in questo passo. Non creare commit.
```

### Passo 7C — Configurazione, codegen e test dei producer

**Nuova chat, dopo 7B. Modello: Terra, effort High.** Sono finding cross-layer non
critici; non modificare i nuclei Sol corretti in 7A/7B senza un nuovo test rosso.

```text
Correggi soltanto i finding A4, A5 e A6 della review della Slice 7, assumendo che 7A e 7B
siano gia' completati. Leggi AGENTS.md, Slice 7, PRODUCT-PIPELINE-001/002 e
PRODUCT-FILENAME-001. Verifica ogni finding prima di modificare file e aggiungi test che
falliscono sulla baseline errata.

- `metadata_auto=false` deve disattivare il percorso automatico configurabile senza
  rimuovere la selezione/manual search;
- rigenera `frontend/src/lib/api-types.ts` dall'OpenAPI: ReviewBundleDetail deve includere
  TaskAttempt e le route cover/retry devono essere nel contratto generato; aggiorna gli
  adapter/UI solo dove necessario al typecheck;
- sostituisci i test handler legacy che richiedono ChangeSet con contract test di
  ReviewBundle/TaskAttempt per match, art, lyrics e ReplayGain, inclusi failure parziale,
  cancel e retry. Il gate ristretto deve tornare verde.

Non introdurre un secondo producer o un changeset enrichment visibile nel nuovo flusso.
Mantieni rate limit/cache provider esistenti e le priorita' configurate. Esegui backend,
frontend e codegen gate pertinenti. Non creare commit.
```

### Passo 7D — Re-review mirata

**Stessa chat della review della Slice 7. Modello: Sol, effort High. Budget: una sola
re-review mirata rimanente.** Usare questo prompt soltanto dopo 7A, 7B e 7C; non ripetere
un audit della repository.

```text
Esegui una re-review mirata dei finding A della Slice 7 corretti nei passi 7A, 7B e 7C.
Non modificare file. Leggi il report di review precedente, il diff successivo alle
correzioni e soltanto i test/boundary necessari a verificare A1-A6.

Conferma con evidenza nel codice e test che: decisioni persistono attraverso enrichment;
pending/cancel/failure/retry/apply readiness hanno il contratto previsto; cover e upload
sono non mutanti e validati; metadata_auto funziona; OpenAPI frontend e test handler sono
allineati. Riporta soltanto finding ancora confermati, altrimenti dichiara la slice pronta
per handoff. Non creare commit.
```

### Passo 7E — Handoff

**Nuova chat per handoff/commit. Modello: Luna, effort Medium** (Terra Medium soltanto
se Luna non è disponibile). Procedere soltanto con una re-review 7D pulita e i gate della
Slice 7 verdi. L'handoff verifica e consegna, non corregge comportamento: se un gate
fallisce in modo riproducibile, fermarsi e tornare al workflow dei finding.

La **Slice 8** parte poi in una distinta nuova chat con **Sol High**, perché introduce il
vero nucleo critico di write/move, manifest, journal e recovery. La consegna a Slice 8
deve includere bundle/revision con task terminali, warning readiness e contratti OpenAPI
rigenerati.

## Prompt 8 — Apply bundle e consistenza catalogo

Modello: **Sol**, effort **High**. Passare a xhigh solo se failure injection, test e
decomposizione lasciano ancora ambiguo un caso di perdita dati o crash recovery. Max non
è previsto dal flusso normale.

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

Modello: **Terra**, effort **High**. Fare review Terra High solo se il diff resta
cross-layer; per incrementi frontend locali bastano gate visuali, Vitest e Playwright.

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

Modello: **Terra**, effort **High**; Medium per cleanup locali già coperti. Usare Sol High
soltanto se la slice modifica davvero l'apply path, un confine security o un'operazione
distruttiva, non per route o azioni UI che si limitano a chiamare servizi esistenti.

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

Slice mista: usare prima **Sol High** per reset, delete, auth/security e garanzie di
integrità; usare poi **Terra High** o Luna Medium per cleanup legacy, acceptance
deterministica e docs. Passare a xhigh solo se il confine distruttivo resta ambiguo dopo
threat model e test su volumi isolati. Limitare la review Sol High al nucleo distruttivo.

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

Modello: **Terra High** per modifiche cross-layer non critiche; **Sol High** soltanto se il
diff include uno dei [domini critici](#domini-che-richiedono-sol). Modifiche meccaniche o
locali con test rosso e gate completi non richiedono una review separata.

Fare la review una sola volta, dopo che l'intero diff della slice è pronto e i gate
pertinenti sono passati. Usare una chat separata per l'indipendenza dall'esecutore; non
aprire una nuova chat per ogni fix.

```text
Esegui una review indipendente delle modifiche non committate della slice appena conclusa.
Non modificare file.

Leggi AGENTS.md, la slice pertinente in docs/recovery-plan.md, gli ID coinvolti in
docs/issues-matrix.md, la matrice nella sezione "Prompt per correggere finding di review"
di docs/recovery-execution-guide.md e il diff della slice. Verifica il comportamento nel
codice, non fidarti del riepilogo della chat precedente.

Limita l'ispezione ai file modificati, ai test e ai boundary direttamente necessari a
capire il diff. Non eseguire un audit dell'intera codebase e non cercare una nuova
architettura. Espandi l'ispezione soltanto quando esiste un percorso concreto con cui il
diff corrente può causare una regressione in un consumer adiacente.

Controlla in particolare:
- root cause realmente rimossa e non mascherata;
- requisiti negativi e failure/partial/cancel/restart semantics;
- regressioni, race, idempotenza, migration e security;
- corrispondenza fra API generata e frontend;
- qualità dei test: devono fallire sulla baseline sbagliata e non consolidare il bug;
- comandi dichiarati rispetto a quelli realmente necessari;
- documentazione/matrice coerenti con lo stato effettivo.

Un finding è confermato solo se puoi indicare evidenza nel codice o un percorso
riproducibile e l'effetto osservabile. Classifica ogni rilievo in una delle categorie:
- A — blocker della slice: appartiene ai requisiti/acceptance della slice oppure è
  introdotto dal diff e invalida failure semantics, security o integrità dei dati;
- B — preesistente/adiacente: reale ma fuori dai requisiti della slice e non causato dal
  diff; va nella issue matrix e non blocca, salvo che renda la slice insicura o
  impossibile da verificare;
- C — suggerimento/ipotesi: miglioramento o rischio non dimostrato; non blocca e non
  alimenta il loop di review.

Riporta prima soltanto i finding A ordinati per severità con file e riga, poi gli
eventuali B e C separati. Se non trovi finding A, dichiara la review pulita anche quando
esistono osservazioni B/C, indicando rischi residui o verifiche non eseguite. Non
modificare file e non creare commit.

Per ogni finding A indica anche:
- se è locale oppure cross-boundary;
- se coinvolge uno dei cinque domini critici della guida;
- il modello e reasoning effort consigliati per correggerlo secondo la matrice della
  sezione "Prompt per correggere finding di review";
- il test o gate che ne dimostrerà la correzione.

Concludi con una sola raccomandazione operativa per il prossimo passo. Se esistono più
finding correggibili nello stesso scope, scegli il modello/effort richiesto dal più
rischioso e specifica quali finding A copre. Se non ci sono finding A, scrivi che non
serve una fase di correzione e che il risultato può passare all'handoff/commit. Non
aumentare effort soltanto per la severità. Usa il blocco Prossimo passo della modalità
guidata: indica anche stessa/nuova chat, budget review e una consegna concisa solo se serve
una nuova chat.
```

## Prompt per correggere finding di review

La severità misura l'impatto del difetto, non la difficoltà della correzione. Scegliere
quindi il modello partendo dalla tabella seguente e applicare Sol solo per i cinque domini
critici. Un finding è **locale** quando contratto e failure semantics sono già decisi, la
modifica resta in un singolo boundary e un test deterministico dimostra la correzione.

| Severità finding A | Modello/effort predefinito | Quando cambiare |
|---|---|---|
| S0 | **Terra High** | **Sol High** se tocca un dominio critico; xhigh solo se quel rischio resta ambiguo dopo test e decomposizione. |
| S1 | **Terra High** | **Sol High** se tocca un dominio critico. |
| S2 | **Terra Medium** se locale, altrimenti **Terra High** | **Sol High** soltanto per un dominio critico. |
| S3 | **Terra Medium** | Luna Medium se il fix è puramente meccanico; Terra High se richiede debugging/cross-layer. |
| S4 | **Luna Low/Medium** | Terra Medium se serve una decisione locale; nessuna re-review separata. |

Con più finding, usare il modello/effort richiesto dal finding più rischioso che possa
essere corretto nello stesso scope coerente. Correggere insieme tutti i finding A
compatibili e rieseguire i check pertinenti. Non fare una review completa dopo ogni
correzione. I finding B vanno nella matrice; i C non richiedono una patch.

Una correzione che apre un nuovo dominio critico o cambia sostanzialmente il boundary non
è più un fix della review: interrompere, definirla come sub-slice separata e ripartire da
un test rosso. Non usarla come motivo per ricominciare l'audit della slice originale.

```text
Correggi soltanto i finding A confermati della review allegata per la slice corrente.
Leggi AGENTS.md e verifica ogni finding nel codice prima di modificarlo. Aggiungi o
rafforza il test che avrebbe dovuto rilevarlo, applica la correzione minima e riesegui i
check pertinenti. Non correggere finding B/C, non espandere lo scope, non riscrivere test
verdi senza motivo e non creare commit. Aggiorna docs/issues-matrix.md solo se stato o
rischio cambiano.

Concludi con il blocco Prossimo passo della modalità guidata. Determina dalla tabella
decisionale se il diff corretto richiede la re-review prevista; non chiederlo all'utente.
```

## Prompt di re-review dei finding corretti

Usare lo stesso modello della review iniziale: **Terra High** nel caso ordinario,
**Sol High** per i domini critici. Preferire la stessa chat del reviewer per evitare di
rileggere tutto il contesto; l'indipendenza necessaria è rispetto all'esecutore.

Fare al massimo una re-review mirata per slice. Non ripetere il prompt di review completo.
Se le correzioni hanno cambiato boundary o aperto un dominio critico, chiudere il loop e
trattarle come nuova sub-slice.

```text
Esegui una re-review mirata e read-only delle correzioni applicate ai finding A della
review precedente. Non modificare file e non creare commit.

Leggi AGENTS.md, i finding allegati e il diff corrente. Verifica direttamente nel codice:
- che ogni finding sia realmente corretto;
- che il test aggiunto o rafforzato fallirebbe sul comportamento precedente;
- che la correzione non introduca regressioni o scope creep;
- che documentazione e stato dichiarato restino coerenti.

Concentrati esclusivamente sui finding A precedenti, sugli hunk corretti, sui test
aggiunti e sui consumer direttamente toccati. Non riesaminare l'intera slice o codebase e
non cercare nuovi finding adiacenti. Se emerge incidentalmente un difetto preesistente,
classificalo B e non bloccare; se la correzione ha introdotto un nuovo blocker in scope,
segnala che il diff deve diventare una sub-slice invece di raccomandare un'altra review.

Riporta lo stato di ogni finding A precedente. Se sono tutti corretti e non è stato
introdotto un nuovo blocker, dichiara la review pulita, indica i check verificati e
autorizza il passaggio all'handoff/commit. Non fidarti del riepilogo dell'esecutore.

Concludi con il blocco Prossimo passo della modalità guidata. Se resta o nasce un blocker,
indica severità, scope locale/cross-boundary, dominio critico, modello/effort, stessa o
nuova chat e il test rosso richiesto. Trattalo come sub-slice senza raccomandare un'altra
review della slice originale. Se la sub-slice è soltanto test/docs o meccanica, specifica
che dopo i gate passa direttamente all'handoff senza un'altra review.
```

## Prompt di handoff/commit opzionale

Modello: **`gpt-5.6-luna`**, effort **Medium**; usare Terra Medium se Luna non è
disponibile. Usare Terra High soltanto per il triage di un gate riproducibile; se emerge
una correzione di comportamento, interrompere l'handoff e tornare al workflow dei finding
invece di correggerla durante il commit.

Usarlo solo dopo review pulita, quando si desidera esplicitamente un commit.

```text
Prepara il handoff finale della slice corrente. Controlla git status e il diff, separa le
modifiche della slice da file utente preesistenti, esegui l'ultimo gate pertinente e
aggiorna la matrice se necessario. Poi crea uno o più Conventional Commit piccoli e
coerenti soltanto per i file della slice; non includere modifiche estranee, non fare push,
tag, release o publish. Riporta commit, test e working tree residuo.
```

## Handoff Slice 16 — gate verificati, non ancora pubblicazione

Slice 16 ha verificato localmente i gate di release sullo stesso codice: `alembic check`
ora ignora soltanto gli oggetti FTS virtual/shadow gestiti manualmente e mantiene il
controllo del drift reale; l'audit runtime di React Router è pulito con la versione
7.18.2; il flusso release riusabile rifiuta SHA non verificati e costruisce/smoke-testa
l'immagine candidata esatta prima di qualunque publish. Sono inoltre verificati l'upgrade
`0016 → head`, il runtime/Compose sull'immagine esatta e il backup/restore freddo isolato
del volume `/data` con checksum, destinazione vuota e musica/config esterne escluse.

La suite E2E completa è 40/40; il teardown delle fixture temporanee è stato corretto.
La CI remota autorizzata ha completato sul commit candidato con backend, frontend, E2E e
Docker/Compose/backup-restore verdi. Questo non equivale a un tag/release o immagine
pubblicati né a un deployment: restano necessari l'approvazione esplicita dell'operatore
prima di qualunque pubblicazione.

Runbook operativo conciso: fermare il container, archiviare `/data` su storage esterno con
SHA-256, verificare il checksum, ripristinare soltanto in un volume nuovo e vuoto, avviare
l'immagine esattamente validata e verificare la readiness. `/music`, `.env`, config e
segreti forniti fuori da `/data` non fanno parte dell'archivio e richiedono backup separati.

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
- Se l'utente deve chiedere quale modello, effort, chat o prompt usare dopo un risultato,
  il blocco **Prossimo passo** era incompleto: correggere il playbook o la sua applicazione,
  non aggiungere un altro giro di review.
