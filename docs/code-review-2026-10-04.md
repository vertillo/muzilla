# Review completa di Muzilla e piano di intervento

Data della review: 4 ottobre 2026. Candidato esaminato:
`e70065df8eacf8f4de90ff862e070469599144a5`.

## Esito e perimetro

Il candidato non è approvabile per il rilascio in produzione. La review iniziale
ha individuato 28 finding. Il riesame Docker aggiunge R29 e R30: il totale è
**30 finding: 13 P1, 16 P2 e 1 P3**. I problemi più importanti
riguardano scritture fuori dalla libreria, perdita di modifiche esterne,
atomicità di Apply/Undo e concorrenza dei worker. Nessun finding P0 è stato
accertato; questo non certifica l'assenza di altre vulnerabilità o difetti.

Il perimetro è l'intero progetto, non un diff. Fonti normative, lette nell'ordine
previsto da AGENTS.md: [product-spec.md](product-spec.md),
[completion-matrix.md](completion-matrix.md),
[production-readiness.md](production-readiness.md), [README](../README.md),
implementazione e test. Sono stati considerati semplicità, correttezza,
integrità dei dati, recovery, migrazioni, sicurezza, concorrenza, performance,
operatività, contratti API, UI, navigazione e accessibilità.

La review usa la skill `review-agent`; per la review UI è stata utilizzata anche
la skill di progetto `ui-ux-pro-max`. La richiesta dell'utente che accompagnava la
review iniziale autorizzava la persistenza di questo rapporto sotto docs e il suo
aggiornamento dopo i gate Docker; in quel perimetro le verifiche non autorizzavano
fix del prodotto, deployment, commit o push. Nessuna musica, configurazione,
credenziale, backup o dato personale è stato usato come fixture.

Questo documento conserva il rapporto del candidato originale ed è ora il backlog
attivo per i goal di remediation: R01–R30 identificano i finding originari e R31 è il
residuo successivo; gli ID sono unità di accettazione/rendicontazione, non pacchetti
obbligatoriamente separati. I goal con ID espliciti restano limitati a quegli ID; per
`/goal Risolvi i prossimi finding`, la selezione autonoma è limitata a due pacchetti e
tre ID totali, congelati prima dell'implementazione, secondo [AGENTS.md](../AGENTS.md).
Raggruppare solo finding con una causa, un confine contrattuale o una acceptance comune;
conservare la mappa criterio→evidenza per ciascuno e non riempire slot dopo chiusure o blocchi.
La completion matrix precedente è stata evasa e non va ripopolata con questi finding.
Il rapporto non sostituisce le specifiche normative o i gate di
[production-readiness.md](production-readiness.md).

## Classificazione

| Priorità | Significato |
| --- | --- |
| P0 | Blocco universale del rilascio o guasto critico. |
| P1 | Correzione urgente, bloccante per il rilascio. |
| P2 | Difetto da correggere prima di dichiarare soddisfatto il relativo requisito. |
| P3 | Problema di minore impatto, comunque concreto e correggibile. |

La priorità di un finding non coincide necessariamente con il CVSS di una
dipendenza o con il rischio S1/S2/S3 della completion matrix. Sono inclusi
difetti dimostrati da riproduzioni o dal percorso effettivo del codice; non
semplici preferenze stilistiche. I riferimenti di riga appartengono al candidato
indicato e possono spostarsi dopo modifiche future.

## Stato dei finding

Nella review iniziale tutti i finding partivano aperti. Lo stato aggiornato sotto
chiude esclusivamente gli ID con acceptance, gate di consegna e review indipendente
documentati sul candidato esatto. Conservare le descrizioni e le evidenze originali:
descrivono il candidato storico, non lo stato dei fix successivi. Per ogni chiusura,
l'evidenza identifica revisione verificata, criteri di accettazione, comandi/esiti,
reviewer, browser se applicabile e rischio residuo; non basta cambiare lo stato nella
tabella.

Nel lavoro dei pacchetti si distinguono queste descrizioni dell'evidenza, senza
assegnarle automaticamente come stati della tabella:

- **Implementato:** la correzione circoscritta esiste a contenuto identificato; i
  criteri residui restano espliciti. Non significa acceptance, prerequisito risolto,
  chiusura o consegna.
- **Verificato/accettato internamente (`INTERNALLY_VERIFIED`):** tutti i criteri assegnati
  hanno evidenza mappata, verifiche d'integrazione/failure applicabili, review indipendente
  e approvazione parent. È un endpoint terminale di successo solo se il proprietario ha
  autorizzato esplicitamente un task internal-only prima del lavoro; altrimenti è progresso,
  non chiusura. Un impedimento di consegna si riporta `BLOCKED`, conservando l'eventuale prova
  interna. `no push`/`no commit` non cambiano automaticamente l'endpoint.
- **Chiuso/consegnato (`CLOSED`):** acceptance e review sono complete, i gate locali applicabili
  e la CI richiesta su tutti i job dello SHA esatto sono passati, ed esiste la ricevuta della
  consegna autorizzata. Solo questo stato giustifica una chiusura non provvisoria.

`BLOCKED` e `NEEDS_OWNER_DECISION` descrivono rispettivamente un impedimento concreto dopo
alternative sicure o una decisione materiale richiesta; non alterano lo stato della tabella.
Le definizioni non cambiano alcuna riga o stato corrente, compresa la chiusura provvisoria già
registrata, e non riqualificano retroattivamente le prove storiche.

| ID | Stato | Evidenza / riferimento |
| --- | --- | --- |
| R01 | Chiuso | [Evidenza R01](#evidenza-r01) |
| R02 | Chiuso | [Evidenza R02](#evidenza-r02) |
| R03 | Chiuso | [Evidenza R03](#evidenza-r03) |
| R04 | Chiuso | [Evidenza R04](#evidenza-r04) |
| R05 | Chiuso | [Evidenza R05](#evidenza-r05) |
| R06 | Chiuso | [Evidenza R06](#evidenza-r06) |
| R07 | Chiuso | [Evidenza R07](#evidenza-r07) |
| R08 | Chiuso | [Evidenza R08](#evidenza-r08) |
| R09 | Chiuso | [Evidenza R09](#evidenza-r09) |
| R10 | Chiuso | [Evidenza R10](#evidenza-r10) |
| R11 | Chiuso | [Evidenza R11](#evidenza-r11) |
| R12 | Chiuso | [Evidenza R12](#evidenza-r12) |
| R13 | Chiuso | [Verifica interna R13](#verifica-interna-r13) |
| R14 | Chiuso | [Evidenza R14](#evidenza-r14) |
| R15 | Chiuso | [Evidenza R15](#evidenza-r15) |
| R16 | Chiuso | [Verifica interna R16](#verifica-interna-r16) |
| R17 | Chiuso | [Evidenza R17](#evidenza-r17) |
| R18 | Chiuso | [Evidenza R18](#evidenza-r18) |
| R19 | Chiuso | [Evidenza R19](#evidenza-r19) |
| R20 | Chiuso | [R20](#r20-p2-separa-scadenza-di-freschezza-e-cancellazione-della-cache) |
| R21 | Chiuso | [R21](#r21--p2-elimina-la-ricerca-quadratica-dei-membri-duplicati) |
| R22 | Chiuso | [Evidenza R22](#evidenza-r22) |
| R23 | Chiuso | [Evidenza R23](#evidenza-r23) |
| R24 | Chiuso | [Verifica interna R24](#verifica-interna-r24) |
| R25 | Chiuso | [Evidenza R25](#evidenza-r25) |
| R26 | Chiuso | [Evidenza R26](#evidenza-r26) |
| R27 | Chiuso | [Evidenza R27](#evidenza-r27) |
| R28 | Aperto | — |
| R29 | Verificato (delivery/CI pendenti) | [Aggiornamento R29](#r29--p2-misura-realmente-i-massimi-dichiarati-dal-benchmark) |
| R30 | Chiuso | [Evidenza R30](#evidenza-r30) |
| R31 | Chiuso (provvisorio) | [Evidenza R31](#evidenza-r31) |

## Finding e interventi

### R01 — [P1] Crea i temporanei senza seguire symlink

**Riferimento:** [changes/writer.py](../src/muzilla/changes/writer.py), riga 387.

Il nome temporaneo è prevedibile: `<file>.muzilla.tmp`. `shutil.copy2()` segue
un eventuale symlink già presente. Un Apply riprodotto ha modificato un file
sentinella esterno alla libreria, sostituito il file musicale con il symlink
e restituito `applied`.

**Intervento:** temporanei esclusivi nella directory verificata, protetti da
symlink e sostituzioni concorrenti. Apply, rollback e Undo devono condividere
questa protezione; il cleanup non deve eliminare file estranei.

**Accettazione:** precreare un symlink verso una sentinella esterna e verificare
che sentinella, sorgente e catalogo restino invariati. Aggiungere una sostituzione
concorrente durante la scrittura. Nessuna scrittura fuori dal contenimento.

### R02 — [P1] Conserva lo stato recuperabile degli errori dopo la sostituzione

**Riferimento:** [changes/writer.py](../src/muzilla/changes/writer.py), riga 451.

Un errore dopo `os.replace()` viene registrato come journal `failed`, anche se
il file è già cambiato. Il rollback considera i journal `done` e può ignorare
la mutazione. Iniettando un errore nella rilettura dopo replace si è ottenuto:
file modificato, catalogo precedente, risultato `failed`,
`recovery_required=False`.

**Intervento:** distinguere errori precedenti alla mutazione, successivi e
incerti. Una sostituzione già avvenuta deve essere riconciliata, ripristinata
oppure esposta come recovery obbligatoria.

**Accettazione:** iniettare errori dopo replace, fsync, rilettura e checkpoint
DB. Ripetere da un nuovo processo: nessuna mutazione può essere nascosta dietro
un semplice fallimento senza recovery.

### R03 — [P1] Verifica il file reale prima di Undo e rollback

**Riferimenti:** [changes/bundle_undo.py](../src/muzilla/changes/bundle_undo.py),
riga 229; [changes/bundle_applier.py](../src/muzilla/changes/bundle_applier.py),
riga 664.

Undo confronta l'hash del journal con quello nel database, senza rileggere il
file. Il rollback ripristina senza verificare il drift corrente. Entrambi i
casi sono stati riprodotti: una modifica esterna dopo Apply viene sovrascritta
da Undo; una modifica esterna durante un bundle viene sovrascritta dal rollback.

**Intervento:** verifica comune di identità, dati correnti, contenimento e
symlink negli antenati immediatamente prima del ripristino. Drift o identità
incerta devono bloccare la scrittura e produrre recovery esplicita.

**Accettazione:** conservare integralmente modifiche esterne anche senza rescan;
provare cambi di inode/percorso, file mancanti e antenati sostituiti con symlink.

### R04 — [P1] Rendi Undo riprendibile dai checkpoint già completati

**Riferimento:** [changes/bundle_undo.py](../src/muzilla/changes/bundle_undo.py),
riga 337.

La cancellazione lascia alcuni file ripristinati e altri applicati, con
`recovery_required=False`. Il retry verifica anche i journal `rolled_back`
contro lo stato dopo Apply e fallisce sui file già ripristinati. Riproduzione
con due tracce: un file restored, un file applied; retry bloccato da drift sul
file restored. L'esecuzione include nuovamente i journal `rolled_back`.

**Intervento:** congelare gli effetti inversi nel manifest, persistere checkpoint
per file, saltare quelli verificati come già ripristinati. Cancellare fra file
completi, non fra tag e spostamento dello stesso file. Riconciliare anche gli
Undo interrotti durante startup.

**Accettazione:** cancellazione e crash a ogni confine, poi retry e restart.
Il bundle deve convergere oppure restare in recovery esplicita. Nessuna falsa
atomicità o riesecuzione di effetti già verificati.

### R05 — [P1] Aspetta realmente la terminazione dei worker prima del reset

**Riferimenti:** [jobs/queue.py](../src/muzilla/jobs/queue.py), riga 365;
[services/reset.py](../src/muzilla/services/reset.py), riga 275;
[tests/conftest.py](../tests/conftest.py), riga 97.

`recover_stuck_jobs()` considera recuperabile qualsiasi job `cancelling`, anche
con lease futura e worker attivo. Durante reset, `workers_are_quiescent()`
diventa vero troppo presto. Un worker esterno con lease valida per un'ora è
stato dichiarato terminale senza confermare la cancellazione. Due test dedicati
alle lease esterne sono disabilitati in conftest.

**Intervento:** separare richiesta di cancellazione e acknowledgement. Aspettare
terminazione confermata o recovery sicura della lease scaduta. Riattivare i
test `test_api_quiesce_recovers_expired_external_lease_but_waits_for_active_lease`
e `test_reset_waits_for_external_leases_to_be_terminal_before_database_or_storage_delete`.

**Accettazione:** DB, blob e credenziali non sono cancellati mentre un worker
può ancora usarli. Verificare lease attiva, scaduta e worker che conferma lo stop.

### R06 — [P1] Acquisisci i job con una transizione atomica

**Riferimento:** [jobs/queue.py](../src/muzilla/jobs/queue.py), riga 129.

SELECT seguito da UPDATE senza condizione sullo stato precedente non garantisce
l'acquisizione esclusiva. La serializzazione delle scritture SQLite non impedisce
a due sessioni di selezionare lo stesso job. Due worker sincronizzati dopo la
SELECT hanno entrambi acquisito il job 1.

**Intervento:** transizione condizionale atomica con controllo dell'acquisizione;
heartbeat e completamento devono verificare il proprietario della lease.

**Accettazione:** interleaving deterministico tra sessioni/processi indipendenti;
una sola acquisizione, un solo handler, nessun effetto duplicato. Un proprietario
obsoleto non deve rinnovare o completare la lease di un altro worker.

### R07 — [P1] Non dichiarare terminato un job mentre il thread continua

**Riferimento:** [jobs/worker.py](../src/muzilla/jobs/worker.py), riga 125;
[jobs/handlers/scan.py](../src/muzilla/jobs/handlers/scan.py), riga 34.

Il timeout di `asyncio.wait_for()` cancella l'attesa di `asyncio.to_thread()`,
non il thread. Alcuni handler passano la stessa sessione SQLAlchemy al thread.
La riproduzione ha registrato `failed` mentre il thread era ancora attivo:
può continuare dopo la chiusura della sessione o mentre parte un reset.

**Intervento:** sessione e risorse appartengono al contesto che le usa. Su timeout,
richiedere arresto cooperativo e aspettare un checkpoint sicuro prima dello stato
terminale e prima di liberare risorse o lease.

**Accettazione:** test con barriere su timeout, quiesce e shutdown. Dopo la
terminazione dichiarata non resta attività capace di mutare stato; nessuna
sessione è usata contemporaneamente da supervisore e thread.

### R08 — [P1] Sposta Apply e Undo fuori dall'event loop HTTP

**Riferimento:** [jobs/handlers/apply.py](../src/muzilla/jobs/handlers/apply.py),
riga 51.

Gli handler async eseguono direttamente il writer sincrono. Copie, mutagen e
fsync bloccano HTTP, heartbeat e cancellazione. Con scrittura simulata di 200 ms,
un timer da 10 ms è stato eseguito dopo circa 212 ms.

**Intervento:** contesto dedicato per l'intera operazione sincrona, sessione
propria e supervisione coerente con R07. Non basta aggiungere un singolo yield
prima dell'operazione.

**Accettazione:** su file realistici, health, letture, heartbeat e richiesta di
cancellazione restano responsivi durante Apply/Undo; cancellazione osservata
solo ai checkpoint sicuri.

### R09 — [P1] Recupera le lease che scadono dopo il riavvio

**Riferimento:** [jobs/queue.py](../src/muzilla/jobs/queue.py), riga 351.

Recovery eseguita all'avvio, non nel polling ordinario. Un riavvio prima della
scadenza lascia il job `running`; alla scadenza non viene più recuperato. La
riproduzione ha lasciato il job bloccato e non acquisibile dal nuovo worker.

**Intervento:** riconciliazione periodica sicura, coordinata con proprietà delle
lease e recovery dei file; non reclamare un lavoro realmente vivo.

**Accettazione:** restart prima della scadenza, poi avanzamento del tempo. Il job
converge senza duplicare effetti e senza richiedere un secondo restart.

### R10 — [P1] Proteggi dalla retention i journal necessari a Undo

**Riferimento:** [pipeline/retention.py](../src/muzilla/pipeline/retention.py),
riga 58.

La sweep protegge ApplyRun attivi o in recovery, non ReviewUndoRun. Un journal
sorgente di Undo con `recovery_required=True` è stato eliminato appena oltre
il limite di età, rimuovendo l'evidenza necessaria al recupero.

**Intervento:** proteggere journal e blob degli Undo pending, in esecuzione o in
recovery; liberare la protezione solo a riconciliazione definitiva.

**Accettazione:** testare soglie temporali e numeriche, restart e sweep concorrente.
L'Undo deve mantenere tutti i dati inversi necessari finché può doverli usare.

### R11 — [P1] Conserva integralmente le immagini incorporate da ripristinare

**Riferimento:** [changes/writer.py](../src/muzilla/changes/writer.py), riga 263.

Il journal cattura solo la prima immagine e non tutti i suoi attributi. Un MP3
con fronte e retro, dopo rimozione e Undo `undone`, conteneva una sola immagine
frontale senza descrizione.

**Intervento:** conservare collezione, bytes, ordine e attributi del formato.
Una cattura dell'inverso fallita deve bloccare Apply prima della mutazione.

**Accettazione:** immagini multiple ID3, FLAC, MP4 e Ogg; confronto prima/dopo
Undo e rollback, comprendente tipo e descrizione. Iniettare errori di cattura.

### R12 — [P1] Rendi durevoli i blob prima di riferirli dal journal

**Riferimento:** [changes/blobstore.py](../src/muzilla/changes/blobstore.py), riga 64.

`BlobStore.put()` scrive e rinomina senza fsync di file e directory. Il journal
SQLite può diventare durevole prima dei bytes necessari al ripristino. Evidenza
strutturale del percorso di persistenza: non è stata simulata una perdita di
alimentazione.

**Intervento:** rendere durevole il blob prima del checkpoint che lo rende
necessario alla recovery; verificare integrità e disponibilità dei blob riusati.

**Accettazione:** ordine dei checkpoint; errori di write, fsync e rename;
nessuna mutazione con inverso non disponibile o non verificato.

### R30 — [P1] Rendi i reset compatibili con le revisioni correnti

**Riferimenti:** [services/reset.py](../src/muzilla/services/reset.py), righe 110 e 391;
[migrazione 0011](../migrations/versions/0011_review_bundle_foundation.py), riga 447.

`_DELETE_ORDER` elimina ProposalRevision prima di ReviewBundle. Il trigger
`proposal_revisions_current_required_delete` vieta di eliminare la revisione
corrente finché il bundle esistente non è `preparing`. Nell’immagine candidata,
autenticazione → scan → review manuale → Apply UI → restart → Undo UI sono
riusciti. Entrambi i reset, su fixture nuove contenenti quella review, falliscono
con HTTP 500 e `sqlite3.IntegrityError: review bundle cannot delete its current revision`.
Retry con la stessa chiave: ancora 500; nuova mutazione: 503; riavvio: container
`exited`, codice 3, stesso errore durante startup recovery. I checksum musicali
restano invariati. Lo smoke precedente passa perché non crea review: non copre
questo normale stato persistente dell’applicazione.

**Intervento:** usare una strategia di cancellazione amministrativa coerente con
trigger, FK, journal e revisioni, nella transazione di reset. Gestire il fallimento
DB e il riavvio senza lasciare una manutenzione irrisolvibile. Preservare i vincoli
delle review ordinarie, la quiesce e tutte le protezioni del reset.

**Accettazione:** entrambi gli scope su DB migrato con review ready/applied/undone,
revisioni successive, journal e run; verificare risultato, dati eliminati o preservati,
segreti, audit, revoca sessioni dove prevista e checksum musicali. Fault injection
durante la cancellazione, retry idempotente e nuovo processo devono convergere senza
500 ripetuto o startup bloccato. Nessuna disattivazione permanente dei vincoli.

### R13 — [P2] Leggi il payload dello snapshot ORM nel preflight

**Riferimenti:** [services/review_apply.py](../src/muzilla/services/review_apply.py),
riga 64; [changes/bundle_applier.py](../src/muzilla/changes/bundle_applier.py),
riga 577.

`source_snapshot` è una relazione ORM, ma il servizio richiede un dict e ritorna
subito: il preflight sincrono è inattivo. File modificato esternamente, preflight
eseguito, nessun conflitto. L'interpretazione errata compare anche nel controllo
dei bundle concorrenti dell'applier.

**Intervento:** leggere il payload del modello corretto tramite un solo lettore
tipizzato, evitando adattamenti dict/object che nascondono gli errori.

**Accettazione:** sorgente obsoleta o review incompatibile sullo stesso file
producono il conflitto API previsto, senza enqueue né scritture.

### R14 — [P2] Preserva il commit riuscito durante una cancellazione tardiva

**Riferimento:** [jobs/queue.py](../src/muzilla/jobs/queue.py), riga 162.

Il supervisore riconosce `applied`/`undone` come già committati, ma
`mark_succeeded()` li trasforma in `cancelled` se trova una richiesta tardiva.
Riproduzione: anche il risultato è perduto, `result=None`.

**Intervento:** transizione terminale del job coerente con l'operazione;
preservare risultato, eventi e diagnostica del commit realmente avvenuto.

**Accettazione:** cancellazione tra commit e completamento del job; API, eventi
e Activity riportano l'esito effettivo, senza falsa cancellazione del commit.

### R15 — [P2] Autentica la revoca globale effettuata dal logout

**Riferimento:** [api/routers/auth.py](../src/muzilla/api/routers/auth.py), riga 89.

Logout non richiede auth o Origin/CSRF ma incrementa l'epoch globale. Una
richiesta anonima con Origin estraneo ha restituito 200 e revocato una sessione
autenticata: un visitatore può forzare disconnessioni ripetute.

**Intervento:** autenticazione e protezioni delle mutazioni sensibili per la
revoca globale; pulizia locale del cookie sicura e idempotente.

**Accettazione:** richieste anonime/cross-origin non invalidano altre sessioni;
logout valido continua a revocare anche il cookie precedentemente catturato.

### R16 — [P2] Inizializza i tag assenti nei formati supportati

**Riferimento:** [tags/writer.py](../src/muzilla/tags/writer.py), riga 131.

Il writer riconosce MP3/WAV/AIFF tramite ID3 già presente. File validi privi di
tag sono leggibili ma la scrittura fallisce con `unsupported format`; riprodotto
per tutti e tre i formati. È un caso centrale per librerie con metadata poveri.

**Intervento:** riconoscere il contenitore e creare il blocco tag mancante senza
alterare l'audio; preservare il rifiuto dei formati realmente non supportati.

**Accettazione:** round-trip, Apply e Undo su fixture inizialmente senza tag,
con verifica dell'audio e degli esiti per file.

### R17 — [P2] Applica le soglie di rifiuto configurate

**Riferimenti:** [matching/engine.py](../src/muzilla/matching/engine.py), riga 94;
[matching/candidates.py](../src/muzilla/matching/candidates.py), riga 577.

`reject` non determina la decisione; il ranking usa una soglia fissa. Distanza
0.3, strong 0.1 e reject 0.2 producono `ambiguous`. Le impostazioni reject non
hanno l'effetto dichiarato.

**Intervento:** classificazione con soglia effettiva coerente per album,
singleton e scelta manuale.

**Accettazione:** valori sotto/sopra/esattamente sulle soglie e impostazioni
persistite; rejected sempre nascosto, non selezionabile e non forzabile.

### R18 — [P2] Limita la preferenza del provider ai candidati equivalenti

**Riferimento:** [matching/candidates.py](../src/muzilla/matching/candidates.py),
riga 560.

La penalità può far vincere un candidato fuori dalla zona di equivalenza:
distanza 0 penalizzata a 0.04, superata da 0.035 con `min_gap=0.03`.

**Intervento:** separare qualità del match e ordinamento dei candidati equivalenti.

**Accettazione:** confini della zona, gap zero, provider non presenti nell'ordine
e corroborazione. Un match materialmente peggiore non vince solo per la sorgente.

**Avanzamento (2026-10-23; consegnato e chiuso).** `adjusted_distance` rappresenta la
qualità dopo corroborazione; la preferenza provider incide solo sull'ordine entro `min_gap`
dalla migliore qualità, senza cambiare rifiuto/confidenza né promuovere candidati fuori zona.
La review iniziale Sol Medium aveva trovato la post-sort singleton che annullava l'ordine
provider e lasciava indici `is_duplicate_of` obsoleti. Le due regressioni d'integrazione
(qualità in-zona non identica e indici duplicati) erano RED prima del fix; la review mirata
Sol Medium ha approvato senza blocker. Test e gate finali PASS; dettagli e ricevute in
[Evidenza R18](#evidenza-r18).

**Stato:** R18 è `CLOSED` sul commit del codice e sulla CI esatta in [Evidenza R18](#evidenza-r18).
La CI sullo SHA del follow-up documentale corrente sarà verificata prima di iniziare R20;
R19 è chiuso e R20 non è ancora iniziato.

### R19 — [P2] Restituisci lo stesso contratto dalla cache e dalla rete

**Riferimento:** [providers/cache.py](../src/muzilla/providers/cache.py), riga 398.

`cached_get_track()` restituisce ReleaseCandidate dalla rete e dict dalla cache;
import URL usa `.source` e `.ref`. Confermato il cambio di tipo tra prima e
seconda chiamata: riuso in altra review o offline può fallire.

**Intervento:** codec del contratto provider, preservando rappresentante e provenance.

**Accettazione:** cache fredda/calda/stale, offline e restart; import dello stesso
candidato in review differenti con risultato equivalente.

**Avanzamento (2026-10-23; consegna completata).** Il decoder condiviso rifiuta identità e
campi normalizzati malformati senza fabbricare ref o convertire valori; i due gateway
trattano payload inutilizzabili come cache miss. La regressione di import Deezer in review
distinte e una matrice di 12 casi negativi sono state riprodotte prima della correzione.
La prima review indipendente Sol High ha bloccato P2 per DTO non tipizzati/ref fabbricati;
la seconda review mirata Sol High ha dato **OK with notes**, senza blocker R19.

Verifiche sul candidato locale basato su `2e7cb4221f89bb0e67d70c5dd49e1a0536dc418`:
`ruff check src tests`, `mypy src`, `lint-imports`, diagnostica LSP (0 finding),
`git diff --check`, suite provider mirata (29 pass), suite backend completa (1603 pass,
9 skip) ed E2E completa (74 pass): PASS. La regressione API copre import equivalenti in
review distinte con una sola chiamata provider. Build frontend PASS; nessuna modifica
frontend, OpenAPI, schema DB o dipendenze.

**Stato:** R19 è `CLOSED` sul commit del codice e sulla CI esatta in [Evidenza R19](#evidenza-r19). Il follow-up documentale `a4f73aff7c9923d211d4a758c0c76bf7efd2c219` è pushato; GitHub run `39954879643` ha lo stesso `head_sha` e tutti e quattro i job obbligatori completati con successo. R18 è chiuso in [Evidenza R18](#evidenza-r18); R20 non è ancora iniziato e attende la CI di questo follow-up.

### R20 — [P2] Separa scadenza di freschezza e cancellazione della cache

**Riferimento:** [pipeline/retention.py](../src/muzilla/pipeline/retention.py), riga 80.

La sweep elimina tutte le entry scadute, anche utilizzabili come stale. Un payload
leggibile da `cache_get_stale()` è scomparso dopo sweep. Avvio e manutenzione
possono quindi eliminare il fallback durante indisponibilità del provider.

**Intervento:** politiche distinte per freschezza e conservazione massima, con
limiti espliciti di crescita.

**Accettazione:** offline e provider indisponibile dopo sweep/restart; dato
conservato utilizzabile, provenance stale visibile e crescita limitata.

**Decisioni owner (2026-10-23):** la cache semantica conserva il fallback stale per 30 giorni
oltre `expires_at`, senza cap per numero di righe o nuovo controllo UI. `retention.enabled=false`
disabilita solo il pruning dei journal Undo: la pulizia cache continua con l'intervallo esistente.
La UI chiarisce questa distinzione. L'implementazione R20 è iniziata solo dopo R18 CLOSED sullo
SHA `7c8b9a2f3303804548689f7fca1da3ed0e81d802` e CI esatta `39985802710`.

**Verifica iniziale pre-review (2026-10-23):** backend completo (ruff, mypy, import-linter, 1615 passed /
9 skipped), frontend completo (lint, typecheck, 168 test, build) ed E2E Chromium (75 test) PASS sul
candidato precedente ai correttivi richiesti dalla review pass 1; questi gate completi vanno rieseguiti
sul candidato finale. Nessuna migrazione, OpenAPI o dipendenza.

**Preflight runtime (2026-10-23):** reviewer Sol High, fresh, background e read-only; il sandbox ha
negato il canary sintetico fuori dai read roots (`Downloads`). Per decisione owner, il controllo
Guardrails `noAccess` non configurato nel runtime effettivo è N/A, non un gate né una prova di protezione;
nessuna policy è stata ripristinata e nessun modulo attivato. La review R20 può procedere riusando questo
preflight; nessun codice è stato letto durante i due preflight.

**Reviewer pass 1 (2026-10-23): BLOCKED.** Ha rilevato la race sweep-first/cache-refresh e la perdita
di provenance stale quando solo l'hydration usa fallback stale. Correttivi R20: `cache_put()` riserva
ora il writer SQLite prima della lookup; `retrieve_and_hydrate()` propaga l'hydration stale nel dettaglio
esistente dell'outcome provider, consumato dalla UI senza nuovo campo API. Le regressioni RED sono state
riprodotte prima dei fix.

**Verifica mirata post-fix:** 54 test backend relativi (retention/cache/matching) PASS; Ruff e mypy sui
moduli Python modificati PASS; test `ReviewManualSearch` (2) PASS, incluso il badge stale visibile.

**Review pass 2 (2026-10-23, Sol High): OK WITH NOTES**, nessun blocker tecnico residuo nei due fix.
Nota: il lock della transazione SQLite resta sotto ownership del caller e può vivere attraverso successivi
await di hydration; tale lifetime preesisteva tramite il flush di `cache_put()` e non si deve dichiarare
universalmente assente un lock durante I/O provider.

**Gate locali finali post-fix (2026-10-23):** backend `uv sync --locked --extra dev --extra audio`,
Ruff, mypy, import-linter e pytest PASS (1617 passed, 9 skipped); frontend lint, typecheck, 169 test e
build PASS; E2E Chromium PASS (75 test), incluso il toggle Undo retention/cache. Build segnala solo il
chunk >500 kB; lint frontend riporta warning esistenti, nessun errore.

**Decisione owner alla ripresa (2026-10-23):** scope R20 invariato; la checkbox preesistente non
accessibile da tastiera è fuori scope. Sono accettate le prove backend dopo restart/provider failure e
la prova del componente UI per il fallback stale. Il subscenario completo in Chrome resta **NOT
VERIFIED**, non PASS.

**Browser acceptance (2026-10-23): PARTIAL PASS.** Settings chiarisce la retention, il toggle salva,
persiste dopo reload ed è stato ripristinato al valore enabled; viewport 375px senza overflow. Ricerca
manuale iniziale e selezione risultato MusicBrainz PASS. Il toggle Undo non è keyboard-operable: il
componente condiviso `frontend/src/components/ui/Checkbox.tsx` rende un label/span senza input/role/tab
stop; il problema preesistente è fuori scope e non è stato corretto. Il subscenario outage con stale
hydration non è stato verificato in Chrome: dopo aver impostato search fresca e hydration scaduta da 1
giorno, `sweep_provider_cache()` ha conservato tutte le righe (pruned=0), ma SIGTERM a un processo
Main-owned è stato negato dal sandbox; non è stato ripetuto con altri strumenti. Il test backend dopo
restart/provider failure e il test UI di rendering restano PASS e sono accettati dall'owner come prove
per il fallback stale; non costituiscono PASS del subscenario browser. DevTools: nessun browser request
fallito o console error; segnalati sei campi senza id/name.

**Ricevuta di consegna (2026-10-23):** commit codice R20 `2f1e4f001aa9d5f2273f5f06a586b54d0c60bb6b`
(17 file di scope), push fast-forward a `vertillo/muzilla` `refs/heads/main`. Il push ha avanzato il
ramo da `7c8b9a2f3303804548689f7fca1da3ed0e81d802` includendo come antenato il commit locale già
esistente `ff64306bd29162735f828a3fed1f04702b3b3190` (`chore: harness update`); nessun file non-R20
rimasto nel worktree è stato staged nel commit R20. SHA locale `main`, `origin/main` e GitHub main
verificati uguali a `2f1e4f001aa9d5f2273f5f06a586b54d0c60bb6b`.

**CI exact-SHA codice:** run `40018709631`, attempt 1, evento push,
[risultati](https://github.com/vertillo/muzilla/actions/runs/40018709631). Tutti i job richiesti sono
`success`: Backend `120637368260`, Frontend `120637367977`, E2E `120644952615`, Docker `120644952646`.
Rigenerazione OpenAPI/types senza diff; nessuna migrazione DB o dipendenza. Docker ha superato build,
verifica runtime nativo, audit inventory, readiness/hardening, backup/restore e reset recovery.

Git ha emesso stderr `fatal: failed to store: 100001`; il push ha riportato l'aggiornamento del ref e
la successiva verifica no-op `git push --porcelain origin main` ha exit 0, con SHA remota esatta.
Configurazione verificata senza esporre valori: helper globale `osxkeychain`; proxy d'ambiente
loopback con userinfo presente, coerenti con la diagnosi sandbox-proxy già stabilita. Nessuna modifica
a credenziali o configurazione Git/sandbox.

**Stato R20: CLOSED** sul candidato consegnato e verificato sopra. La checkbox non accessibile resta
fuori scope per decisione owner; il subscenario stale-outage completo in Chrome resta **NOT VERIFIED**,
non PASS, con prove backend+component accettate dall'owner come descritto sopra.

**Pulizia host separata e non completata:** fixture temporanea in
`/private/tmp/muzilla-r20-browser-gkofj0nf`, mock ultimo osservato PID 92832/porta 63232 e app PID
92833/porta 63233. `ps` e `kill -TERM 92832` hanno restituito `Operation not permitted`; non è stata
verificata la terminazione e non è stato tentato alcun metodo alternativo. Questa pulizia resta
separata dalla consegna R20.

### R21 — [P2] Elimina la ricerca quadratica dei membri duplicati

**Riferimento:** [pipeline/duplicates.py](../src/muzilla/pipeline/duplicates.py),
riga 96.

Ogni gruppo scorre tutta la libreria ricostruendo il set degli ID per ogni traccia.
Costo quadratico con molte coppie. Microbenchmark della sola selezione:
2.000 tracce/1.000 coppie 0.219 s; 4.000/2.000 0.798 s; 8.000/4.000 3.118 s.
Questi numeri non costituiscono benchmark del workflow completo.

**Intervento:** mappa ID→dati costruita una volta, visita dei soli membri;
ridurre query e caricamenti ripetuti.

**Accettazione:** risultati identici, crescita prossima al lineare e responsiveness
con 100.000 tracce e molte coppie.

**Implementazione e accettazione tecnica (2026-10-24).** `detect_duplicates()` costruisce
una sola mappa ID→Track, carica i membri esistenti con una query `selectinload` e rinvia il flush
fino alla fine. La regressione SQL era RED (4 query `duplicate_members` per 4 gruppi) e ora passa
con una query; regressioni e handler mirati: 14 passed, Ruff e mypy sui file modificati PASS.
Benchmark sintetico su DB SQLite temporanei, misurando rilevamento+commit: 20.000 tracce/5.000
coppie/10.000 singole 1,238 s; 50.000/12.500/25.000 3,144 s; 100.000/25.000/50.000
6,911 s, con 25.000 gruppi e 50.000 membri verificati. Il picco RSS del processo durante questa
sequenza era 1.000 MiB; non è una misura del workflow completo né una certificazione readiness.
La prima review indipendente Sol Medium (run `8e306932-c2d9-451c-a5eb-c4fcb91c6cfa`) era **BLOCK**
per la prova 100k/workflow mancante, senza aver individuato il successivo problema di responsiveness.
Il workflow completo host-mode su 100.000 file sintetici ha poi riprodotto la regressione: il job
crea 25.000 gruppi/50.000 membri in 7,935 s e aggiorna gli stessi gruppi in 6,808 s; durante i due
job, le richieste catalogo/health hanno avuto latenze massime rispettivamente 7,779 s e 5,946 s,
contro ~38–45 ms di catalogo a riposo. Anche `/api/health` si blocca, indicando il worker event loop,
non solo contesa SQLite. Il test RED sull'handler ha confermato la causa: `detect_duplicates()` era
chiamato sincronicamente dall'handler async. Ora l'handler usa `run_with_session` e una sessione
propria nel thread supervisionato; nuovi test provano responsiveness dell'event loop e rollback
su errore dopo l'inserimento del gruppo. Regressioni pipeline/handler mirate: 17 passed; Ruff,
mypy e `git diff --check` PASS.

Il run completo usa il sorgente congelato basato su HEAD `86f62d8500c15a53e19f21b599377f52758e0dc7`
e `duplicates.py` SHA-256 `ca9cba7d66b8f17efdc0614433efdd707dc566d616dcb0d763d53d93f366ebad`, in
host-mode perché Docker build è stato negato dal sandbox (`~/.docker/buildx/refs/...: operation not
permitted`; nessun bypass). Scansione, grouping, mock matching, navigazione, Apply/Undo browser e
zero HTTP 5xx sono passati; il benchmark non è un exact-image PASS: RSS non disponibile (`ps`
negato), Apply/Undo API metriche Docker-only non disponibili e source/image identity fail-closed.
Artifact `perf_result.json` SHA-256 `4be3b634f31ba750a657696166e3831774bc748036aa21b3f3f4d599bd1902f7`;
latencies NDJSON SHA-256 `b83c22ddd4988a280a493db4214e18f990ba3acddb850cc5daaef7e20a69f8fd` in
`/private/tmp/muzilla-r21-perf-86f62d85-20261024T101914Z.3glmuN/host-mode-100000-99973fd7/results/`.
Il re-run esatto è stato costruito sul candidato isolato pulito `e55297fb78b6164e7110ba5fcda76d3ea190d6ed`
(tree `5de6f89e87b2e5351349e326d9dbb186e2fcb067`, build context
`8f653cb37f19d3728d7371853e9a7d8eb9945f85344ed3343bb9d22dd87c7b76`), immagine Docker
`sha256:2f0f730924b7964c6a6da6d363a7fd712c191a77c9e7ac3451ff962f006a442c`; identità sorgente e
immagine verificate. Nel workflow exact-image 100k la scansione ha completato in 165,12 s (605,6
tracce/s), grouping in 56,88 s, API 5xx = 0 e Playwright Apply/Undo è riuscito. Questo run non è un
PASS completo dei threshold PERF: `apply_p95_ms_per_bundle` e `undo_p95_ms` sono fail-closed/non
misurabili perché i batch API restituiscono `409 stale_source`, anche se il flusso UI Apply/Undo
riesce. Non si dichiara production readiness e non si abbassano soglie. Il segmento duplicati
incorporato in quel run usava probe senza intervallo e ha prodotto status error; non è usato come
prova finale.

La prova R21 finale dedicata usa lo stesso esatto image ID e il database sintetico del workflow
100k, con fixture DB seminate dentro il container verificato (`docker exec`) per evitare scritture
host sul WAL. Su 100.000 tracce, 25.000 coppie/50.000 match e 50.000 tracce non abbinate, entrambi
jobs sono riusciti e i conteggi finali erano 25.000 gruppi/50.000 membri, senza job attivi o errori
HTTP catalogo/health. La creazione ha richiesto 11,326 s (catalogo 71 campioni, p95 352,48 ms,
max 423,76; health 70, p95 268,97 ms); l'aggiornamento 7,725 s (48 campioni, p95 420,97 ms,
max 601,05; health 43, p95 331,03 ms). Entrambi i p95 catalogo sono sotto 800 ms. L'artifact
raw durevole è [docs/evidence/r21-stress-100k.json](evidence/r21-stress-100k.json), SHA-256
`c747224e93169ecd53cfa44dca1c464594136f737e9fe2895e752a776563b53d`; è byte-identico al risultato
originario in `/private/tmp/muzilla-r21-perf-86f62d85-20261024T101914Z.3glmuN/exact-image-100000-e6d22281/`.
Cleanup Compose e porta verificati. Crescita del rilevamento+commit su SQLite sintetico: 20k/5k coppie 1,238 s; 50k/12,5k
3,144 s; 100k/25k 6,911 s. Backend completo sul candidato: 1.671 passed, 9 skipped; Ruff,
Mypy e lint-imports PASS; targeted Python 3.12 handler/pipeline 17 passed. La review indipendente
finale Sol High ha dato **PASS, nessun blocker tecnico** per R21 (run
`dc98eb77-75da-4fba-9d00-5b3fa2acd085`, report SHA-256
`5a12b733b7464f16c3b8034ce135bc580c7e219ee8c2da9533d99e34237767b5`); è il secondo e ultimo pass
automatico consentito per il rischio CRITICAL. Restano esplicitamente fuori dalla chiusura R21 il
FAIL dei soli threshold PERF Apply/Undo API già descritto e il fatto che la detection non abbia un
checkpoint cooperativo a metà passaggio.

**Consegna R21 (2026-10-24).** Il codice e l'artifact sono consegnati sul repository canonico
`vertillo/muzilla`, `refs/heads/main`, con push fast-forward nel commit
`d9a2fb9128f3c9edaa0904c6f975c88c343b4528` (tree `6928fa6e59712855f5b624059649f7c1aa624abd`).
Il sorgente e i test R21 coincidono byte per byte con il candidato congelato valutato; `HEAD` locale
e `origin/main` verificati sullo stesso SHA. La CI exact-SHA run
[40091923466](https://github.com/vertillo/muzilla/actions/runs/40091923466), attempt 1, è terminata
con successo: `backend`, `frontend`, `e2e` e `docker` tutti PASS. Le modifiche preesistenti ad auth,
cache, test non R21 e il documento audit non sono incluse nel commit.

Il push e il fetch di verifica sono terminati con exit code 0 e refs corretti, nonostante il warning
`fatal: failed to store: 100001`: il helper Git effettivo è `osxkeychain` e il sandbox runtime
fornisce credenziali temporanee del proxy ai processi isolati. Warning trattato come non bloccante;
non sono state modificate credenziali o configurazioni. R21 non implica PASS dei threshold PERF
Apply/Undo né production readiness.

### R22 — [P2] Ripristina i file con memoria limitata

**Riferimento:** [changes/writer.py](../src/muzilla/changes/writer.py), riga 543.

`path.read_bytes()` carica l'intero audio in RAM prima della copia. La memoria
cresce con la dimensione; grandi file PCM possono consumare o superare il budget
di 2 GiB. Evidenza strutturale, non una misura del picco su un file di 2 GiB.

**Intervento:** copia a blocchi verso un temporaneo sicuro, preservando gli attributi.

**Accettazione:** RSS su file realistici crescenti: memoria aggiuntiva limitata,
contenuto e risultato del ripristino invariati.

### R23 — [P2] Rimuovi l'effetto delle entry di guardia dalla navigazione

**Riferimento:** [ReviewDetail.tsx](../frontend/src/pages/ReviewDetail.tsx), riga 429.

Quando l'editor diventa dirty viene aggiunta una entry history non rimossa al
salvataggio. Riproduzione browser: dopo Save, un Back resta sul dettaglio anziché
tornare all'inbox.

**Intervento:** blocco integrato col router o ripristino esatto della history.

**Accettazione:** Back/Forward dopo salva, scarta, resta e modifiche ripetute;
verificare destinazione reale, URL, filtri e assenza di entry spurie.

### R24 — [P2] Aggiorna le review durante il lavoro in background

**Riferimento:** [useReviews.ts](../frontend/src/hooks/useReviews.ts), riga 28.

Il polling del dettaglio avviene solo in `applying`. Una review `preparing` non
viene aggiornata al completamento senza altro evento di refetch. Riproduzione
browser: una sola richiesta, nessun aggiornamento successivo.

**Intervento:** aggiornamento derivato da bundle/task/run attivi, oppure eventi
job che invalidano le query pertinenti.

**Accettazione:** apertura durante preparazione, retry enrichment e Undo già
avviato; aggiornamento senza reload e stop del polling a lavoro terminato.

### R25 — [P2] Usa il lockfile per CI e immagine runtime

**Riferimenti:** [Dockerfile](../docker/Dockerfile), riga 99;
[ci.yml](../.github/workflows/ci.yml), riga 44.

Docker installa `.[audio]` con pip; diversi job CI risolvono senza lockfile.
L'audit esporta invece uv.lock. Pacchetti testati, auditati e distribuiti possono
divergere anche sullo stesso commit. La build Docker successiva lo conferma:
30 versioni installate differiscono da uv.lock, incluse SQLAlchemy
2.0.51 → 2.1.3, Starlette 1.3.1 → 1.7.0 e urllib3 2.7.0 → 2.8.0.
Inventario e confronto completi sono nell’[appendice Docker](code-review-2026-10-04-docker.md).

**Intervento:** installazione congelata di gruppi/extra dal lockfile in CI e
runtime; inventario dei pacchetti confrontabile e registrato.

**Accettazione:** build pulita usa versioni attese oppure fallisce; audit effettuato
sull'inventario realmente distribuito, senza risoluzione silenziosa di nuove versioni.

### R26 — [P2] Risolvi gli advisory nel lockfile Python

**Riferimento:** [uv.lock](../uv.lock), riga 2075.

Audit dell’export runtime **congelato del lockfile**: tre advisory per
urllib3 2.7.0, PYSEC-2026-4175/4176/4177.
Upstream indica 2.8.0 come versione corretta: [TLS dei proxy HTTPS](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77),
[allocazione non limitata nello streaming](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw),
[loop nello streaming Deflate](https://github.com/urllib3/urllib3/security/advisories/GHSA-gh4c-6fx4-qh6g).
Presenza nel lockfile confermata; exploit attraverso Muzilla non dimostrato.
L’immagine costruita durante il riesame contiene invece urllib3 2.8.0 e il
suo audit non segnala vulnerabilità note. R26 resta aperto sul lockfile:
non attribuire questi tre advisory all’immagine verificata e non considerare
la risoluzione non congelata di R25 una correzione riproducibile.

**Intervento:** aggiornare dipendenza e lockfile, audit e regressioni nell'ambiente
distribuito. Eventuale accettazione temporanea documenta esposizione, compensazioni
e scadenza secondo readiness.

**Accettazione:** nessun advisory non accettato nell'inventario effettivo; contratti
provider e funzionalità native continuano a passare.

### R27 — [P2] Supporta rinomine del solo casing

**Riferimento:** [changes/writer.py](../src/muzilla/changes/writer.py), riga 54.

Percorsi distinti sullo stesso inode sono sempre collisioni, senza distinguere
hard link e case-only rename. Su filesystem case-insensitive,
`Track.mp3 → track.mp3` fallisce con `destination already exists`.

**Intervento:** distinguere i casi senza perdere no-clobber; eventuale percorso
intermedio deve essere journaled e recuperabile.

**Accettazione:** Apply/Undo/rollback/crash su filesystem case-sensitive e
case-insensitive; hard link distinti e vere collisioni continuano a essere rifiutati.

### R29 — [P2] Misura realmente i massimi dichiarati dal benchmark

**Riferimenti:** [perf_benchmark.py](../scripts/perf_benchmark.py), righe 2330,
2424 e 2852.

`fd_count_max` e `sqlite_connections_max` vengono riempiti con un singolo
campione, non con un massimo sul workflow. `peak_rss_mib` e la metrica valutata
sono congelati prima della prova browser finale, mentre il sampler continua
fino al `finally`: un aumento successivo non cambia il risultato pubblicato.
Nel run a 100.000 tracce il harness dichiara PASS con 21 FD, 7 FD SQLite e
1.169,4 MiB, ma questi numeri non dimostrano tutti i picchi richiesti dalla
readiness. Il contatore SQLite è inoltre un proxy di FD, non una misura diretta
delle connessioni. L’evidenza è il percorso effettivo del harness; non è stato
osservato un superamento di risorse e non si afferma un OOM inesistente.

**Intervento:** campionare risorse durante tutte le fasi, raccogliere il risultato
solo dopo l’arresto del sampler, distinguere memoria container/RSS e FD/connessioni,
e fallire esplicitamente quando la misura necessaria manca. Se una misura è un
proxy o un campione istantaneo, dichiararlo senza confrontarlo come un picco.

**Accettazione:** un picco controllato nell’ultima fase rende il gate FAIL;
assenza/errori del sampler non producono zero o PASS. Conservare serie e massimi
riconciliabili, poi rieseguire il workflow completo con le soglie originali.

**Addendum tecnico R29 (2026-10-24, senza chiusura).** Prima del workflow completo,
la consulenza tecnica read-only Sol High (`50068fc4-af9d-4905-a93d-2be84bd7101e`)
ha rilevato collisioni del project Compose a timestamp e `down -v` senza prova di ownership.
Il candidato usa ora un project UUID, preflight fail-closed su container anche stopped,
network e volume per label e nome, traccia ogni tentativo di startup, rimuove con gli stessi
file Compose/environment senza `-v` e verifica che non restino risorse; ownership o teardown
incerti falliscono senza cancellazione. Una seconda consulenza (`cc7c1070-4919-4d76-9a13-daccc12fbfdc`)
ha rilevato che l’overlay auth-disabled poteva pubblicare su `0.0.0.0`: il run verifica
configurazione e mapping effettivo loopback `127.0.0.1` prima/dopo l’avvio. Sono salvaguardie
limitate al fixture del benchmark; Compose di produzione, autenticazione, workload e soglie
non cambiano. La soglia DB resta un proxy FD campionato e il conteggio fisico delle connessioni
SQLite rimane non misurato: nessuna dichiarazione di readiness.

**Verifica pre-run (2026-10-24).** `uv sync --locked --extra dev --extra audio`,
Ruff su `src/tests` e harness, MyPy su `src` e `scripts/perf_benchmark.py`,
`lint-imports`, `--help` e `git diff --check` sono PASS; harness **50 passed**.
La configurazione Compose risolta pubblica `127.0.0.1:1846 → 8080` (il mapping live
resta da provare nel run). Il run backend finale passa: **1.693 passed, 9 skipped, 1
warning**. Un tentativo completo precedente aveva fallito una volta il test provider
invariato `tests/api/test_providers.py::test_get_providers_status_reflects_recorded_rate_limit`;
la suite finale non riproduce il failure. Nessun file `src/` è nel delta R29.

**Review indipendente Sol High, pass 1** (`1857a8c9-cc99-457d-b343-bfc24d4947ba`):
REQUEST CHANGES per tre confini — campioni zero RSS/FD misti, persistenza della serie e
cleanup nei workflow abortiti, fallback `docker buildx` dopo qualunque errore `docker build`.
Il candidato li corregge; harness e gate backend finali sono verdi.

**Review indipendente Sol High, pass 2** (`43f73aa7-024d-46ba-92d3-de331004df99`):
REQUEST CHANGES. Resta da provare il percorso eccezionale reale: il test precedente creava un
errore locale e chiamava direttamente il report helper, senza `main`, `_run_workflow` o cleanup.
**Decisione del proprietario (2026-10-24):** eccezione puntuale per una sola ulteriore review
indipendente Sol High, limitata a questa correzione e ai criteri interessati; se restano blocker,
fermare il lavoro senza altri cicli.

La regressione `test_main_aborted_workflow_stops_sampler_cleans_and_publishes` ora esegue `main`
con setup esterno isolato, sampler reale e `_run_workflow` che solleva; verifica propagazione,
stop/thread join, campioni terminali e valori, cleanup Compose reale con `down` fallito in modo
iniettato, ordine cleanup/process-stop/report, fail-closed `threshold_pass=false` e checksum.
Il test mirato e l’intero harness passano (50 passed); Ruff, MyPy su `src`/script, lint-imports
e `git diff --check` sono PASS. La singola review Sol High autorizzata (`d5cdfbd6-be63-4886-ad17-24f2e1fecf2f`, fresh/high) è stata **BLOCKED al preflight**, prima di ispezionare il codice: la read del canary deliberatamente inesistente `/private/tmp/muzilla-r29-review-read-canary-e1373a2a60b341288c7fbcde8e4794f3.txt`, validato fuori dal cwd effettivo del figlio, ha restituito `ENOENT` invece del denial Guardrails pathAccess atteso (`mode=block`). Il transcript conferma cwd candidato, modello Sol e effort high; nessun review/test è stato svolto. Il proprietario ha poi autorizzato un solo preflight aggiuntivo, ora esaurito. Il reviewer
Sol High fresh/background (`a6a6cd51-8c66-4034-ad90-161c9208b367`) ha letto una sola volta
il canary sintetico `/private/tmp/muzilla-r29-review-read-canary-420d021232664eeaab947dc6e1b33128.txt`;
era inesistente e sibling del cwd. Il matcher configurato (`mode=block`, `allowedPaths=[]`,
risorse dinamiche docs/skill non pertinenti) lo nega, ma l’esito è stato ancora `ENOENT`; nessun
codice o test è stato ispezionato.

Diagnosi read-only originaria: il child abilita le estensioni ambient, ma il filtro globale
esclude `-extensions/path-access/index.ts` mentre `.pi/extensions/guardrails.json` dichiara
`features.pathAccess=true` e `mode=block`. Il `-path` esclude la risorsa; i due canary precedenti,
però, erano in `/private/tmp`, consentito dalla sandbox. La successiva decisione owner chiarisce che
l’esclusione di path-access è intenzionale e che il controllo Guardrails non è applicabile.

**Rettifica owner (2026-10-24):** path-access deve restare escluso. In base ad `AGENTS.md`, il
controllo Guardrails `pathAccess` è N/A, non un requisito fallito; la precedente proposta di
abilitare l’estensione e modificare la configurazione protetta è superata, mantenuta solo come
cronologia. Nessuna configurazione è stata modificata.

**Nuova autorizzazione owner (2026-10-24):** un solo preflight sandbox nel reviewer Sol High
fresh/background, con canary fittizio inesistente fuori dall’effettivo allowlist, senza segreti.
Canary `/mnt/muzilla-r29-sandbox-read-canary-ff1a4f64da27443ca15e325312ae83e8.txt`: validato
inesistente e non ammesso dal matcher sandbox per `projectTrusted` sia `false` sia `true`. Se il
reviewer riceve il diniego `Sandbox: read access denied`, prosegue con la review mirata R29 già
prevista; in caso contrario si ferma senza altri tentativi. Questa autorizzazione sostituisce il
precedente stop soltanto per tale singolo preflight e la review condizionata.

**Esito preflight/review (2026-10-24):** il reviewer Sol High fresh/background
(`9062c090-ed67-445a-943e-cdd610b6b637`) ha letto per prima cosa il canary e ricevuto il denial
atteso `Sandbox: read access denied`; Guardrails `pathAccess` è rimasto non applicabile. La review
mirata è **PASS**, senza blocker concreti. Ha verificato la regressione d’aborto reale
`test_main_aborted_workflow_stops_sampler_cleans_and_publishes` (test:730), la propagazione e il
report fail-closed (`perf_benchmark.py`:3599–3604, 3767–3827), campionamento, ownership/cleanup
Compose e mapping loopback pre/post-start. Non ha eseguito shell, test o browser. È approvazione
tecnica del delta, non chiusura R29: restano benchmark exact-image 100k/Playwright, verifica live
mapping, delivery e CI exact-SHA; le connessioni SQLite reali non sono misurate.

**Gate locali post-review (2026-10-24):** `uv sync --locked --extra dev --extra audio`, Ruff
(sorgenti, test e script), MyPy (`src` e script), `lint-imports` e il backend completo
`pytest -q --cov=muzilla --cov-report=term-missing` passano sul candidato R29: **1.693 passed,
9 skipped, 1 warning in 658,98 s**. La suite non sostituisce il workflow exact-image/browser.

Il candidato resta isolato nel worktree `/private/tmp/muzilla-r29-candidate-6699c1d0-20261024`,
branch `agent/r29-resource-measurements-20261024`. Il commit locale `7ca9fc5` contiene il delta R29,
basato su `6699c1d0bf93da1c10fa778205bcb890aa68450b`, limitato a `benchmark/thresholds.json`,
questo ledger, `scripts/perf_benchmark.py` e `tests/test_perf_harness.py`. `main` e `origin/main`
sono ancora a `6699c1d0`; le modifiche preesistenti nel worktree principale restano intatte. Il
candidato è solo locale, non è stato pushato e non ha ancora CI exact-SHA.
**Tentativo workflow exact-image (2026-10-24):** sul source pulito SHA
`babf752f65d908f1a02bc852337b8f4f7000744f`, il runner ha generato 100.000 tracce sintetiche
(seed 0), ma `docker build` è terminato con exit 1 prima di produrre un’immagine: Docker ha
segnalato accesso negato a `/Users/asant/.docker/config.json` e la scrittura Buildx in
`/Users/asant/.docker/buildx/refs/default/default/.tmp-wllbujsvo3rb3ys2a1u7erj6g2690729137`
ha restituito `operation not permitted`. Nessun container/app, sampler, workflow Playwright o
verifica live del mapping è partito; non esistono `perf_result.json`/checksum. La directory di
scratch sintetica da 2,8 GiB è stata rimossa; la porta 1846 è rimasta libera. Non ho riprovato con
`DOCKER_CONFIG` alternativo, altro builder o permessi diversi, per non aggirare il confine sandbox.
Per riprendere serve un percorso Buildx esplicitamente autorizzato e supportato, con accesso minimo
al suo stato temporaneo e senza leggere/modificare configurazione globale o credenziali.

**Retry exact-image autorizzato (2026-10-24):** la sandbox aggiornata include `~/.docker/buildx/`;
il runner Bash corrente ha creato e rimosso una sola volta il canary fittizio
`~/.docker/buildx/refs/default/default/.muzilla-r29-write-canary-081c49b8-1173-4470-8d50-2d5d8efcaa54`, provando l'accesso effettivo al percorso negato prima. `~/.docker/config.json` è rimasto protetto. Per il run è stato usato un `DOCKER_CONFIG` univoco temporaneo, con la sola voce `cliPluginsExtraDirs` verso il plugin Buildx installato; nessuna credenziale o configurazione/contesto host è stata copiata. `DOCKER_HOST=unix:///var/run/docker.sock` ha restituito gli stessi metadati dell'endpoint Docker già autorizzato (`linux|aarch64|10|25161572352`); Buildx `v0.35.0-desktop.2` trovato. Config temporanea rimossa al termine.

Il runner ha verificato source pulito SHA `ad75cba97630029ea24038e8f42b0c723c6593e9`, generato 100.000 tracce sintetiche (`seed=0`), costruito e verificato l'immagine esatta `sha256:6efc5808bda086a500c4581e7d8a92b46a28df4546b6cf567abc62d8d27b2b92` e completato tutte le fasi del workflow. L'ispezione live Compose conferma `127.0.0.1:1846 → 8080`; Playwright Apply/Undo UI su 10 tracce PASS. Il cleanup Compose è PASS (container/rete del progetto assenti dopo il run); la porta 1846 è libera. Risultato: `/private/tmp/muzilla-r29-perf-ad75cba-20261024/results/perf_result.json`, SHA-256 `05a15302a87b292e7c0c57d430daa3de06817ec624b6a5aed703148c0355496c`; serie `latencies.ndjson` SHA-256 `3b7b1f0ec39d4ab22608a7d3d19412913de59d332aaeec939af009bd2876f97b`.

**Acceptance FAIL fail-closed, soglie originali invariate.** RSS max `1105.23 MiB ≤ 2048`, FD max `34 ≤ 1024`, throughput cold scan `609.6 ≥ 30 track/s`, scan incrementale p95 `1518.1 ≤ 5000 ms`, search `61.7 ≤ 800 ms`, filtri `184.5 ≤ 800 ms`, grouping `65.09 ≤ 120 s`, matching `984.7 ≤ 2000 ms`, review generation `401 ≤ 5000 ms`, navigation `5 ≤ 500 ms`, cancel `187.7 ≤ 2000 ms`, API 5xx rate `0`, e Playwright Apply/Undo passano. Il massimo proxy DB-file-FD osservato è 3 ma la serie è incompleta: 40/2028 campioni hanno errore `descriptor changed during DB-file sample` mentre l'FD scompare fra `listdir` e `readlink`; solo 1988 valori sono osservati. `sqlite_connections_max` resta unavailable e il gate `resource_sampling` fallisce; il sampler però si è arrestato e ha raccolto il campione terminale (gap massimo 0.377 s). I 30 bundle API temporizzati hanno tutti risposto 409 `stale_source`, quindi Apply/Undo p95 sono unavailable: il setup diretto via `put_revision` omette `physical_guard` nei rispettivi `source_snapshot`, richiesto dal preflight Apply. Il pass Playwright separato non sostituisce quei campioni.

Il run identifica due correzioni del solo harness necessarie senza rilassare soglie o completezza: gestire la race degli FD con un ricampionamento limitato e ancora fail-closed se instabile; creare i bundle API con source snapshot fisico completo tramite il percorso/helper corrente. Sono cambiamenti al candidato e richiedono una nuova review. La review Sol High `9062c090-ed67-445a-943e-cdd610b6b637` ha dato PASS sul delta precedente; l'eccezione owner per un'ulteriore review mirata è stata consumata. **R29 resta APERTO e non consegnato; R28 non è iniziato.** È necessaria una decisione owner prima di avviare un nuovo ciclo review oltre il limite già autorizzato.

R29 resta **APERTO**: il workflow exact-image 100k, Playwright e mapping loopback sono stati
eseguiti/provati sul source `ad75cba`, ma il run è fail-closed per le misure incomplete e Apply/Undo
p95 unavailable. Delivery e CI exact-SHA non sono stati eseguiti. R28 non è iniziato. La soglia
SQLite resta un proxy FD, non un conteggio connessioni. Soglie, criteri, Compose di produzione e
autenticazione restano invariati.

**Nuovo ciclo mirato autorizzato dal proprietario (2026-10-24):** solo due correzioni dell’harness
(race di campionamento DB-FD e source snapshot incompleto delle fixture API Apply), regressioni
Docker/host prima del fix e uso di `capture_file_guard` alla creazione. Nessun controllo di produzione,
criterio di completezza o soglia può cambiare. È autorizzata una review indipendente Sol High e, se
necessaria, una sola re-review mirata; workflow exact-image 100k completo, delivery e CI exact-SHA
restano obbligatori prima di R28.

**Regressioni deterministiche prima del fix:**

```sh
uv run pytest -q tests/test_perf_harness.py -k 'docker_resource_probe_retries_a_closed_descriptor or docker_resource_probe_fails_closed_after_persistent_descriptor_churn or apply_fixture_source_snapshots_capture_physical_guards_in_both_modes'
```

Ha dato **3 failed**: il probe non ricampionava (una sola lettura della directory FD) e mancava
`physical_guard` in entrambi i setup fixture Docker/host. Dopo il fix: harness completo **53 passed**.
Il probe ripete al massimo tre campionamenti; se il descriptor continua a sparire, registra errore e
non pubblica un conteggio SQLite inventato. Entrambi i setup fixture acquisiscono il guard tramite
`capture_file_guard`; nessun codice di produzione o soglia è cambiato.

**Gate locali post-fix (2026-10-24):** `uv sync --locked --extra dev --extra audio`, Ruff su
`src`, `tests` e script, MyPy su `src` e `scripts/perf_benchmark.py`, `lint-imports` e
`git diff --check` sono PASS. Il primo backend completo ha avuto un failure intermittente nel test
non modificato `tests/jobs/test_worker.py::test_durable_apply_result_wins_timeout_after_thread_join`;
il test isolato è passato, poi il backend completo è PASS: **1.696 passed, 9 skipped, 1 warning,
649,16 s**. Rischio di flake registrato, non corretto fuori scope.

**Stato refs/delivery:** il candidato R29 rimane sul branch `agent/r29-resource-measurements-20261024`,
basato su `6699c1d`; nel frattempo `main` e `origin/main` sono avanzati insieme a `e9f50e7`
(solo aggiornamento di `AGENTS.md`). Le storie divergono: il candidato non è fast-forwardabile su main
con la procedura attuale. Nessuna merge/rebase/cherry-pick o push è stato fatto; serve risolvere
l’integrazione secondo `AGENTS.md` prima della consegna. Review, benchmark exact-image post-fix,
delivery e CI restano pendenti; R28 non è iniziato.

**Review indipendente Sol High (2026-10-24):** run `abdf71ec-7ee6-4711-89f5-31c1844b9088` — **REQUEST CHANGES**. Un blocker: la fixture Apply/Undo host legge DB e library root tramite configurazione ambientale del processo harness invece di usare lo storage scratch e `library_dir`; la regressione precedente controllava solo il testo. La correzione Docker FD è stata accettata.

**Riparazione/verifica mirata R29:** `_create_host_apply_bundle` ora apre esplicitamente `scratch_root/data/muzilla.db` e acquisisce i physical guard entro `library_dir`; il ramo host chiama questa setup. Il test `test_host_apply_fixture_uses_scratch_storage_and_validates_persisted_guards` usa DB Alembic migrati e file audio sintetici separati per scratch/ambient: con storage env assente rifiuta ogni `load_config`, con env conflittuale punta l’ambiente a un secondo DB/library di test. In entrambi i casi crea bundle solo nel DB scratch, verifica che lo storage ambientale resti byte-per-byte invariato e convalida ogni guard persistito tramite `_source_precondition_error`.

Esiti: test mirati **2 passed**, harness completo **54 passed**; Ruff sui file modificati, MyPy su `src`/script, `lint-imports` e `git diff --check` PASS.

**Re-review indipendente Sol High (2026-10-24):** run `1e7950d5-4c58-4673-b84c-92c03aa4f787` — **PASS, nessun blocker concreto**. Ha verificato la provenienza scratch del DB e della library, la regressione host eseguibile, la validazione dei guard persistiti, la non-interferenza con lo storage ambientale e la permanenza delle correzioni Docker. Review basata su diff e codice collegato; il reviewer non ha eseguito shell, test o browser. Il ciclo di review autorizzato è esaurito; non è disponibile un ulteriore ciclo automatico.

**Gate backend finale (2026-10-24):** `uv run pytest -q --cov=muzilla --cov-report=term-missing` — **1.697 passed, 9 skipped, 1 warning, 652,73 s**. Warning non bloccante: deprecazione `starlette.testclient` sull’uso di `httpx`.

**R29 resta APERTO:** benchmark exact-image/Playwright post-fix 100k, integrazione, delivery e CI exact-SHA sono pendenti. Il candidato precedente rimane isolato sul branch `agent/r29-resource-measurements-20261024` (HEAD `727429b875dca2a36b1fcb37339f0745b234f4f8` più delta non committato). `main` è a `e9f50e7d4680054dbf6f2d428e91543ffabcb730`; la divergenza impedisce il fast-forward.

**Decisione owner successiva (2026-10-24, questionnaire `308119f9-efa9-4882-a969-6157e2ca04c6`):** selezionato “Nuovo candidato e9 → delivery”. Autorizzati un worktree candidato isolato basato su `e9f50e7`, l’applicazione dell’intero delta R29 revisionato dal baseline `6699c1d` (senza merge/rebase/cherry-pick), commit locale, gate backend/exact-image, push canonico su `main` e CI exact-SHA. Nessuna release, tag o deployment è autorizzata. Il worktree root con modifiche preesistenti non va alterato; R28 resta fermo fino alla chiusura end-to-end R29.

**Nuovo candidato di consegna:** worktree `/private/tmp/muzilla-r29-final-e9-20261024`, branch `agent/r29-e9-delivery-20261024`, base `e9f50e7d4680054dbf6f2d428e91543ffabcb730`. Il delta completo R29 dal baseline `6699c1d` è stato applicato senza cherry-pick/rebase/merge; patch applicata SHA-256 `76663fad90f7ad5b753a1b09ed793beb3e4c6ca1ab39f51ef4d53757f04f3ff7`. Quattro soli file risultano modificati rispetto a e9 (`benchmark/thresholds.json`, questo ledger, harness e test); i target/operatori del benchmark restano invariati, le sole differenze di threshold-file sono descrizioni. Il vecchio worktree e il main dirty non sono stati toccati.

**Gates locali sul candidato e9 (2026-10-24):** `uv sync --locked --python 3.12 --extra dev --extra audio` con CPython **3.12.13**; Ruff su `src`, `tests` e `scripts/perf_benchmark.py`; MyPy su `src` e sullo script; `lint-imports`; `git diff --check` — PASS. Suite backend finale `uv run pytest -q --cov=muzilla --cov-report=term-missing` — **1.697 passed, 9 skipped, 1 warning, 658,45 s** (warning Starlette/httpx già noto).

**Exact-image 100k, tentativo 1 (2026-10-24):** commit `c4724d1bfb10e7d850153e36a308fe1728cedbfa`, source clean verificata, immagine esatta `sha256:14d345762842babc722661d435eb7b8f484daf9ec65513dcd5832207d2cc622b`; dataset 100.000/seed 0. Loopback `127.0.0.1:1846 → 8080`, workflow completo, Playwright Apply e Undo entrambi verificati, Compose cleanup **cleaned**. `apply_p95_ms_per_bundle=1147,7`, `undo_p95_ms=1128,9`, `api_error_rate=0`, audio tools OK, throughput scan `599,3 tracks/s`. La serie risorse ha 2.287 campioni; FD e DB-file-FD proxy completi (massimi 37 e 4), ma RSS ha 2.284/2.287 campioni: sequenze 878, 1796 e 1913 hanno errore `VmRSS unavailable` per PID effimeri. RSS massimo osservato 900,27 MiB non è autoritativo; `peak_rss_mib=null`. Il cgroup (2047,996 MiB) resta una diagnostica separata, non sostituisce RSS. Gate **FAIL-CLOSED**: `muzilla_rss_mib_peak unavailable` e `resource_sampling incomplete`; nessun campione è stato convertito in zero.

**Rerun diagnostico 2:** stesso commit e stesso image digest con `--no-build`; Compose cleanup **cleaned**. RSS completo, ma il DB-file-FD proxy è mancato alla sequenza 2269 anche dopo i 3 tentativi (`descriptor changed during DB-file sample after 3 attempts`); gate risorse **FAIL-CLOSED**. Questo report è solo diagnostico, non accettazione: `candidate_image_labels` è vuoto e il runner segnala mismatch di source identity nel percorso `--no-build`; il digest coincide con l’immagine già source-verified nel tentativo 1.

Raw evidence durabile: `~/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/2026-10-24T09-37-11-389Z_01a16d45-76dd-7257-940a-3c9233de57af/r29-benchmark-e9-c4724d1/` (tentativo 1: `perf_result.json` SHA-256 `a17968aec44c9783e66af884df94476d1f52b62cf1dd02abd330cd1a62ac770c`, `latencies.ndjson` `39eb4d9f0c0657376462115b16d6587c37cde0299a299ebc7ac495fec6a0ecdf`; rerun: `perf_result.json` `8c1e4577ab8a7ecf8b148e2f9a687c2bf27310ec9efb91b16a4a6c2a7ccdea97`, `latencies.ndjson` `04625184dcfc54064ca0ad1d16a7a8c754e92531119410ac8bb17c981e7194d8`).

**R29 resta BLOCKED/APERTA:** due workflow exact-image consecutivi hanno mostrato campioni risorsa incompleti, prima RSS e poi DB-file-FD persistente dopo retry limitato. Non sono state cambiate soglie/operatori né indebolite completezza/semantica; il comportamento fail-closed è rispettato. Il limite owner di review (review iniziale + una re-review) è esaurito: non ho fatto altre modifiche né un terzo run. Nessun push, merge o CI è stato avviato; il commit `c4724d1` resta locale e R28 non è iniziato. Per riprendere serviva autorizzazione esplicita a un ulteriore ciclo tecnico/review sul probe RSS e sulla race DB-file-FD; l’owner ha poi concesso la deroga seguente.

**Deroga owner successiva (2026-10-24):** autorizzata una consultazione Oracle Sol High read-only sulla metodologia RSS/DB-file-FD, seguita da una riparazione circoscritta affidata a Implementer Sol Medium fresh. Prima del benchmark completo sono obbligatorie prove in Linux sotto churn di processi/descriptor e regressioni deterministiche, inclusi errori reali. Dopo la riparazione è autorizzata una review indipendente Sol High e, solo se necessaria, una sola re-review mirata; questa eccezione esaurisce ogni ulteriore allowance R29. Non aumentare i retry senza diagnosi, non convertire misure mancanti in zero, non cambiare significato metriche, soglie, operatori o fail-closed. Solo dopo gates, exact-image 100k, delivery e CI exact-SHA R29 si può iniziare R28. Nessuna modifica ai controlli di produzione o allo stato dirty preesistente di `main`.

**Consultazione Oracle Sol High (2026-10-25; run `9e63534d-6779-445c-855f-c5b3f3ef9cee`, report `~/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/9e63534d-6779-445c-855f-c5b3f3ef9cee/r29/resource-sampling-oracle.md`):** il preflight native background ha ricevuto il denial Sandbox previsto; review read-only, nessun comando/test o edit. Le evidenze storiche non conservano stato/errori dei PID né identità degli FD, quindi indicano cause probabili ma non provano retroattivamente zombie/uscita o FD chiuso. Non dichiarare validi i campioni mancanti.

**Direzione tecnica adottata per la riparazione:** rimuovere i retry dell'intera scansione FD e classificare soltanto condizioni di churn dimostrate e locali alla singola operazione. Per RSS, ancorare la lettura a un'identità di processo (directory FD Linux), validare campo/unità, includere valori validi ed escludere dal totale solo un child la cui uscita/stato terminale senza address space sia confermato; PID 1 resta obbligatorio. Un campo mancante su processo vivo o non classificabile, errori di permesso/I/O e status malformato restano errori fail-closed. Per DB-FD, risolvere ogni entry numerica separatamente; `ENOENT` è churn solo se un controllo no-follow conferma che la stessa entry è scomparsa, mentre entry ancora presente ma non risolvibile, permessi o errori inattesi falliscono il proxy. Un target riusato va risolto come target corrente; zero DB-FD è valido solo se tutte le entry sono risolte o confermate chiuse. Registrare i churn confermati nei metadata.

**Limite esplicito:** procfs non offre snapshot atomici di processi/RSS/FD né una misurazione congiunta. Documentare RSS come somma di osservazioni per processo nell'intervallo e DB-FD come conteggio dei target main-DB risolti nell'intervallo; sono massimi osservati campionati, non high-water mark o fotografia simultanea. Mantenere `_ResourceSampler`, gap/terminal/join e pubblicazione fail-closed invariati, senza carry-forward/interpolazione/zero sostitutivi; cgroup resta diagnostico separato e `sqlite_connections_max` resta solo il proxy DB-file-FD, non connessioni fisiche. I 16 target/operatori non cambiano. Prima del workflow 100k sono richieste regressioni deterministiche per churn e errori reali e una prova Linux breve sul candidate image con identità verificata; poi review, gate completi, workflow esatto e cleanup.

**Riparazione collector (Implementer Sol Medium fresh, run `5fe15d66-d75d-47ce-9773-d8b205b84ee3`; report `~/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/5fe15d66-d75d-47ce-9773-d8b205b84ee3/r29/resource-measurement-implementation.md`):** le letture RSS sono ancorate al directory FD del PID e validate; il task appena creato senza `VmRSS` riceve una sola conferma dopo 1 ms sullo stesso FD. Solo identità confermate terminate o terminali senza RSS vengono escluse; processo vivo non misurabile e errori reali restano fail-closed. Gli FD sono risolti individualmente, distinguendo chiusure confermate, target riusati e incertezza; il conteggio è completato solo se l’intero set è classificato. Nessun cambio a sampler, soglie, operatori, semantica delle metriche o configurazione di produzione.

**Regressioni e gate locali (candidato `c4724d1` + delta non committato):** le prove mirate `pytest -k 'docker_resource_probe or malformed_envelopes'` danno **12 passed**; coprono churn confermato e negativo, PID/FD riusati, errori di permesso/I/O, campioni mancanti/malformati e propagazione al sampler reale. `uv sync --locked --extra dev --extra audio` con CPython 3.12.13, Ruff su `src`, `tests` e script, MyPy su `src` e script, `lint-imports` e `git diff --check` sono PASS. La suite backend completa, eseguita sul contenuto finale, passa **1.707 passed, 9 skipped, 1 warning in 651,77 s**. Un precedente run completo e il test isolato hanno riprodotto il failure del test provider invariato `tests/api/test_providers.py::test_get_providers_status_reflects_recorded_rate_limit`, presente anche sul baseline pulito e9; il successivo run completo è PASS. Il test e il comportamento provider non sono stati modificati. I 16 target/operatori sono byte-per-byte invariati e ora sono tutti fissati dal test.

**Prova Linux sotto churn:** il probe candidato (SHA-256 `2a723fc5f41536a1138177df773577ac64eb705a5c29f37185463cc6e831d856`; test `326b9f6ac02ea03e6115a7da31fc063c1d4f702cfe1e32c2cdfbf5b45fbbc973`) ha prodotto **287 campioni completi**, gap massimo 0,129 s e zero errori, osservando 625 chiusure FD e 109 esclusioni processo confermate durante 1.180 fork/reap e 16.818 batch FD. Il proxy DB-FD è rimasto tra 2 e 14. Il fixture è stato eseguito come utente `muzilla`, senza rete, nel container poi arrestato e rimosso. Artifact: `~/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/2026-10-24T09-37-11-389Z_01a16d45-76dd-7257-940a-3c9233de57af/r29-linux-churn-f928b885/resource-sampling.json`. L’immagine Linux identificata è la base pre-riparazione `sha256:14d345762842babc722661d435eb7b8f484daf9ec65513dcd5832207d2cc622b` (revision `c4724d1`); il probe candidato è stato eseguito dentro quel container, quindi questa prova non sostituisce l’immagine app post-riparazione né il workflow 100k. Due precedenti smoke con il nuovo fixture erano falliti correttamente fail-closed prima della conferma da 1 ms; i campioni storici incompleti non sono stati riabilitati.

**Review indipendente Sol High, pass 1** (run `c1e90832-af36-47c2-85a7-6a8915b21ffb`, report `~/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/e86fda7c-a472-41bc-b38b-ca84ed53a057/r29/resource-sampling-review.c1e90832.md`): **PASS** sul delta collector, nessun blocker concreto. Ha verificato identità RSS, classificazione RSS/FD, envelope, sampler/publicazione fail-closed, soglie e regressioni; non ha eseguito shell o test. È review tecnica del delta, non chiusura R29. Resta disponibile soltanto l’eventuale re-review mirata già autorizzata se il workflow post-riparazione richiede altre modifiche.

**Prova churn sul candidate image post-riparazione:** immagine `sha256:a288e6c13a1b1d39762316bd9c51eb395fca57519a1dffb02e8aa23e1752f9a4`, revision `9da2f1559350ff3f34a38d2f245ca1d924ce1e7d`, tree `b393154ea87f8724e5ed8edf0b8510c5b9b9aa93`, build context `43f0cdc3eba8d5083cf954c0488d19598b3b2f8597df8fd5e9dfb7054890acf4`; Config.User `muzilla`. Su `--network none`, **286 campioni completi**, gap massimo 0,114 s, zero errori, 341 chiusure FD e 108 esclusioni RSS confermate durante 1.192 processi fork/reap e 16.942 batch FD; DB-FD proxy 2–14. Il container è stato arrestato e rimosso. Artifact: `~/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/2026-10-24T09-37-11-389Z_01a16d45-76dd-7257-940a-3c9233de57af/r29-exact-image-smoke-9da2f15593-637a6213cc/linux-churn.json`. Un primo preflight del runner si era fermato prima di creare il container perché Docker riportava `No such container` invece della stringa attesa; nessun servizio era stato avviato. Il successivo controllo ha riconosciuto l’assenza e la prova sul candidate image è PASS.

**Workflow exact-image 100k post-riparazione — PASS, soglie originali:** source clean `9da2f1559350ff3f34a38d2f245ca1d924ce1e7d`, tree `b393154ea87f8724e5ed8edf0b8510c5b9b9aa93`, build context `43f0cdc3eba8d5083cf954c0488d19598b3b2f8597df8fd5e9dfb7054890acf4`; immagine immutabile `sha256:8d785a86dd239c6425ad9f069bc3db59f35f39abc9fc87c12ada548ea5e2cbc6`, label revision/tree/context corrispondenti e ID del container verificato uguale. Dataset sintetico 100.000, seed 0. Mapping effettivo loopback `127.0.0.1:1846 → 8080`; mock provider solo MusicBrainz verificato; audio tools e grouping job PASS. Apply/Undo Playwright PASS su 10 tracce; Apply p95 1.346,6 ms, Undo p95 1.136,1 ms, API error rate 0. Il sampler si è arrestato con campione terminale e serie completa: **2.323/2.323 campioni, gap massimo 0,235 s, zero errori/mancanti**. Massimi osservati RSS 920,13 MiB, FD 35 e DB-file-FD proxy 3; cgroup 2.048,0 MiB resta diagnostica distinta. Tutte le 16 valutazioni threshold sono PASS, nessun failure; tra i risultati: cold scan 608,7 tracce/s, incremental scan p95 1.515 ms, search 58,7 ms, filters 187,9 ms, grouping 64,21 s, matching 986,3 ms, review generation 486,2 ms, navigation 5,4 ms, cancel 206,6 ms. `sqlite_connection_count_status=not_measured`: il valore 3 è solo il proxy DB-file-FD, non connessioni fisiche. Compose cleanup **cleaned**: il teardown ha registrato il container e la rete del progetto prima della rimozione e zero risorse residue dopo; scratch sintetico rimosso.

Evidenza durevole in `~/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/2026-10-24T09-37-11-389Z_01a16d45-76dd-7257-940a-3c9233de57af/r29-exact-100k-9da2f15593-6c8b4d7ec7/`: `benchmark/perf_result.json` SHA-256 `af1d2b38359847c977db61beecf2572496d3cff46108669f7d5af2bdda0dc184`; `latencies.ndjson` `e89e474f608332682122f1dee7052ddd8aa0b16cf93ef594919550f8e31f74bf`; `checksums.json` `e5c67c7859348e032085ad61fd68bd5a53a363f2a9836bd404cf6d4bef6a9bc4`; `compose.log` `263597822f35f3241bc55c90916236ece531509be586af4c780d987a355e7e58`. I checksum degli artefatti copiano i rispettivi valori del manifest.

Verifica pre-delivery: `origin` è il repository canonico `https://github.com/vertillo/muzilla.git`; `git ls-remote origin refs/heads/main` ha restituito `e9f50e7d4680054dbf6f2d428e91543ffabcb730` con exit 0, coincidente con i ref locali `main` e `origin/main`. Git ha inoltre emesso `fatal: failed to store: 100001`; è non-blocking: helper globale `osxkeychain`, remote GitHub senza userinfo, proxy autenticato loopback e sorgenti runtime/Git confermano lo store della credenziale proxy temporanea dopo la richiesta riuscita. Nessuna credenziale, helper, configurazione o permesso è stato modificato.

**R29 è VERIFIED tecnicamente, ma non ancora CLOSED:** review Sol High pass 1 e criteri, regression/gate locali, churn Linux esatto e workflow 100k passano. Il receipt è incluso nel candidato locale; restano push canonico e CI esatta sullo SHA finale. Solo dopo questi passi inizierà R28.

### R28 — [P3] Allinea README ai comandi e al modello correnti

**Riferimento:** [README](../README.md), riga 214, tabella storage e sezione Undo.

README descrive ancora `muzilla changes apply`, `muzilla changes undo`,
`apply --backup` e retention ChangeSet. CLI attuale non registra questi comandi;
l'operatore può affidarsi a funzionalità inesistenti.

**Intervento:** Apply esclusivamente UI, percorso reale dei backup e retention
ReviewBundle. Documentazione segue il comportamento implementato.

**Accettazione:** esempi confrontati con CLI help e flussi verificati; nessuna
istruzione obsoleta o contraddizione con le specifiche.

### R31 — [P2] Non mostrare “Strong” per la selezione manuale senza confidence

**Riferimento:** [ReviewDetail.tsx](../frontend/src/pages/ReviewDetail.tsx),
logica `bandLabel` nel dettaglio review.

Un candidato manuale valido è serializzato con `confidence_band: "manual"` e
`confidence: null`; non è stata calcolata alcuna confidence. La UI del dettaglio,
tuttavia, tratta un qualunque snapshot non nullo senza valore numerico come
“Strong”. Il badge attribuisce quindi una valutazione inesistente al candidato,
anche se gli altri riepiloghi lo descrivono come selezione manuale. Questo
finding è stato rilevato durante la preparazione deterministica dei candidati
manuali per l'acceptance; non faceva parte del conteggio della review iniziale.

**Intervento (tracking-only; nessun fix in questo pacchetto):** mostrare
“Manual selection” quando lo snapshot indica `confidence_band: "manual"`, senza
inventare score o soglie e senza modificare classificazione, decisione, o stato
dell'operazione. Mantenere distinta R24, relativa all'aggiornamento della review
in background; R31 riguarda esclusivamente l'etichetta della confidence.

**Accettazione:** una review con `candidate_source: "manual"`,
`confidence_band: "manual"` e `confidence: null` mostra l'etichetta manuale,
non “Strong”; confidence numeriche Strong/Ambiguous/Low e stati skipped/no-candidate
mantengono l'etichetta appropriata. Aggiungere un test UI di regressione. R31
resta aperto finché l'implementazione non supera tali verifiche.

## Piano dettagliato di esecuzione

La matrice era vuota al momento della review. I finding R01–R30 costituiscono il
backlog originario; R31 è stato aggiunto successivamente senza riscrivere il
rapporto storico. Le unità dei goal, gli interventi e le acceptance sono definite sopra.
I goal rispettano lo scope congelato e i limiti di selezione in AGENTS.md; la chiusura o
il blocco di un pacchetto non autorizza rimpiazzi o una nuova selezione. Seguire le
dipendenze del piano qui sotto: quelle di fase valgono per i finding selezionati della
fase, oltre alle dipendenze esplicite fra finding. Prima di aprire lavoro dipendente,
verificare il prerequisito: uno non ancora consegnato può essere consumato solo nello
stesso pacchetto coerente congelato, con acceptance tecnica completa e prove
consumer/provider secondo AGENTS.md. Altrimenti deve essere già consegnato; un
prerequisito fuori scope non si implementa automaticamente. Non chiudere un finding
modificando soltanto la descrizione normativa.
R15 (logout) è indipendente, non è prerequisito delle altre fasi e si include solo se
rientra negli ID selezionati e congelati.

| Fase | Finding | Dipendenze e risultato |
| --- | --- | --- |
| 1. Concorrenza e supervisione | R06, R07, R08, R09, R14 | Acquisizione esclusiva, ownership lease, arresto verificato e stato terminale coerente. R09 dipende da R06; R08 deve usare il ciclo di vita di R07. |
| 2. Reset | R05, R30 | R05 dipende dalla fase 1; R30 corregge la cancellazione amministrativa contro trigger/FK. Nessuna pulizia con worker vivo; entrambi gli scope con review e recovery verificati. |
| 3. Writer e inversi | R01, R12, R11, R02, R03, R22, R27 | Prima temporanei e inversi durevoli, poi errori e guard al ripristino; copie limitate e rename usano gli stessi confini. |
| 4. Undo e retention | R04, R10 | Fasi 1 e 3: manifest congelato, checkpoint riprendibili e journal protetti fino a riconciliazione. |
| 5. Preflight e formati | R13, R16 | Preflight tipizzato, conflitti al confine API e formati supportati senza tag iniziali. |
| 6. Matching e cache | R17, R18, R19, R20 | Prima classificazione effettiva e codec cache; poi spareggi e conservazione stale. |
| 7. UI e performance | R23, R24, R21, R31 | R23 e R31 indipendenti; R24 sugli stati job corretti. R31 corregge solo l'etichetta manuale e non dipende da R17, che riguarda la classificazione. R21 precede benchmark completo. |
| 8. Build, audit, misure, istruzioni | R25, R26, R29, R28 | Inventario riproducibile e audit coerente; R29 prima di certificare i massimi del nuovo benchmark; istruzioni verificate. |
| 9. Accettazione | R01–R31 | Un SHA e una immagine identificata; gate generici e acceptance specifica completati. |

### Strategia di semplicità

Concentrare le correzioni su quattro confini comuni: accesso protetto ai file,
checkpoint persistenti, proprietà delle lease e serializzazione dei contratti.
Evitare controlli copiati tra servizi, Apply e Undo: le divergenze hanno già
prodotto difetti. Il frontend usa un solo criterio per aggiornare review attive.
Refactoring estesi seguono le regressioni, non precedono la definizione degli
esiti attesi. Non introdurre transazioni globali del filesystem o compatibility
layer privi di uscita.

### Metodo di chiusura per ciascuna unità

1. Collegare requisito normativo, riproduzione e ID R richiesto; verificare le dipendenze.
2. Riprodurre il difetto o aggiungere regressione sul confine errato.
3. Per confini critici, fissare prima esito atteso e semantica di failure.
4. Implementare una correzione minima coerente con i confini architetturali.
5. Verificare positivi, negativi e restart/retry/concorrenza pertinenti.
6. Review indipendente dell'intero perimetro assegnato; riparare gli unsatisfied criteria,
   regressioni introdotte e blocker del confine condiviso interessato, poi riesaminare delta,
   blocker precedenti e criteri coinvolti. Browser solo per comportamento effettivamente
   interessato o acceptance esplicita priva di evidenza adeguata.
7. Per ogni pacchetto congelato, completare acceptance, gate locali applicabili, CI sullo SHA
   esatto e consegna autorizzata. Un secondo pacchetto può procedere solo se era già congelato
   nello stesso batch limitato; in parallelo solo con le regole d'isolamento di AGENTS.md, altrimenti
   in sequenza dopo la consegna del primo. Nessuna selezione o sostituzione dopo chiusura/blocco.
8. Registrare la chiusura solo con acceptance, review e ricevuta di consegna. La verifica
   interna è terminale solo per un task internal-only esplicitamente autorizzato; altrimenti
   resta progresso o, se la consegna è impedita, `BLOCKED`. Conservare diagnosi ed evidenze
   storiche senza ricreare righe nella matrix.

## Evidenze di chiusura: R06, R07, R08, R09, R14

Implementazione verificata su SHA `b3b950513efedd2c2456cfb1372f913fd35b7314` (`fix(jobs): fence leases and supervise cancellation-safe execution`). Questo checkpoint precede le note di chiusura; i gate qui registrati sono stati rieseguiti dopo l'aggiornamento del ledger.

**Gate comuni:** `uv run ruff check src tests` PASS; `uv run mypy src` PASS, 190 file; `uv run lint-imports` PASS, 4 contratti; `uv run pytest -q --cov=muzilla --cov-report=term-missing` PASS, 1.422 passed/8 skipped; `cd e2e && npm run test` PASS, 70 passed. Build Linux/ARM64 e runtime native POSIX verificati sull'immagine `sha256:80c6b971650a28c8a27ccad2a3539837353d79a34ab626ed860957d9ab8b21b3`; schema DB/migrazioni e contratto OpenAPI non cambiano, e la rigenerazione TypeScript in scratch è identica (`cmp`). Nessuna modifica frontend.

**Review e limiti comuni:** reviewer indipendente `0d7efd4b`, verdetto **OK** (artifact `74df38c2-486d-40cd-bcb8-25ed99a85f5c/review/final.md`); browser tester `a6cb405b`, **PASS limitato** (artifact `final-browser.md` nello stesso output), tre file Apply/Undo, Activity, collisione e navigazione mobile. Il report browser non afferma prove manuali di cancellazione/heartbeat in-flight o delle race R09/R14: quelle condizioni hanno evidenza automatizzata sotto. `flock` è advisory e POSIX sul filesystem locale, non un mutex distribuito multi-host/shared-DB. R04 resta aperto: cancellazione dopo Undo parziale fallisce chiusa con `recovery_required`; retry/convergenza dell'Undo non è dichiarata risolta. Questa chiusura di cinque ID non certifica la produzione; gli altri finding restano aperti.

### Evidenza R06

**SHA verificato:** `b3b950513efedd2c2456cfb1372f913fd35b7314`. Acquisizione condizionale con ownership/attempt fence: `tests/jobs/test_queue.py::test_competing_sessions_claim_one_selected_candidate` verifica una sola claim fra sessioni indipendenti; `tests/jobs/test_worker.py::test_competing_workers_run_one_handler_once` verifica un solo handler/effetto; `tests/jobs/test_queue.py::test_stale_worker_cannot_renew_or_complete_reclaimed_lease` verifica che il vecchio owner non rinnovi né completi la lease. Tutti inclusi nel gate backend comune: **PASS**.

### Evidenza R07

**SHA verificato:** `b3b950513efedd2c2456cfb1372f913fd35b7314`. `tests/jobs/test_worker.py::test_timeout_joins_thread_owned_session_before_terminal_state` prova session ownership e assenza di stato terminale mentre il thread è vivo; `::test_supervised_sync_work_joins_after_repeated_cancellation` copre cancellazioni ripetute; `::test_quiesce_cancels_and_joins_active_sync_work` e `::test_provider_lease_releases_only_after_supervised_thread_joins` coprono quiesce e risorse; `tests/jobs/test_execution_lock.py::test_process_exit_releases_job_execution_lock` prova il rilascio del lock alla morte del processo. Gate backend comune: **PASS**.

### Evidenza R08

**Riapertura storica (goal `8e40c599`, prima del milestone); verifica interna corrente nel goal `14d2c8ad-fcf8-4f44-b47f-3b0ea56290e4` — INTERNALLY_VERIFIED:** il codice R08 a `b34edd931f861bcfe0992e4d92aa3eae84ae083c` è stato accettato dal parent dopo review indipendente delle riparazioni, run `f30891a1-e481-431e-a1e6-78f085ad2734`: **OK, nessun blocker**. La regressione HTTP confermata riguardava preflight Apply e osservazione Refresh sincroni prima dell’enqueue/promozione. La chiusura e le evidenze storiche seguenti restano conservate; la ricevuta di delivery corrente è riportata sotto.

- **Responsività e supervisione request/session/reset:** i quattro casi `tests/api/test_request_supervision.py::test_request_io_is_responsive_and_joined_before_reset` (Apply/Refresh × richiesta cancellata/non cancellata) erano RED sul baseline `93ebab77d4d0735a4280b0743d6ad4f8a5bd4d5f`, con event loop bloccato fino al watchdog. Sul codice verificato passano: alle barriere reali di preflight/tag read, health e review GET rispondono, heartbeat avanza e cancel viene accettato; cancellazioni ripetute dell’endpoint e del waiter esterno non abbandonano il thread/sessione né rilasciano l’ultima registrazione del MutationGate. Il reset reale attende join/chiusura e vede ApplyRun o revisione Refresh durevoli; Apply conserva 202/ID idempotenti e Refresh restituisce DTO materializzato con intento preservato.
- **Osservazione senza writer reservation e promozione atomica:** i sei casi `::test_refresh_observation_releases_database_and_revalidates_promotion` coprono `heartbeat`, `edit`, `apply`, `path`, `decision`, `promotion_failure`. I primi tre erano RED a `30b9837707ff95b05aa8add10ccc7d70387a7811`: il flush del primo file impediva il rinnovo heartbeat durante la lettura del secondo; Refresh sovrascriveva un edit concorrente; un ApplyRun concorrente consentiva una risposta 200 obsoleta anche con digest identico. Ora la sessione di lettura viene chiusa prima di osservare tutti i file; una sessione nuova acquisisce `BEGIN IMMEDIATE` prima delle riletture di eleggibilità. Heartbeat/cancel restano responsivi su due file; edit, Apply e cambio path concorrenti causano 409 senza scritture parziali Track/source/revisione. Le decisioni rilette vengono preservate e Refresh sequenziali convergono; un errore in promozione annulla anche gli aggiornamenti Track già staged.
- **Prerequisito R07 e worker Apply/Undo:** le prove R07 di timeout, cancellazioni ripetute, quiesce e session ownership e le prove worker Apply/Undo ai checkpoint sicuri descritte sotto restano applicabili e passano sul candidato; nessuna modifica al relativo ciclo di vita. R13 resta fuori scope e invariato.

**Gate locali sul codice verificato:** `uv run pytest -q tests/api/test_request_supervision.py tests/api/test_apply_supervision.py tests/api/test_reset.py tests/services/test_reset.py tests/jobs/test_worker.py tests/services/test_review_apply.py tests/jobs/test_conflicts.py` → **63 passed**; `uv run ruff check src tests` → **PASS**; `uv run mypy src` → **PASS** (191 file); `uv run lint-imports` → **PASS** (4 contratti); `uv run pytest -q --cov=muzilla --cov-report=term-missing` → **1551 passed, 9 skipped, 84% coverage**; `git diff 93ebab77d4d0735a4280b0743d6ad4f8a5bd4d5f HEAD --check` → **PASS**. Migrazioni, OpenAPI e frontend invariati; nessun browser test applicabile al delta backend, coperto dalle prove HTTP deterministiche. **Delivery verificata dal parent — CLOSED:** milestone `f2523444b8d0f799a9abdafcd35b852b0d988a7c`, push normale fast-forward tramite l’upstream configurato `origin/main` da `93ebab77d4d0735a4280b0743d6ad4f8a5bd4d5f`; ref canonico `vertillo/muzilla` `main` verificato allo SHA del milestone. CI hosted [run `39382205646`](https://github.com/vertillo/muzilla/actions/runs/39382205646), head SHA esatto `f2523444b8d0f799a9abdafcd35b852b0d988a7c`, conclusione **success**; tutti i job richiesti **success**: `Backend (lint, typecheck, test)`, `Frontend (lint, typecheck, build)`, `E2E (Playwright)`, `Docker build`. Questa ricevuta riguarda il milestone già consegnato, non il successivo commit documentale di chiusura, il cui push e controllo CI restano separati.

**SHA verificato:** `b3b950513efedd2c2456cfb1372f913fd35b7314`. `tests/api/test_apply_supervision.py::test_multi_file_tag_undo_releases_writer_lock_between_safe_file_checkpoints[2]` e `[3]` usano MP3 codificati di 30 secondi e provano health/read, heartbeat, cancel ai confini sicuri e checkpoint per-file di Undo; `::test_apply_rollback_releases_writer_lock_before_slow_tag_restore` è la regressione deterministica a due worker: Apply tag+move del primo file viene cancellato, il rollback blocca il ripristino tag dopo il checkpoint move durevole, mentre il secondo handler termina e health/job/Activity/cancel restano responsivi; verifica tag/path finali e pool senza sessioni checkout. `::test_apply_and_undo_keep_api_responsive_and_preserve_late_cancel` copre gli esiti API tardivi. Il test di rollback era rosso prima del fix (journal move ancora `done`) e passa dopo. Il browser PASS è limitato ai flussi dichiarati sopra; le race sono dimostrate dai test automatizzati. Nessuna modifica ai controlli di drift; R02/R03 non sono chiusi. Gate backend ed E2E comuni: **PASS**.

### Evidenza R09

**SHA verificato:** `b3b950513efedd2c2456cfb1372f913fd35b7314`. `tests/jobs/test_worker.py::test_restarted_process_recovers_lease_after_expiry_without_second_restart` copre restart prima della scadenza e reclaim successivo senza un secondo restart; `tests/jobs/test_queue.py::test_recovery_does_not_reclaim_expired_job_while_execution_lock_is_held` e `::test_concurrent_recovery_sessions_reconcile_an_expired_job_once` coprono worker vivo e recovery concorrente. `::test_recovery_preserves_finalized_file_run_after_process_exit` copre process exit dopo finalizzazione; `::test_recovery_preserves_durable_apply_commit`, `::test_recovery_preserves_durable_undo_commit` e `::test_recovery_marks_uncertain_apply_for_explicit_recovery` verificano rispettivamente outcome Apply/Undo durevoli ed esito incerto fail-closed. `::test_recovery_preserves_apply_and_fails_closed_for_uncertain_undo` controlla preservazione e fail-closed Undo. Gate backend comune: **PASS**; immagine Linux/ARM64 e probe POSIX sopra: **PASS**.

### Evidenza R14

**SHA verificato:** `b3b950513efedd2c2456cfb1372f913fd35b7314`. `tests/jobs/test_queue.py::test_committed_apply_wins_late_cancel_and_preserves_result` prova che un commit Apply prevale sulla cancellazione successiva e conserva il risultato; `tests/jobs/test_worker.py::test_durable_apply_result_wins_timeout_after_thread_join` copre timeout/interruzione dopo commit durevole; `tests/api/test_apply_supervision.py::test_apply_and_undo_keep_api_responsive_and_preserve_late_cancel` verifica l'Apply/Undo API, gli eventi di stato e Activity dopo cancellazione tardiva. `tests/jobs/test_queue.py::test_recovery_preserves_finalized_file_run_after_process_exit` e i test Apply/Undo durevoli sopra provano la recovery dopo processo terminato. Gate backend ed E2E comuni: **PASS**. Nessuna race manuale browser è dichiarata.

## Evidenze di chiusura: R05, R30, R01, R12, R11, R02, R03, R22, R27

### Candidato, verifiche comuni e limiti

Il candidato dei nove finding è il checkpoint locale `6b3c743e1c4c9b230bdcd6deb64ee1353b4cb060` (HEAD alla chiusura di questo aggiornamento). La fingerprint verificata del contenuto dei 30 file autorizzati è `9a153c178880dafc58eb66ec1b62fe13a9cb877ebea691bcc528d32ff0ba23b1`; lo stesso valore è stato calcolato prima dello staging, sugli oggetti staged e sui 30 file del commit, escludendo `.pi/`. Immagine runtime esatta `sha256:0f6a07c2145165355a881be5a5e44644fb904fdef6eceb8d59744ad9b1886960` (`linux/arm64`); fingerprint dei 328 input runtime `d508b3f25ac90e82e8d925367615d97ec77720a6072b0bc392ddcb62003c1730`.

Gate sul candidato: `uv run ruff check src tests` PASS; `uv run mypy src` PASS (190 file); `uv run lint-imports` PASS (4 contratti, zero rotti); `uv run pytest -q --cov=muzilla --cov-report=term-missing` PASS (1.516 passed, 6 skipped); frontend lint/typecheck/test/build PASS (135 test); E2E Playwright PASS (70 test); `uv run pytest -q -rs tests/changes/test_writer_safety.py` PASS (73 su macOS); test workflow `tests/workflows/test_release_workflows.py` PASS (11). Sull'immagine esatta: writer safety 64 passed/9 skipped perché il filesystem Linux è case-sensitive; suite blobstore/grouping/path/cancellation 61 passed; Compose reset smoke, runtime non-root/native e volume case-sensitive PASS. La review indipendente conclusiva ha verdetto **OK su tutti e nove** (`last-r12-confirmation.md`); il riesame ha verificato in particolare il nuovo percorso del blob catalogo R12.

Browser acceptance PASS sui flussi UI Apply/Undo, rename case-only e rifiuto fail-closed del source drift, e su entrambi i reset UI. Per la ripetizione dei reset, immagine e fingerprint sopra erano in esecuzione su una rete interna `internal=true`, senza route di egress o seconda rete; una connessione TCP di controllo ha restituito `ENETUNREACH`. Solo un proxy locale con upstream fisso verso l'app pubblicava `127.0.0.1:55040`; tutti i provider erano disabilitati. Il reset Catalog eseguito dalla UI ha preservato settings, secret gestito, sessione, musica e backup; dopo reseed diretto e verificato delle fixture disposable, il Factory reset UI ha cancellato settings/secret, revocato la sessione e lasciato intatti i volumi musica/backup. Le risposte reset erano HTTP 200 e gli assert sul database/secret/sessione sono passati. La sorgente musicale e il backup sentinel hanno conservato i rispettivi SHA-256 `3aeb5442b1588879f399f8e2a71252cc47fc64a222ee49a28c74abc5ae9656b7` e `ebc2d291b13db46ee9ff4055170a363ac427c0d54d1f6664910d19caaa6aee3d`.

Per il flusso Apply/Undo, UI e Catalog hanno verificato percorso e tag originali, attributi e hash del payload MPEG; Mutagen ha serializzato nuovamente i byte ID3, quindi l'hash dell'intero file dopo Undo non era quello pristine. L'acceptance richiede qui il ripristino semantico, dei frame audio e degli attributi, non una serializzazione ID3 byte-identica. Le nove prove filesystem case-insensitive saltate su Linux sono coperte dal run macOS senza skip. Non sono state simulate perdita di alimentazione o restore di un file audio da 2 GiB; le garanzie R01 non si estendono a manipolazioni ostili da root/stesso UID o a descrittori esterni già aperti. Questi limiti non sono dichiarati risolti. R31 resta aperto e visibile; non viene contato tra i nove ID chiusi. Altri finding restano aperti, quindi ciò non costituisce dichiarazione di readiness/produzione.

### Evidenza R05

Quiesce e recovery delle lease verificate da `tests/api/test_reset.py::test_api_quiesce_recovers_expired_external_lease_but_waits_for_active_lease`, `tests/services/test_reset.py::test_reset_waits_for_external_leases_to_be_terminal_before_database_or_storage_delete` e test di acknowledgement in `tests/jobs/test_cancellation_deterministic.py`. Una lease attiva rimane una barriera; una lease scaduta è recuperata solo insieme all'acquisizione del lock di esecuzione; la pulizia aspetta la terminazione confermata. I due scope UI di reset hanno inoltre superato il repeat no-egress sull'immagine esatta; nessun dato/blob/secret viene rimosso mentre il worker attivo può usarlo.

### Evidenza R30

I test su database migrato in `tests/services/test_reset.py` coprono scope Catalog e Factory con review ready/applied/undone, revisioni successive, run/journal, secret, epoch, preservazione dei checksum e fault DB/retry/recovery in nuovo processo. Il Compose smoke esatto ha passato fault injection, riavvio e replay. Il repeat browser no-egress ha poi eseguito entrambi gli scope via Settings UI su fixture disposable: Catalog ha preservato settings, secret, sessione e volumi; dopo reseed, Factory ha eliminato settings/secret, incrementato l'auth epoch, revocato la sessione, mantenuto audit/idempotency e preservato musica/backup. Non sono state alterate le protezioni delle review ordinarie.

### Evidenza R01

Il writer condiviso Apply/rollback/Undo usa staging privato per-operazione, quarantine e pubblicazione no-clobber con identità/ancestry verificate e checkpoint journalizzati. I regressions in `tests/changes/test_writer_safety.py` verificano symlink/sentinel preesistenti, sostituzioni concorrenti, collisioni durante pubblicazione/compensazione, cleanup di soli entry posseduti e crash/restart nei checkpoint di Apply, rollback e Undo. La suite passa su macOS e sull'immagine Linux esatta; il reviewer ha verificato che gli entry estranei e il contenuto trattenuto restano disponibili quando la recovery deve fallire chiusa.

### Evidenza R12

I blob nuovi e riusati sono verificati per hash/size e durevoli prima del checkpoint che li rende necessari. In particolare `BlobStore.get_durable_bytes()` stabilisce file fsync e fsync della catena di directory per il precedente blob-art di catalogo prima di retain e persistenza del journal inverso. `tests/changes/test_writer_safety.py::test_apply_does_not_mutate_music_when_catalog_art_fsync_fails` usa bytes di catalogo distinti dall'art embedded e verifica failure prima di mutare musica/catalogo; `::test_apply_fsyncs_catalog_art_before_retain_and_journal_checkpoint` verifica l'ordine file+directory fsync → retain → checkpoint. I test blobstore verificano anche riuso, corruzione e errori di pubblicazione. Il reviewer ha confermato che il defect precedentemente aperto è risolto; nessun power-loss fisico è simulato.

### Evidenza R11

`test_native_art_collection_and_catalog_identity_survive_inverse` confronta la collezione fisica completa (più immagini, bytes, ordine e attributi) e l'identità artwork del catalogo sui fixture MP3, FLAC, M4A, Ogg e Opus, sia per Undo sia per rollback. I test di errore di cattura/storage verificano che Apply non muti la traccia quando l'inverso non è disponibile. `tags/writer.py` mantiene distinti lo snapshot fisico completo e il blob cover del catalogo. Run macOS e immagine esatta PASS; il reviewer ha ricontrollato il percorso R12 che rende durevole il blob catalogo necessario all'inverso.

### Evidenza R02

In caso di errore incerto dopo una sostituzione, il journal resta recuperabile e l'esito del bundle espone recovery; non viene restituito un semplice `failed` che nasconde una mutazione. `tests/changes/test_writer_safety.py` inietta errori su directory-fsync post-replace, reread, checkpoint DB e rollback sessione, quindi riapre l'app/processo e verifica recovery. `test_post_replace_directory_fsync_failure_is_recovery_required_after_restart` e `test_post_replace_database_or_reread_failure_recovers_after_restart` passano, insieme ai regressions di rollback. Writer safety, backend ed E2E PASS.

### Evidenza R03

I guard fisici verificano contenuto corrente, inode/percorso, contenimento e antenati prima degli effetti inversi; drift esterno non viene sovrascritto da Undo o rollback. `test_undo_fails_closed_on_physical_file_drift`, `test_undo_preserves_drift_after_tagged_file_was_moved` e `test_rollback_fails_closed_on_external_artwork_drift` passano, così come i casi di file/move history mancante. La regression Apply→Undo di correzione solo grouping ripristina gruppo e pin senza scrivere musica, mantenendo il relativo preflight valido. Suite completa writer safety, grouping e backend PASS.

### Evidenza R22

Il ripristino copia l'audio a blocchi a memoria limitata e preserva payload MPEG, mode e timestamp. Il probe isolato ha verificato MP3 320-kbit/s stereo da 30 secondi (1.203.923 byte, RSS peak growth 2.670.592 byte) e da 1.200 secondi (48.050.658 byte, RSS growth 3.506.176 byte): il file è cresciuto circa 40×, RSS è rimasto sotto il cap 24 MiB e il delta è circa 0,8 MiB. `tests/changes/test_restore_rss.py` automatizza misure incrementali, hash del payload e attributi; non si dichiara una prova da 2 GiB.

### Evidenza R27

Rename solo casing Apply e Undo mantiene la grafia esatta, inode e bytes; hard link e collisioni reali continuano a essere rifiutati. `tests/changes/test_writer_safety.py` verifica directory entries, journal dei due hop e recovery dopo crash/restart; volume Linux case-sensitive dell'immagine ha passato `Track.mp3` → `track.mp3` e rifiuto hard-link; macOS ha superato i test case-insensitive senza skip. Il flow UI case-only ha passato Apply e Undo sul candidato. Reviewer **OK**.

## Evidenze di chiusura: R25 e R26

### Evidenza R25

**Revisione dell'implementazione:** `f8e9a658e5dbc13d08a9d413ffea497bebd380be`. CI, release, sviluppo e sandbox usano uv `0.11.29` e `uv sync --locked` con Python 3.12 e gli extra previsti; Docker installa dal lock e verifica il sync con `--locked --check`. L'immagine runtime registra lock e inventario effettivamente installato. `uv lock --check` e i 16 test `tests/workflows/test_python_dependency_lock.py` + `tests/workflows/test_release_workflows.py` sono PASS. La regressione di lock stantio fa fallire la build con `The lockfile at uv.lock needs to be updated, but --locked was provided` (`/tmp/muzilla-r25-stale-docker-build.log`); non c'è risoluzione silenziosa.

Build CI Linux/amd64 per lo stesso SHA: immagine config `sha256:86f7bfb8eff512b0a766dbf77935dee3a8b8dffbe0e585b1fff041c2b87ff0c4`, manifest `sha256:e9ab2bb3e7bddd06cbf8b9178feccebc8d98033389bb2bd7ac284580788ac85c`. I quattro test runtime/build-guard PASS; audit eseguito sull'inventario estratto da `muzilla:ci`, Compose readiness/hardening, backup/restore e reset/recovery PASS. La run esatta [37372627343](https://github.com/vertillo/muzilla/actions/runs/37372627343), attempt 2, ha conclusione `success`; API dei job: backend `111985972341`, frontend `111985972383`, E2E `111985972761`, Docker `111985972825`, tutti SUCCESS. Il risultato E2E è 70/70. Reviewer indipendente `4d270358` ha approvato il baseline con note; il reviewer fresco `3d1693bf` ha dato OK finale alla correzione atime limitata.

### Evidenza R26

**Revisione dell'implementazione:** `f8e9a658e5dbc13d08a9d413ffea497bebd380be`. `uv.lock` congela `urllib3==2.8.0`, versione corretta rispetto ai tre advisory registrati nel finding; `uv lock --check` e i 16 test workflow sopra PASS. `pip-audit` sulla lista estratta dall'immagine CI Linux/amd64 riporta **No known vulnerabilities found**; non sono stati soppressi advisory. I contratti provider e le funzionalità runtime continuano a passare: backend CI 1514 passed/16 skipped, suite locale completa 1521 passed/9 skipped, test runtime/native/build-guard immagine 4/4 e Compose readiness, backup/restore e reset/recovery PASS; Playwright hosted 70/70 e locale 70/70, con fixture mock e senza provider live. I revisori indipendenti sono `4d270358` (baseline, OK con note) e `3d1693bf` (delta atime, OK).

Il confronto macOS/arm64 (51 pacchetti) vs immagine Linux/arm64 (52) ha una sola differenza attesa: `greenlet==3.5.4`. Il marker della dipendenza SQLAlchemy seleziona greenlet per Linux aarch64/x86_64, non macOS arm64; il target CI Linux/amd64 usa il marker Linux e non presenta questa differenza. Non è un advisory e non è stata modificata la dipendenza per nasconderlo. Sull'immagine locale esatta sono passati `rsgain` con fixture come uid 1000 e `fpcalc` help/analisi funzionale via Compose; l'immagine CI Linux/amd64 è stata costruita e verificata sulla stessa revisione.

**Gate dopo questo aggiornamento del ledger:** la run verde verifica l'implementazione `f8e9a658e5dbc13d08a9d413ffea497bebd380be`, prima della modifica documentale. Il commit finale che includerà questo aggiornamento dovrà avere una nuova run esatta con tutti e quattro i job PASS prima di `goal_complete`; lo SHA futuro non è ancora noto e non viene anticipato qui. Questa modifica chiude solo R25/R26, senza cambiare gli altri finding o dichiarare readiness/produzione.

## Evidenze della review iniziale

| Verifica | Esito |
| --- | --- |
| `.venv/bin/ruff check src tests` | PASS |
| `.venv/bin/mypy src` | PASS, 188 file |
| `.venv/bin/lint-imports --no-cache` | PASS, quattro contratti |
| `.venv/bin/pytest -q --cov=muzilla --cov-report=term-missing` | 1.391 passed, 8 skipped, 3 warning; 83%; 464,65 s |
| Frontend `npm run lint`, `npm run typecheck` | PASS; lint con warning |
| Frontend `npm run test -- --run` | 135 passed, 22 file |
| Frontend `npm run build` | PASS, copia temporanea del candidato |
| Export OpenAPI e openapi-typescript | Rigenerazione/confronto senza differenze |
| E2E esistenti `npm run test` | 70 passed, 4,2 minuti, copia temporanea |
| Due riproduzioni browser aggiuntive | Entrambe FAIL, confermano R23/R24 |
| `npm audit --omit=dev --json` | Nessuna vulnerabilità runtime segnalata |
| Audit Python dell'export runtime congelato di uv.lock | FAIL, tre advisory urllib3 2.7.0 |
| Stato Git e `git diff --check` prima della persistenza | Worktree pulito, nessuna modifica al prodotto |

I primi tentativi browser e audit npm erano limitati dal sandbox; il riesame
con accesso ai processi locali/rete necessario ha prodotto gli esiti riportati.
I fallimenti di ambiente non sono classificati come difetti del prodotto.

Le riproduzioni usano filesystem e DB temporanei. Il file sentinella dei test
di contenimento è esterno alla libreria della fixture, ma sempre all'interno
dello scratch temporaneo. Nessun percorso musicale personale è stato usato.
Per R12/R22 l'evidenza iniziale è strutturale: non sono dichiarati test di power
loss o misure RSS su file molto grandi mai eseguiti.

## Verifiche Docker successive: esiti e riesame

Il rapporto è stato salvato prima di eseguire questi gate. Le verifiche successive
usano il commit iniziale, una copia locale pulita del candidato, la stessa immagine
immutabile e solo librerie/volumi temporanei. Dettagli, comandi, metriche, inventario,
hash e limiti sono nell’[appendice Docker](code-review-2026-10-04-docker.md).

Immagine: `sha256:785e3a1eb31195d7474622e5a13362ee42a3cea99057c3d4d4dbf31154639061`.

| Verifica | Esito |
| --- | --- |
| Build candidata e identità source/image | PASS; revision/tree/build-context registrati |
| Runtime rsgain, shared libraries, UID 1000 | PASS, due test |
| Build negativa senza libreria TagLib | PASS: la build incompleta viene rifiutata |
| Compose readiness/capabilities/hardening/persistenza | PASS |
| Reset catalogo senza review, musica intatta | PASS nel Compose smoke |
| Cold backup/restore /data, checksum e destinazione non vuota | PASS nel backup smoke; limiti nell’appendice |
| Benchmark 100.000, seed 0, soglie originali | Harness PASS, zero threshold failures; limite di misura R29 |
| fpcalc/rsgain funzionali sulla stessa immagine | PASS: 20 fingerprint e 10 righe ReplayGain |
| SPA Apply/Undo su bundle di 10 tracce | PASS, tag applicati e ripristinati verificati |
| Auth, CSRF/origin negativi, scan/review, Apply UI, restart, Undo UI | PASS nella prova aggiuntiva |
| Factory reset **con review persistita** | **FAIL**: 500, retry 500, mutazioni 503, restart exit 3; R30 |
| Reset catalogo **con review persistita** | **FAIL**, stessi effetti su una fixture nuova; R30 |
| Audit Python sull’inventario dell’immagine | PASS, nessuna vulnerabilità nota segnalata; urllib3 2.8.0 |
| Confronto inventario immagine vs. lockfile | FAIL di riproducibilità: 30 versioni differenti; R25 |
| OpenAPI dell’immagine vs. sorgente revisionata | Schemi e route API uguali; sola route SPA aggiuntiva nell’immagine |

Cold scan: 124,51 s, 803,1 tracce/s; grouping: 52,01 s. Ricerca p95 54,6 ms,
filtri/facets 183,4 ms; Apply p95 1.249,7 ms e Undo p95 948,1 ms.
Valore di memoria pubblicato dal harness: 1.169,4 MiB su limite di 2.048 MiB,
con la limitazione R29. Non sono state rilassate soglie o modificati codice,
test o contratti del candidato per ottenere questi esiti.

Nel riesame storico di quell'immagine, R01–R28 restavano aperti; quel riesame
rafforzava R25 con il confronto effettivo, distingueva R26 tra lockfile e runtime,
e aggiungeva R29/P2 e R30/P1. Quel candidato storico **restava non approvabile
per produzione**: i percorsi positivi e il PASS del benchmark non eliminavano le
riproduzioni negative già confermate. Le chiusure successive sono documentate
separatamente nella sezione sopra.
La prova completa dei reset fallisce ora anche nell’immagine distribuita.

## Evidenze di chiusura: R04 e R10

**Chiusura provvisoria e perimetro:** il candidato implementativo degli 11 file ha fingerprint di contenuto `ead7490b947f737c99c7b71589c20c7679457ac7b7afdeacc7c13e254f096684`, verificata su `HEAD` baseline `7a250428fb82492dd2db7bbedbb7cd8fd8b3c892`; `.pi/sandbox.json` è preesistente, non incluso né modificato. La prima sequenza completa dei gate locali sull'implementazione è PASS. Dopo questo aggiornamento del ledger verrà ripetuta la sequenza sul candidato codice+ledger esatto; gli esiti di quella verifica finale e l'eventuale CI esatta restano nell'handoff durevole, senza un altro edit al ledger. Non è una dichiarazione di completamento complessivo, readiness o produzione; gli altri stati del ledger restano invariati.

**Gate locali sulla revisione implementativa:** `uv sync --locked --extra dev --extra audio` PASS (99 risolti, 93 verificati); `uv run ruff check src tests` PASS; `uv run mypy src` PASS (191 file); `uv run lint-imports` PASS (4 contratti, 0 rotti); `uv run pytest -q --cov=muzilla --cov-report=term-missing` PASS (1.541 passed, 9 skipped, 3 warning); frontend `npm run lint` PASS con warning preesistenti, `npm run typecheck` PASS, `npm run test` PASS (137 test/22 file), `npm run build` PASS; `cd e2e && npm run test` PASS (70/70). **Piano di verifica finale:** il candidato codice+ledger viene verificato secondo il piano delle ricevute esterne nell'handoff durevole; tali ricevute registrano fingerprint e risultati finali senza richiedere un successivo edit del ledger. Nessun provider live, schema/migrazione DB o contratto OpenAPI è stato modificato. Nessun benchmark 100k, immagine completa, CI, commit o push è dichiarato.

### Evidenza R04

L'Undo congela l'intento inverso in `services/review_undo.py::_manifest_for_inverses`; i checkpoint di esecuzione per step/file restano separati e validati in `changes/bundle_undo.py::_manifest_files` e `::_execution`. `tests/services/test_atomic_review.py::test_two_file_tag_move_undo_retries_after_cancel_at_complete_file_boundary` e `::test_undo_retry_skips_files_verified_restored_before_cancellation`; `tests/changes/test_writer_safety.py::test_undo_cancellation_waits_for_complete_file_inverse` coprono cancellazione su file completi, retry senza replay dei checkpoint verificati e limiti del confine per-file. Il residuo iniziale di crash su mossa è provato da `tests/services/test_atomic_review.py::test_initial_undo_move_step_crash_reopens_and_retries_remaining_tags`: un processo figlio termina con `os._exit(74)` dopo il checkpoint durevole della mossa, il nuovo processo riconcilia senza riprodurre la mossa, poi Retry esplicito completa solo gli step rimanenti. Il test verifica TrackMeta, payload audio, path, attributi e risultati per-file durevoli. `tests/changes/test_writer_safety.py::test_undo_fails_closed_on_physical_file_drift` e `::test_undo_preserves_drift_after_tagged_file_was_moved` verificano drift/mancanza di evidenza senza seconda mutazione; `tests/services/test_atomic_review.py::test_different_key_retry_reuses_active_job_across_sessions` verifica deduplicazione del retry tra sessioni.

In `frontend/src/pages/Jobs.tsx` solo `state === "undone"` conta come ripristino; i test `frontend/src/pages/Jobs.test.tsx` coprono l'esito parziale uno-ripristinato/uno-fallito e la distinzione fra failed/pending/skipped/unknown. Il browser autenticato e indipendente (report `outputs/9e6f7fb8-3cb6-44e0-9304-438ff0352010/browser/undo-checkpoints.md`) ha verificato Apply→Undo ordinario e Retry esplicito di un caso parziale su fixture prodotta dallo scan: Activity è passata da 1 ripristinato/1 fallito a 2 ripristinati; il file già completato ha mantenuto inode e hash, e Review/Activity hanno conservato l'esito per-file. Nel follow-up TRCK=0, scan e inverse contenevano il valore corretto, tutti i 48 campi TrackMeta erano coerenti, e Undo ha ripristinato TRCK=0 e semantica originale. Il precedente caso negativo usava una riga Track seminata senza `track_no`/`track_total`; non dimostra perdita con dati canonici da scan. È stata osservata una rappresentazione raw aggiuntiva `TCMP=0` (false compilation); nessun frame originale è stato rimosso e nessuna differenza semantica di TrackMeta è stata riscontrata.

Reviewer indipendente `6798b744`: **R04 OK**, bounded delta OK, stesso fingerprint (report `outputs/221b8dff-d87e-4be4-aa81-e4626faa90e1/review/undo-checkpoints.md`). Il browser PASS è limitato ai flussi descritti; non copre ogni confine di crash in browser, che è coperto dai regressions backend. Il riesame non certifica l'intero prodotto.

### Evidenza R10

`pipeline/retention.py::_undo_run_needs_journals` protegge i run pending/undoing e i failure con esito cancellato, retryable o recovery; `sweep_apply_journals` acquisisce `begin_sqlite_write_transaction` prima delle letture di protezione e mantiene la reservation attraverso la selezione/deletion nello stesso commit del chiamante. `tests/api/test_retention_undo_expiry.py::test_undo_expired_by_age_fails_closed_and_shows_expiry_in_detail` e `::test_undo_expired_by_count_fails_closed_and_shows_expiry_in_detail` coprono entrambe le soglie e l'errore visibile fail-closed. `::test_retention_protects_journals_for_unresolved_undo` copre pending, undoing e failed con recovery per soglia temporale e numerica; `::test_retention_preserves_recovery_required_journals` copre la protezione di recovery. `::test_retention_and_undo_enqueue_are_serialized_across_sessions` e `::test_retention_and_explicit_retry_are_serialized_across_sessions` verificano entrambi gli ordini della reservation tra sessioni distinte.

`tests/changes/test_art_replacement_undo.py::test_art_replacement_captures_original_and_undo_restores` copre blob inverse assente/corrotto, restart con DB e blob store riaperti, retention per età e conteggio durante recovery, contenuto blob durevole e Retry esplicito. A riconciliazione definitiva, `apply_review_undo_run` persiste `state="undone"`; la predicate `_undo_run_needs_journals` non protegge gli stati terminali diversi da failure retryable/recovery, quindi la sweep successiva può applicare le soglie. I test d'età/conteggio dimostrano la prune path non protetta; non viene dichiarato un test end-to-end dedicato della transizione `undone` seguita da sweep.

Reviewer indipendente `6798b744`: **R10 OK**, bounded delta OK. La browser acceptance non testa retention: le soglie, restart, blobs e race sono coperte dai test backend deterministici sopra. Le evidenze non certificano immagini Docker, gate 100k o readiness di produzione; la CI del futuro SHA esatto è in attesa e non è anticipata.

## Evidenze di chiusura: R31 (provvisoria)

### Evidenza R31

**Perimetro e criterio della chiusura.** È soddisfatta soltanto l'acceptance specifica R31 del label manuale; questa chiusura è **provvisoria**, sul contenuto identificato qui sotto, in attesa del candidato finale, della CI sull'esatto SHA e della consegna. Non dichiara completato il goal complessivo né modifica lo stato di altri finding. Il blocco R31 sopra conserva intatti la diagnosi, l'intervento tracking-only e i criteri originali registrati all'apertura.

**Candidato e mappa dei criteri.** Le due sole modifiche R31 rispetto all'export combinato congelato sono `frontend/src/pages/ReviewDetail.tsx` (SHA-256 `7048e1ba36b79eefa0e0251202e9424ebb428507ae000b017c7b734f388b9a6e`) e `frontend/src/pages/ReviewDetail.test.tsx` (SHA-256 `55a56ae7e477cffe1b5dd510046d0e3cc320b08420b780d9f68e577fe506198c`). L'export sorgente a 758 file ha tree SHA-256 `64a93d51787c53bb4d33f037116e05fd6d520394a95992f21f6a19c672301de8`; l'output statico servito, 10 file, ha tree SHA-256 `1a46b2681211cad7b373f889e100982a6a875478989ca0a5f7f928259a0f157f`. Il lock frontend è invariato (`85f5fd3743f9e1a91c053b2efdd8e63f52d5dc9ac6432c5062ab035877155593`). I manifest post-run hanno verificato tutti i 758 file sorgente e i 10 statici senza mismatch; il modulo app servito proviene dall'export congelato. Il JS servito `/assets/index-BuR2pipy.js` ha restituito HTTP 200, 449.168 byte, SHA-256 `bf1ae78b3eab2b11acaceab89c1eee31157e0db2a7c881de266b0e375798445f`.

- **Manuale senza score:** `candidate_source: manual`, `confidence_band: manual`, `confidence: null` mostra `Manual selection`, non `Strong`. La regressione UI riproduceva il label `Strong` sulla baseline (33 test preesistenti pass, caso manuale fail); il focused file passa 34/34 dopo il fix.
- **Confidence numerica preservata:** `0.92`, `0.72` e `0.4` mostrano rispettivamente `Strong`, `Ambiguous` e `Low`; la regressione copre questi tre valori.
- **Stati skipped e no-candidate preservati:** il caso skipped mostra `Skipped — Leave unchanged` prima del ramo manuale; no-candidate mantiene `Needs attention`. Il test copre entrambi.
- **Browser acceptance reale:** sullo stesso export, sei detail request autenticati `GET /api/reviews/{1..6}` hanno restituito HTTP 200; gli heading accessibili `h2` e i badge mostrano rispettivamente `Manual selection`, `Strong`, `Ambiguous`, `Low`, `Skipped — Leave unchanged`, `Needs attention`. Desktop 1280×900 non ha overflow; manuale e skipped lungo sono stati verificati nel viewport CSS effettivo 375px, skipped anche a 390px, senza clipping, wrapping, frammentazione del testo o overflow orizzontale. Tutte le richieste browser erano loopback, i sei provider erano offline/disabilitati e le sei risposte avevano zero ApplyRun, UndoRun e TaskAttempt. Le misure DOM/accessibilità sono durevoli nel report browser; screenshot richiesti erano inline/ephemeral e non salvati su disco.

**Gate, review e ricevute.** Sull'export congelato Node `v22.23.1`, npm `10.9.8`, lock invariato e CI convention `npm ci --legacy-peer-deps` (242 package): `npm run lint` PASS (warning preesistenti non pertinenti), `npm run typecheck` PASS, `npm run test -- --run` PASS (23 file, 162 test), `npm run build` PASS. Log esatti e red/green focused output sono in `/Users/asant/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/c79735b3-9f73-4711-8ee6-690820af0d4a/r31/evidence/`; il loro archivio `SHA256SUMS` (fingerprint `a8505baa097be1d39ffd775111766a716227539760e87db3861c24985eeb0676`) verifica tutti i file archiviati. Reviewer indipendente fresco `6e42b54f` ha dato **OK, no issues**; report `/Users/asant/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/6e42b54f-0555-48b0-8497-fefd7a070eb3/r31/manual-confidence-review.md`, SHA-256 `adc05ca591b10e5001be4f4e8313fe0759d7a09a3b5fcaf035b258ee65dc2fd9`. Browser report `/Users/asant/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/a00e9b81-0d5b-4f4a-bc73-f9a8ede634aa/r31/browser-acceptance.md`, SHA-256 `5666481bbe9c31e4f333cf0a42c00dd7c9473d9388b86f351f3f6eca459233d6` (verdetto PASS). Non sono state ripetute suite, review o acceptance browser.

**Eccezione browser registrata, non occultata.** Per il fixture discarded ID 5, `GET /api/reviews/5/neighbors` ha restituito 404 e il browser ha registrato la corrispondente failure resource; il detail GET diretto ha restituito 200 e badge/stato sono corretti. È l'esito atteso e non bloccante per un elemento fuori dall'inbox attiva; la richiesta non è stata soppressa né l'errore nascosto. Nel diagnostic read-only i sei payload `ReviewBundleDetailOut` serializzati coincidevano esattamente con i response fixture; startup retention sweep aveva zero righe eliminate e i conteggi ApplyRun, UndoRun, TaskAttempt, journal e provider-cache erano zero. L'ultimo startup mobile ha registrato un ulteriore sweep; il file SQLite grezzo è cambiato per startup/WAL bookkeeping. Dopo quell'ultimo lease non è stato ripetuto l'aggregate SQL diagnostic; i detail GET finali per ID 1 e 5 hanno comunque restituito gli stati attesi. Il processo CLI è stato fermato cooperativamente; PID/start identity manager e CLI assenti, PGID posseduto vuoto e TCP connect al loopback ha restituito `ECONNREFUSED` (61). Il bind-only probe non è stato usato come prova di teardown perché il connect-refusal e l'enumerazione libproc forniscono la prova positiva. Questi limiti restano documentati nel report browser.

**Applicabilità, residui e perimetro.** R31 cambia solo il testo di un badge: non cambia route, browser state, interazione, file flow, API/schema OpenAPI, database/migrazioni, backend o native tools. Perciò un ulteriore E2E generico non è applicabile alla sola acceptance R31. Gli obblighi E2E più ampi di R13/R15 restano pendenti e i relativi finding restano aperti; non sono chiusi da questa prova. Il backend 1.984-test proof del parent non è stato rieseguito per R31; nessun backend gate, provider o runtime aggiuntivo è richiesto da questo label-only delta. Il delta documentale presente è limitato al ledger: i gate frontend restano applicabili perché sorgenti, test, lock, export statico e ambiente non sono cambiati dopo le verifiche; l'audit hash post-ledger è registrato nell'handoff R31 gestito. Nessuna modifica agli altri finding, a `completion-matrix.md`, readiness, AGENTS o `.pi`.

**Stato di consegna.** La chiusura R31 rimane esplicitamente provvisoria fino alla CI verde sullo SHA finale esatto e alla consegna autorizzata. HEAD/upstream sono ancora `ab3cf7fe13166d92e10beed2e5b1ec0773aafb0f`, index vuoto; il candidato R31 è contenuto di worktree e non ha un commit SHA finale. Nessuno stage, commit o push è incluso in questa chiusura.

## Verifica interna corrente — contenuto non consegnato

Questa sezione registra prove interne recenti e residui, non una nuova chiusura o
dichiarazione di readiness. Il candidato resta contenuto di worktree non committato, basato su HEAD
`ac6cfa88f253f9e395c1847742870133b7c59889`; i report descrivono gate e confini distinti,
non un unico SHA certificato. Base delle ricevute durevoli esterne al repository:
`/Users/asant/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/`.

- **Gate backend corrente:** `14909e9c-e8b6-4476-a685-881e1c81a5d3/verification/current-backend-gates.171c3b95.f1fb7b2e.md`
  registra una singola invocazione completa `uv run pytest -q --cov=muzilla --cov-report=term-missing -rs`:
  **2283 passed, 15 skipped, 17 warnings, 900.13 s, 85%**; `uv run ruff check src tests` PASS.
  I 15 skip restano skip di host-native, piattaforma/permessi e immagine non impostata.
  Sync locked, mypy (198 file) e lint-imports (4 contratti) sono riuso esplicito su input invariati;
  non sono stati rieseguiti. Il run precedente `1 failed, 2281 passed, 15 skipped` resta storico,
  non si combina col nuovo per dichiarare un gate composito verde.
- **R23, sola integrazione interna (prova storica):** `30daae9a-6072-437f-9871-cf6ba8c4fcf3/r23/root-integration.md`
  registra la riproduzione root prima della modifica (history length 3 invece di 2), focused 59/59,
  frontend 189 test/24 file e lint/typecheck/build PASS. La review indipendente
  `1a8de526-a072-4e18-ba04-0e2008fd314f/r23/root-integration-requirements-review.md` dà OK
  all'implementazione limitata ma lascia l'acceptance bloccata. Browser Back/Forward, unload,
  keyboard/focus, pointer e E2E restavano pendenti in quella verifica; R23 risultava aperto all'epoca.
- **R29, edit e prova runtime limitata:** profilo/review sorgente in
  `c7f741a8-5a05-4e21-bdcf-216844e56af4/r29/` e report corrente
  `4048c214-1ba5-4ddd-ac7a-ccaac8dd8c95/r29/owned-resource-proof.1f91da9b.ff5abd25.95570802.5af18ede.e0ccbf1a.47b70d4e.74ae74c5.13beb6f8.2bd64648.0c69d62c.0f443a7f.2d292276.cb305040.3b8ec52f.md`
  documentano serializzazione one-shot byte-equivalente senza alterare deadline, limiti o sticky latch,
  regressione focused (1 pass e suite coperta 221 pass/1 skip), hook/manifest correnti e review OK.
  La prova Linux limitata verifica factory/migrazione/query, fixture reali,
  retirement prima della successiva release (app/fixture 10/10, picco CUB 20), ACK e adoption
  sconosciuta fail-closed. Non è il workflow completo a 34 invocazioni, certificazione universale
  fairness/RSS/FD/HWM o immagine finale; R29 resta aperto.
- **Immagine e inventario:** `0865a237-a5a0-4ce8-9cd6-546df674248b/image/current-integrated-candidate.md`
  identifica immagine Linux/arm64 `sha256:a77d68aeb08c62d0a3068aab4e8d615741d68978dd037f77c47cb7a7805eeab1`,
  manifest Docker SHA-256 `4b721984e5b83211e89e857c4ea60d9f178d89c086c106c16f9d6cb0d29ebed0`
  e asset frontend JS/CSS correnti. Help PID 1 è solo `muzilla serve --help`, non startup/lifespan.
  La prova native funzionale precedente è riusata solo per il confine invariato, non rieseguita
  integralmente. Il delta `current-integrated-candidate.f2612db3.68aca089.md` registra
  `pip-audit 2.10.1` sull'inventario esatto (52/52 dipendenze, nessun advisory segnalato, exit 0,
  snapshot PyPI 2026-10-19 07:47:12–15 UTC); la review
  `e4ad69d0-e0df-468d-86ca-8f37ffd49f25/image/current-integrated-requirements-review.25aad221.md`
  approva solo il perimetro immagine/audit/riuso. Non è audit OS/native/npm né readiness complessiva;
  R25/R26 conservano le righe e la storia già presenti.
- **Gate e prerequisiti aperti:** CI dell'esatto SHA finale, browser/E2E, Compose/full-service/lifespan,
  benchmark completo 100k, prove risorse/fairness/HWM, recovery/backup/reset, generated contracts e
  readiness cumulativa restano da verificare; i green sopra non li implicano. I processi host
  precedentemente tracciati (PID 70636 e 70804, parent 70783/70782) risultavano ancora vivi
  all'ultimo riscontro del 19 ottobre ~07:12; non sono stati terminati né avviati nuovi listener.
  I prerequisiti espliciti R18/R20 (R17/R19 e CI milestone prima dell'implementazione) non sono
  soddisfatti o riordinati qui; R24 non assorbe dipendenze di job. Nessun finding estraneo è incluso.

Le prove storiche sopra non cambiano gli stati, diagnosi, criteri o storia degli altri finding:
R08 resta riaperto e R31 conserva il testo storico “Chiuso (provvisorio)”. R23 è l'unico stato
aggiornato, nella sezione dedicata, a `CLOSED`, sulla base della consegna e della CI esatta
registrate sotto. Nessun altro finding o prerequisito cambia stato. La readiness complessiva e i
gate di rilascio ancora aperti restano indipendenti da questa chiusura. In questo aggiornamento non
sono stati modificati codice, test o contratti; l'edit del solo ledger non richiede gate
backend/frontend/browser artificiali.

### Chiarificazione di policy (2026-10-19)

La nota storica sopra sui prerequisiti R18/R20 (R17/R19 e CI milestone prima
dell'implementazione) registra la condizione applicata allora e resta invariata. La
condizione procedurale di CI per-prerequisito è superata esclusivamente quando R18/R20
e i relativi prerequisiti fanno parte dello stesso pacchetto coerente di consegna:
R17 e R19 devono prima soddisfare tutti i propri criteri tecnici e le prove
consumer/provider d'integrazione pertinenti, e tutti i criteri di R18/R20 restano
obbligatori prima della milestone del pacchetto. In tale pacchetto non occorre CI
separata per ID. Altrimenti i pacchetti prerequisito vanno consegnati e chiusi prima
di aprire il pacchetto dipendente. Questa chiarificazione non attesta evidenza attuale,
non cambia gli stati o i criteri, né riduce R21 (workflow a 100.000 tracce), R29
(riesecuzione completa del workflow con le soglie originali) o alcuna condizione di
rilascio.

## Evidenza R23 — chiuso e consegnato

**Criterio congelato.** Rimuovere le entry di guardia spurie e verificare destinazioni reali di
Back/Forward, URL e filtri, Save/Restare/Scartare e modifiche ripetute.

**Identità del contenuto.** Base dichiarata della verifica isolata: Git `HEAD`
`51ed96177c26b2b5c861c6fa75b5d45e799add14`. Il candidato verificato è lo snapshot non-Git
`/private/tmp/r23-candidate.Mk4nJH`, identificato dal manifest
`/private/tmp/r23-candidate-manifest.json`; SHA-256 della patch (fingerprint della patch, non un
commit SHA): `39f3ef6975cd038dc590956ae3aecdfd28ea96757c7ed870df0adb79f8cab0eb`. La milestone
codice consegnata è il commit `399f66d2ef141db8a3c9ad31ed6f1f9e1fc87f07`, con i cinque file
R23 codice/test e il ledger nella revisione di integrazione; il successivo aggiornamento del ledger
registra separatamente la chiusura senza modificare i cinque blob R23 di codice/test.

| File del candidato | SHA-256 |
| --- | --- |
| `frontend/src/App.tsx` | `c4716e94ee188c377531aee9d54a55ff368698ea8e0c21630b3ff588c42da6b7` |
| `frontend/src/App.test.tsx` | `877e107233bf4e77bbae95b9f2c71cdd384e223a311b2b39bd02bf3eda38c169` |
| `frontend/src/pages/ReviewDetail.tsx` | `8e0fcb7e8e9c3905985131f8f23906dd021f0032d697dc8e00a448bf2199a02e` |
| `frontend/src/pages/ReviewDetail.test.tsx` | `20f4ee8ccaa1507bade51b4791f9929d62cac513379c2eca49fff25eab58f65c` |
| `e2e/tests/review-bulk-edge.spec.ts` | `cc0b21010d181624e0aa259b910b5477e1c9fdece8a993527273c556f32b7e72` |

**Mappa criterio→evidenza.**

| Criterio | Evidenza |
| --- | --- |
| Nessuna entry history spurie; Back/Forward raggiungono la destinazione reale | `ReviewDetail.tsx` usa il blocco integrato con il router; `ReviewDetail.test.tsx` verifica history length e destinazioni effettivamente renderizzate attraverso salvataggi ripetuti. |
| Save, Stay, discard e modifiche ripetute preservano transizioni/URL | I test browser `review-bulk-edge.spec.ts` coprono Back/Forward dopo Save, ripetizioni Stay e discard della transizione richiesta una sola volta; la prova di dirty Back copre keyboard/focus e Stay/Discard. |
| URL, filtro e destinazione di ritorno restano corretti | Il test browser del protocollo router verifica origini filtrate, URL e storia sostitutiva per Close/neighbor; le regressioni unit verificano query/hash/state e URL di ritorno ostili. |
| Nessun salvataggio implicito; un Save fallito mantiene la protezione | Le regressioni unit/browser verificano che Stay/discard non salvino il draft e che il fallimento mantenga editor dirty e protezione unload. |

**Gate frontend sul candidato isolato** (`/private/tmp/r23-candidate.Mk4nJH/frontend`,
Node 22.23.1, npm 10.9.8, Vite 8.1.5): `npm run lint` PASS; `npm run typecheck` PASS;
`npm run test -- --run` PASS (23 file, 162 test); `npm run build` PASS. Log:
`/private/tmp/r23-final-gates.rN8YaR/`.

**E2E browser isolato.** Preflight con `PYTHONPATH=/private/tmp/r23-candidate.Mk4nJH/src`
ha asserito `muzilla.__file__=/private/tmp/r23-candidate.Mk4nJH/src/muzilla/__init__.py`.
Comando completo:

```sh
cd /private/tmp/r23-candidate.Mk4nJH/e2e && \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/private/tmp/r23-candidate.Mk4nJH/src \
TMPDIR=/private/tmp/r23-isolated-e2e.ztUlL2/tmp \
npm_config_cache=/private/tmp/r23-isolated-e2e.ztUlL2/npm-cache \
npm run test -- --output=/private/tmp/r23-isolated-e2e.ztUlL2/playwright-results
```

Esito: Playwright 1.62.0, Chromium 151.0.7922.34, 73/73 pass, un worker, 4,5 minuti.
Tutti e quattro i casi R23 sono passati; nessun `pageError` o `failedRequest`. Un'unica riga
console 422 (Unprocessable Entity) è quella attesa del Save deliberatamente non valido nel test
unload; nessun altro errore browser. Log: `/private/tmp/r23-isolated-e2e.ztUlL2/playwright.log`.

**Review indipendente.** `/Users/asant/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/a5636cc4-94ef-448e-a76d-c9a5a4710cf6/r23/candidate-requirements-review.md`:
verdetto **OK with notes**, nessun blocker source-level; il reviewer ha verificato i cinque hash
sopra e la mappa dei criteri. La nota E2E pendente nel report precede il run isolato qui registrato.

**Consegna e CI sullo SHA esatto.** `git push origin main` è riuscito. `git rev-parse HEAD`,
`git rev-parse @{u}` e `git ls-remote origin refs/heads/main` confermano tutti
`399f66d2ef141db8a3c9ad31ed6f1f9e1fc87f07`; la configurazione del remote non è stata modificata.
`gh run list --repo vertillo/muzilla --commit 399f66d2ef141db8a3c9ad31ed6f1f9e1fc87f07`
restituisce solo la run più recente **39344883924**, [CI GitHub](https://github.com/vertillo/muzilla/actions/runs/39344883924),
con SHA esatto `399f66d2ef141db8a3c9ad31ed6f1f9e1fc87f07`, conclusione success. Tutti i job richiesti
sono completati con successo:

| Job | ID job | Esito |
| --- | ---: | --- |
| Backend (lint, typecheck, test) | `118374990524` | Success |
| Frontend (lint, typecheck, build) | `118374990210` | Success |
| E2E (Playwright) | `118380230481` | Success |
| Docker build | `118380230384` | Success |

GitHub ha mostrato l'avviso di trasferimento del repository e Git ha stampato una diagnostica
credential-store; il push e le verifiche HEAD/upstream/remote sono comunque riusciti, senza
cambiare la configurazione.

**Stato:** criteri tecnici, review indipendente, CI esatta e consegna del code milestone soddisfatti;
R23 è `CLOSED`. La patch e lo snapshot isolato identificano il contenuto verificato e non sono
commit SHA. La ricevuta sopra riguarda la milestone di codice; questa annotazione documentale è
un follow-up senza modifiche ai cinque blob R23. La chiusura vale solo per R23: non
certifica readiness o rilascio complessivi; gli altri finding aperti e i gate cumulativi descritti in
Condizione di rilascio restano applicabili.

## Evidenza R15

**Criteri e comportamento.** `POST /api/auth/logout` resta pubblico per consentire la pulizia locale
idempotente. La dipendenza same-origin respinge Origin mancante/estraneo prima di qualsiasi
`Set-Cookie`; cookie assente, malformato, scaduto o già revocato, e auth disabilitata, non avanzano
l'epoch globale. Solo una sessione verificata, non revocata e accompagnata da CSRF legato al cookie
può revocarlo globalmente. `Secure` e `SameSite=Lax` continuano a corrispondere agli attributi di
login. I controlli condivisi same-origin/CSRF delle altre mutazioni restano invariati.

| Criterio | Evidenza |
| --- | --- |
| Logout anonimo/cross-origin non revoca altre sessioni e non cancella cookie cross-site | RED pre-fix: il test atteso 403 ha osservato HTTP 200. Dopo il fix il test API verifica 403, assenza di `Set-Cookie` e validità del cookie catturato; la regressione E2E invia una vera top-level form da `evil.example`. |
| Logout anonimo same-origin pulisce localmente senza epoch bump; invalid/revoked restano locali | Test API dedicati verificano cookie captured ancora valido dopo logout anonimo, cleanup di cookie invalido/revocato senza invalidare una sessione successiva e `Set-Cookie` di eliminazione locale. |
| Logout autenticato richiede CSRF session-bound e revoca il cookie catturato | Test API coprono CSRF mancante (403, nessun cookie rimosso) e logout valido; il cookie precedentemente catturato riceve 401 dopo la revoca globale. |
| Auth disabilitata non revoca globalmente | Test API verifica cleanup same-origin, risposta coerente con auth disabilitata e epoch invariato. |
| Attacco browser top-level cross-site fallisce senza perdere la sessione | `e2e/tests/auth.spec.ts`: Chromium invia `Origin: http://evil.example`; cookie `SameSite=Lax` assente nella POST; risposta 403 senza `Set-Cookie`; valore cookie invariato e catalogo ancora autenticato dopo la navigazione di ritorno. |

**Candidato e manifest dei file sorgente/test.** Baseline `main`/`origin/main`:
`3fe99d7dc3fb4ffbd1ae87685fd5e95ec20e983a`; i quattro file del candidato sono:

| File | SHA-256 |
| --- | --- |
| `src/muzilla/api/routers/auth.py` | `c6784f3c9e0d8c6f5f70d39a1b7dd86cf76b66ec12e8650a1d05c3d41a2639b6` |
| `src/muzilla/api/security.py` | `1640ee0716b86adc2b181ccb179da87596a9b5179204cf5cfc365b1ad9f978cf` |
| `tests/api/test_auth.py` | `e68a76f1f51c690367da531b6f65d844596f81445f125c8adcb5bd45582fc63f` |
| `e2e/tests/auth.spec.ts` | `4b88c559f7cf66d94814ea7a32aa34dd6667f608ddd2da7e49ac2f812362672d` |

La suite auth focused passa 26/26. Sul candidato completo: `uv sync --locked --extra dev --extra
audio`, Ruff, mypy (191 file) e lint-imports (4 contratti) PASS; `uv run pytest -q
--cov=muzilla --cov-report=term-missing -rs`: **1583 passed, 9 skipped, 1 warning, 618.70 s,
84%** (Darwin, Python 3.12.13). Gli skip sono i gate host-native/immagine non impostata già
previsti e non riguardano R15. Frontend lockato: lint/typecheck/build PASS e 168 test; il build
servito dall'E2E è stato generato da quei sorgenti. La OpenAPI route logout continua ad avere
`parameters: []`; nessun tipo o schema generato richiede aggiornamento. `cd e2e && npm run test`:
**74/74 pass** con Chromium 151.0.7922.34, inclusa la regressione cross-site sopra.

**Review indipendente.** Reviewer Sol High, run `98ca8f90-96cd-4f8c-b713-7cb6d015b873`,
verdetto **OK — nessun blocker R15**; effort HIGH verificato dai metadati runtime e confermato
al reviewer via supervisor. Il report ha esaminato i criteri, i caller auth/CSRF/epoch e le
regressioni; non ha eseguito test o browser. L'esito browser deriva dalla suite E2E reale riportata
sopra, non dalla review.

**Consegna e CI sul commit del codice.** Il commit `77d4cae22f46690706ef18caf5aba83591d5e471`
è stato pushato in fast-forward al `origin/main` canonico da `3fe99d7dc3fb4ffbd1ae87685fd5e95ec20e983a`.
La run GitHub [39840173328](https://github.com/vertillo/muzilla/actions/runs/39840173328), attempt 2,
ha `conclusion=success` e `head_sha` esattamente uguale al commit del codice. Tutti i job richiesti
sono `completed/success`:

| Job | ID | Esito |
| --- | ---: | --- |
| Backend (lint, typecheck, test) | `120050748019` | Success |
| Frontend (lint, typecheck, build) | `120050751019` | Success |
| E2E (Playwright) | `120077517121` | Success |
| Docker build | `120077517167` | Success |

**Stato:** R15 è `Chiuso` sul commit del codice sopra. Questa registrazione è un follow-up
solo documentale; la CI esatta sullo SHA che la contiene deve ancora completare prima della fine del
Goal. La sua ricevuta resterà nella CI/sessione, senza un ulteriore commit creato solo per citare
la propria SHA.

## Evidenza R17

**Criterio e comportamento.** Le soglie di rifiuto effettive album/singleton governano ranking e classificazione anche nella scelta manuale. Ricerca e import manuale condividono le stesse funzioni di scoring, metadati locali (incluso il nome file) e impostazioni persistite. I rejected non compaiono tra le scelte, non sono importabili con `force` via ID o URL e non alterano la revisione.

| Criterio | Evidenza |
| --- | --- |
| Confini strong/reject sotto, uguale e sopra; configurazione album e singleton | `tests/matching/test_engine.py::test_decision_applies_strong_and_reject_threshold_boundaries`, `test_album_reject_threshold_uses_matching_config` e `test_singleton_reject_threshold_uses_matching_config`. |
| Soglie persistite ed effettive nella scelta manuale | `tests/api/test_matching.py::test_persisted_reject_threshold_hides_and_blocks_forced_manual_selection` (track e group); verifica PUT/GET delle impostazioni, ricerca nascosta, import forzato per ID/URL rifiutato e revisione invariata. |
| Parità tra ricerca/import e protezione contro falsi rifiuti | `score_group_candidates` e `score_track_candidates` in `pipeline/matching.py` sono condivise da `manual_search.py`; `test_manual_candidate_import_uses_filename_matching_evidence` dimostra un import valido basato sul nome file. |
| Esperienza manuale | `e2e/tests/manual_search.spec.ts` rende rejected un candidato con soglia persistita e verifica “No candidates” e zero pulsanti “Use this result”; E2E completo 74/74 pass. |

**Contenuto del codice consegnato.** Commit `ccea5a6359b4456e485e7984feab5d7f262b42ae`, discendente diretto del baseline `73d54c9d6cc736e6fc5ee49f0111659e02b21445`, pushato in fast-forward al repository canonico `vertillo/muzilla`, `refs/heads/main`. `git push` exit 0; `git ls-remote` pre/post push exit 0, prima ha restituito il baseline e dopo lo SHA candidato. Le modifiche locali estranee sono rimaste fuori dal commit.

**Verifiche locali.** `uv sync --locked --extra dev --extra audio`, `ruff check src tests`, `mypy src`, `lint-imports`, build frontend, `git diff --check` e suite matching/API/servizi/handler/impostazioni (69 test): PASS. Il pytest backend completo ha riportato 1588 passed, 9 skipped, 1 failed: `tests/api/test_providers.py::test_get_providers_status_reflects_recorded_rate_limit`. La stessa failure è stata riprodotta dal baseline pulito `73d54c9d6cc736e6fc5ee49f0111659e02b21445`, verificando che il modulo importato provenisse dall’archivio baseline; il test e i suoi moduli provider non sono cambiati in R17. Non è stata estesa la scope per correggere questa failure preesistente. Il job backend CI sullo SHA consegnato è verde.

**Review indipendente.** Pass finale R17 (2/2): **PASS**, nessun difetto concreto residuo nel blast radius; la review ha verificato la correzione ai due blocker di ricerca/import manuale. Test ed E2E sono prove separate dalla review.

**CI sullo SHA del codice.** GitHub [run 39934281518](https://github.com/vertillo/muzilla/actions/runs/39934281518), attempt 1, evento `push`, `head_sha` esattamente `ccea5a6359b4456e485e7984feab5d7f262b42ae`, conclusion success. Job obbligatori `completed/success`: Backend `120349872554`, Frontend `120349872916`, E2E `120355554734`, Docker `120355554895`.

**Stato:** R17 è `CLOSED` sul commit del codice e sulla CI esatta sopra. Questa nota è un follow-up documentale; la sua CI esatta sarà verificata prima di iniziare R19 e la relativa ricevuta resterà nella sessione/GitHub senza un ulteriore commit auto-referenziale. R19 non è ancora iniziato.

## Evidenza R19

**Criteri e comportamento.** Entrambi i gateway restituiscono `ReleaseCandidate | None`;
il decoder condiviso richiede source/ref persistiti coerenti e rifiuta campi normalizzati
malformati senza fabbricare identità o convertire valori. I payload non validi sono cache miss:
online il provider viene interrogato, offline non si fa rete, e un errore provider non viene
sostituito dal dato stale invalido. I candidati validi mantengono representative track, track
list completa, ID provider, artwork, raw e provenance.

| Criterio | Evidenza |
| --- | --- |
| Cache fredda/calda, restart, stale-on-error e offline-stale con contenuto/provenance invariati | `tests/providers/test_url_import_cache.py::test_cached_get_track_round_trips_candidate_and_provenance`; round trip su engine/sessione indipendente, poi stale offline e stale-on-error senza seconda chiamata non necessaria. Le prove release URL coprono cache fresh riutilizzata, offline e stale fallback. |
| Payload cache malformati non diventano hit utilizzabili | RED: i 12 casi fallivano prima della correzione. GREEN: `test_invalid_cached_candidate_fails_closed_for_both_gateways` verifica ref mancante/ID ref malformato e album malformato in `get_release`, durata malformata in `get_track`, con refetch online, miss offline senza rete e rilancio dell'errore provider. |
| Import dello stesso candidato Deezer in review distinte | `tests/api/test_matching.py::test_deezer_track_url_cache_imports_same_candidate_in_distinct_reviews`: source/ref e proposte equivalenti, una sola chiamata provider. |
| Chiamanti rispettano il risultato tipizzato e i confini di errore/flush | `services/manual_search.py`, `pipeline/matching.py`, `jobs/handlers/match.py`; `mypy src` PASS. Il reviewer ha verificato propagazione cancellazione/errore e flush senza commit invariati. |

**Candidato e verifiche locali.** Commit codice `b3807e91f63011631f31eaef94ee1bb36919e87a`, discendente diretto del baseline `2e7cb4221f89bb0e67d70c5cdd49e1a0536dc418`, pushato in fast-forward al repository canonico `vertillo/muzilla`, `refs/heads/main`. Il commit contiene solo i file R19 accettati; le modifiche locali estranee sono rimaste fuori. Sul candidato: `ruff check src tests`, `mypy src`, `lint-imports`, diagnostica LSP sui file cambiati (0 finding), `git diff --check`, cache suite mirata (29 pass), backend completo (1603 pass, 9 skip), E2E completo (74/74) e build frontend: PASS. Nessuna modifica a frontend, OpenAPI, schema DB o dipendenze.

**Review indipendente.** Sol High, pass finale 2/2, run `9f4f5cbf-be88-443c-b8e7-4e3e73b9e70e`: **OK with notes**, nessun blocker R19. La review è read-only e non ha eseguito i test; i risultati sopra sono del parent e della CI.

**Consegna e CI sullo SHA del codice.** `git push origin main` exit 0; la diagnostica `fatal: failed to store: 100001` era non bloccante: il processo ha aggiornato il ref, l'helper proviene da `/opt/homebrew/etc/gitconfig` (`osxkeychain`) e il runtime espone un proxy loopback con userinfo, senza stampare credenziali. La diagnosi sandbox già stabilita è coerente; nessun helper o config è stato modificato. `git ls-remote` pre/post push exit 0 e ha restituito rispettivamente baseline e SHA candidato. GitHub [run 39949856238](https://github.com/vertillo/muzilla/actions/runs/39949856238), attempt 1, evento `push`, `head_sha` esattamente `b3807e91f63011631f31eaef94ee1bb36919e87a`, conclusion success. Job obbligatori `completed/success`:

| Job | ID | Esito |
| --- | ---: | --- |
| Frontend (lint, typecheck, build) | `120401389482` | Success |
| Backend (lint, typecheck, test) | `120401389797` | Success |
| E2E (Playwright) | `120409479955` | Success |
| Docker build | `120409480073` | Success |

La run ha emesso annotazioni non bloccanti di lint sul frontend non modificato, indisponibilità del servizio cache GitHub Actions e deprecazione Node 20; tutti i job obbligatori hanno concluso con success.

**Stato:** R19 è `CLOSED` sul commit del codice e sulla CI esatta sopra. Il follow-up documentale `a4f73aff7c9923d211d4a758c0c76bf7efd2c219` è stato verificato da GitHub run `39954879643` sullo SHA esatto, con Backend, Frontend, E2E e Docker `completed/success`; R18 può procedere. La ricevuta è conservata nella run GitHub, senza un commit auto-referenziale ulteriore.

## Evidenza R18

**Criterio e comportamento.** La specifica di prodotto stabilisce che la preferenza provider
sia solo un tie-breaker e non possa superare un match materialmente migliore. La qualità resta
`adjusted_distance` dopo corroborazione; la preferenza modifica l'ordine, non qualità, rifiuto o
confidenza, ed è applicata solo alla zona `min_gap` ancorata alla migliore qualità.

| Criterio | Evidenza |
| --- | --- |
| La preferenza non cambia qualità/rifiuto; il gap zero consente solo il tie-break di candidati a punteggio identico | `tests/matching/test_candidates.py::test_rank_candidates_source_priority_breaks_ties` e `test_rank_candidates_source_preference_does_not_change_rejection`; i punteggi restano invariati e il candidato non cambia stato di rifiuto. |
| Confine incluso, fuori-zona esclusa e provider non configurati | `test_rank_candidates_source_priority_cannot_override_outside_min_gap` (gap 0 con scarto 0.005 e gap 0.03 con scarto 0.035), `test_rank_candidates_source_priority_includes_boundary_but_not_outside` (0.03 incluso, 0.030000002 escluso) e `test_rank_candidates_provider_not_in_priority_order_is_unpenalized`. |
| Corroborazione contribuisce alla qualità, senza incorporare la preferenza provider | `test_rank_candidates_corroboration_lowers_adjusted_distance`; il punteggio dopo corroborazione è invariato dalla preferenza e i segnali restano distinti. |
| Decisione `min_gap` corretta anche con ordinamento provider inverso alla qualità | `tests/matching/test_min_gap.py::test_min_gap_uses_absolute_score_difference_when_order_is_reversed` e `test_provider_tie_only_within_gap`. |
| Post-sort singleton conserva ordine di zona e rimappa gli indici dei duplicati | `test_singleton_provider_order_survives_earliest_release_sort` e `test_singleton_earliest_release_sort_reindexes_duplicate_indices`; entrambe le nuove regressioni erano RED prima del fix. |
| Esiti matching esistenti non vengono alterati dalla vecchia penalità di qualità | Fixture `missing_metadata_duration_is_not_identity.yaml` e `piki_twilight_rejects_kawai.yaml` verificano `adjusted_distance` e stato di conferma aggiornati. |

**Candidato e verifiche locali.** Commit `76d50c5c59859b5e02d9c4a1e1ccef3fdc6a66fb`, figlio del
baseline `a4f73aff7c9923d211d4a758c0c76bf7efd2c219`, consegnato in fast-forward al repository
canonico `vertillo/muzilla`, `refs/heads/main`. La ref locale, `origin/main` e la ref remota
osservata con `git ls-remote` coincidono sullo SHA del commit. Il secondo `git push` è terminato
con exit 0 (`Everything up-to-date`); `git ls-remote` exit 0 ha restituito la stessa ref.

Sul candidato: `uv sync --locked --extra dev --extra audio`, `ruff check src tests`, `mypy src`
(191 file), `lint-imports`, pytest backend completo (1611 pass, 9 skip), frontend lint/typecheck/
168 test/build, E2E Chromium completo (74 pass), diagnostica LSP sulle righe modificate e
`git diff --check`: PASS. Il lint frontend ha emesso soltanto warning esistenti; LSP ha warning
AST-grep preesistenti altrove. Nessuna modifica OpenAPI, schema DB, dipendenze o frontend.

**Review indipendente.** Reviewer Sol Medium, pass mirato 2/2, run
`0a0089f7-fd2b-40e3-8f7b-98cea8d1f298`: **APPROVE**, nessun blocker residuo. La review ha
verificato zona qualità, ordine provider, immutabilità di qualità/rifiuto/confidenza, post-sort
e indici duplicati; non ha eseguito test o verifiche di consegna.

**Consegna e CI sullo SHA del codice.** La push ha aggiornato `main`; l'output includeva
`fatal: failed to store: 100001`, ma il successivo push e `git ls-remote` hanno exit 0 e la ref
remota è esattamente il candidato. `credential.helper` proviene da
`/opt/homebrew/etc/gitconfig` (`osxkeychain`); il runtime imposta l'autenticazione proxy Basic
preemptiva e il trace riassunto ha confermato una chiamata di store dell'helper. Non sono stati
stampati URL autenticati, credenziali o trace grezzi; non è stato dimostrato quale record
l'helper non abbia potuto memorizzare. Nessun helper o configurazione è stato modificato.
GitHub [run 39969799773](https://github.com/vertillo/muzilla/actions/runs/39969799773), attempt 1,
evento `push`, `head_sha` esattamente `76d50c5c59859b5e02d9c4a1e1ccef3fdc6a66fb`, conclusion
success. Job obbligatori `completed/success`:

| Job | ID | Esito |
| --- | ---: | --- |
| Backend (lint, typecheck, test) | `120469144498` | Success |
| Frontend (lint, typecheck, build) | `120469145202` | Success |
| Docker build | `120495263905` | Success |
| E2E (Playwright) | `120495264030` | Success |

La run ha emesso solo annotazioni non bloccanti: warning di lint sul frontend non modificato,
cache GitHub Actions indisponibile e deprecazione Node 20; tutti i job richiesti sono success.

**Stato:** R18 è `CLOSED` sul commit del codice e sulla CI esatta sopra. Il follow-up
`e43fdc44710b8b480ac871f2915ff142bef88974` è pushato su `vertillo/muzilla` `main`; la run
[39980138207](https://github.com/vertillo/muzilla/actions/runs/39980138207) ha lo stesso
`head_sha`, `conclusion=success` e tutti i quattro job obbligatori (`Backend`, `Frontend`,
`Docker build`, `E2E`) completati con successo. `git ls-remote` con exit code 0 ha restituito
`e43fdc44710b8b480ac871f2915ff142bef88974` per `refs/heads/main`, uguale a `HEAD` e
`origin/main`; l'output includeva il warning non bloccante del credential helper `failed to
store: 100001`. La chiusura e la consegna R18 sono quindi complete; solo ora può iniziare R20.

## Condizione di rilascio

Dopo le correzioni, sullo stesso candidato: fault injection e restart ai checkpoint
Apply/rollback/Undo/reset; processi concorrenti, lease, cancellazione e timeout;
E2E completi e nuove regressioni; immagine esatta non-root e native tools;
Compose, persistenza, reset, cold backup/restore isolati; benchmark workflow
100.000 tracce con soglie già fissate e budget 2 GiB; audit del runtime effettivo;
contratti generati in sync e acceptance specifica per ogni finding chiuso.

Una suite ampia e verde non sostituisce queste prove. La completion matrix vuota
non implica rispetto dei contratti di produzione mentre i finding confermati
restano aperti. Nessuna pubblicazione o deployment è parte della review.

## Riconferma acceptance sul main riconciliato — Goal 5b43a9a5 (in corso)

Questa nota registra evidenze di revalidazione, non aggiorna stati o chiude finding. La mappa completa requisito→test→ambiente→artifact per R01–R12, R14, R22, R23, R25–R27, R30–R31 e il controllo procedurale §5 è in `PLAN.md`, sezione **Revalidazione attiva — Goal 5b43a9a5**. Le ricevute dettagliate browser restano nel session artifact `outputs/goal-5b43a9a5-d367/browser-acceptance.md` e nel supplemento `r31-matrix-supplement.md`.

- Baseline `main`/`origin/main`: `2562384eebcd8f85705546d2d85d1630d8b1cffb`; candidate source dirty, index vuoto e nessun conflitto. L’immagine Linux/arm64 esatta è `sha256:d367826774688325cee43831ea3eb380821ef26975f0ef87bfc25592cf53176d`. `AGENTS.md` è invariato; la verifica §5 non ha rilevato gap né autorizza un edit procedurale.
- Prove aggregate sul contenuto corrente: backend `1577 passed, 9 skipped, 3 warnings, 84%`; Ruff/mypy/import-linter pass; frontend lint/typecheck/build e 168 test pass; E2E 73 pass; OpenAPI rigenerato in scratch esterno e diff types vuoto. Le compensazioni Linux/audio/container, package audit e wrapper nativi sono riferite nella mappa del piano.
- R04 ha un vecchio tentativo fallito correttamente fail-closed per mismatch di identità della directory antenata in una fixture container stale; non è stato contato come pass né corretto indebolendo le guardie. Il retry successivo su namespace disposable nuovo ha completato entrambi i file. R27 case-only Apply/Undo è stato eseguito dalla UI; il volume snapshot conserva path/SHA/inode originali.
- R30 reset Catalog e Factory completati dalla UI su fixture separate, con verifica indipendente dei DB snapshot e dei manifest musica/backup; il reset Catalog non lascia dati stale nel client senza reload. R31 matrix corrente: sei stati × desktop/mobile (12 celle) PASS su heading/badge, clipping/overflow e keyboard. I tre `neighbors` 404 del caso skipped e i warning UI sono riportati nel browser artifact; non sono nascosti. Strong Apply non è stato eseguito e non fa parte dell’acceptance R31, che richiede l’etichetta.
- Conteggi storici `1571` e `1577` si riferiscono a run diversi: il run completo più recente è `1577 passed/9 skipped`, coerente con raccolta corrente di 1586. La precedente raccolta baseline `1560` non è una suite eseguita. I report precedenti non vengono sommati o presentati come prova del candidato attuale.
- La precedente review Sol High pass 1/2 era BLOCKED da B1/R30 stale UI e assenza browser evidence. Reviewer Sol High run `2d77e95e-9086-4b24-a362-3a51182e6f67` ha completato il pass finale **2/2: PASS, nessun blocker**; conferma le 20 acceptance, le dipendenze e i nove scenari AGENTS §5. B1 e B2 risultano coperti da fix/cache tests e dai journey browser correnti. Note non bloccanti registrate: per R23 due blob ReviewDetail differiscono dal receipt storico, ma frontend/E2E correnti coprono i criteri; inventory R26 contiene 52 pacchetti shipped e audit ne scansiona 56 includendo quattro compatibility dependencies. La review non ha eseguito test/browser o live Git e non approva delivery/CI. Nessuno stato è cambiato: R08 resta riaperto e R31 mantiene lo stato storico provvisorio; commit/push, CI exact-SHA e uguaglianza ref sono ancora da dimostrare.

## Verifica interna R13

Checkpoint del goal owner-assegnato in sequenza **R13 → R16 → R24**. Tutti e tre i finding sono
`CLOSED` sul candidato di codice `d5b59b4fca28ce3354511971a04f6a7edae3a510`, spinto su
`vertillo/muzilla` `refs/heads/main`; la CI esatta `40070852671` (run 193, attempt 1) ha tutti e
quattro i job richiesti in successo. La ricevuta completa è riportata sotto.

**Prima consegna integrata:** il commit `74cd44067d9f9329b8a35198d89fa12c963a64ee` è stato
spinto con fast-forward a `vertillo/muzilla` `refs/heads/main`; `git ls-remote` ha confermato lo
stesso SHA. CI `40066623434` (`https://github.com/vertillo/muzilla/actions/runs/40066623434`) sullo
SHA esatto: Backend, Frontend ed E2E PASS; Docker FAIL nel reset smoke. Il fixture isolato del reset
creava review applicate con snapshot privi di `tag_hash` e `physical_guard`, ora richiesti dal
preflight R13 fail-closed. La correzione aggiunge al solo fixture disposable la hash letta dal file
e la guardia fisica catturata; non indebolisce il preflight. Ruff, format, parse e `git diff --check`
passano; il candidato `74cd440...` resta uno storico tentativo incompleto per il failure Docker.

**Consegna finale:** commit `d5b59b4fca28ce3354511971a04f6a7edae3a510` spinto con fast-forward su
`vertillo/muzilla` `refs/heads/main`; dopo la CI, `git fetch` e `git ls-remote` hanno confermato
local `main`, `origin/main` e il remote allo stesso SHA. CI `40070852671` (run 193, attempt 1;
[GitHub Actions](https://github.com/vertillo/muzilla/actions/runs/40070852671)) sullo SHA esatto:
Backend, Frontend, E2E e Docker PASS, incluso il reset recovery smoke. Fetch/push hanno anche emesso
`fatal: failed to store: 100001`, ma i comandi Git sono riusciti e il remote è stato verificato;
metadata redatti mostrano proxy loopback con userinfo, helper effettivo `osxkeychain` e il percorso
Git 2.53 che approva proxy credentials dopo HTTP riuscita. Avviso di store Keychain non bloccante;
nessun valore credenziale esposto o configurazione modificata.

**Candidato esaminato:** HEAD `563368105cded5d63d1e8a68e5bf07d0f2bbe06c`, fingerprint
`97b08864f091edcc4c1df1abcb7e3edd4975c5c2b61a16b3d0386adb6a011623` (45 path, index vuoto,
nessuna entry unmerged). Il fingerprint identifica il worktree esaminato; questa ricevuta del solo
ledger è successiva. Reviewer Sol High, pass finale: **OK, nessun blocker concreto R13**; report
`54578c06-ea6d-4578-af28-529d4ba12fc6_reviewer_output.md`.

| Criterio R13 | Evidenza |
| --- | --- |
| Payload ORM letto da un solo reader tipizzato; snapshot malformed/incompleti, target mancanti e revisioni concorrenti senza current revision falliscono chiusi | `tests/db/test_source_snapshots.py`; `tests/api/test_review_preflight.py` (conflitti strutturati prima dell'enqueue); `tests/jobs/test_conflicts.py` (coverage manifest/worker prima del journal). |
| Guard fisico acquisito al preview e conservato in composition, import e refresh/no-result; Apply non adotta una nuova baseline | `test_refresh_preserves_a_new_physical_guard_without_rewriting_history`; `test_refreshed_no_result_review_can_import_candidate_and_apply`; regressioni ID/URL import e `_check_source_precondition`. |
| Source drift/incompatibilità produce 409 strutturato e nessuna enqueue o mutazione; worker e retry riconciliano solo checkpoint durevoli | API competitor incompleto/vuoto/malformato e no-current-revision; worker competitor incompleto, target manifest omessi e no-current-revision; `test_retry_after_successful_rollback_uses_journal_checkpoint`. Le regressioni asseriscono assenza di run/job/attempt/journal e byte musica invariati ove pertinenti. |

**Verifiche sul codice.** La suite R13 collegata aveva dato **272 passed, 2 skipped** prima del delta
minimo per review senza current revision; dopo il delta, le regressioni mirate hanno dato **4 pass API
+ 2 pass worker**. Sul delta: `uv run ruff check src tests`, `uv run mypy src` (192 file) e
`uv run lint-imports` PASS. LSP: 5 file confermati clean, un server silent-on-clean/inconclusivo,
zero diagnostiche riportate; non è registrato come sweep completamente clean.

**Browser acceptance.** `npm run build` da `frontend/` ha generato gli asset correnti in
`src/muzilla/web/static/` (output ignorato dal Git, non riusato da build stale); `cd e2e && npm run
test`: **75/75 PASS** in Chromium 151.0.7922.34, 4,5 minuti, un worker. Il teardown fixture è
passato; il test negativo di salvataggio produce l'unica console 422 attesa, senza page error o
failed request riportate. Build/E2E hanno emesso solo warning non bloccanti (proxy Undici
sperimentale e chunk Vite >500 kB).

**Gate finali:** sul worktree di verifica, `uv sync --locked --extra dev --extra audio`,
`ruff check src tests`, `mypy src` (192 file), `lint-imports` e la suite backend completa sono PASS
(**1667 passed, 9 skipped**); i test frontend hanno dato **169 passed** e la suite E2E completa
sul worktree corrente **81 passed**. La CI sul candidato di codice `d5b59b4fca28ce3354511971a04f6a7edae3a510` ha confermato backend, frontend, E2E e Docker PASS. Nessun criterio residuo per R13, R16 o R24; non è una dichiarazione di readiness complessiva del prodotto.

## Verifica interna R16

R16 è `CLOSED`: criteri tecnici e review indipendente completati; la consegna e la CI esatta sono
registrate nel checkpoint R13.

**Candidato R16 esaminato:** HEAD `563368105cded5d63d1e8a68e5bf07d0f2bbe06c`, fingerprint
`f1acc436c68f9fb6c98689c7296f9f3ab96e77ec8bc8b33c09b230f2de101884`. Il fingerprint include la
sola riformattazione equivalente di una firma preesistente in `tests/tags/test_writer.py`; le
asserzioni e la logica R16 non sono cambiate. Reviewer Sol High, contesto fresh, run
`771e6f0c-c181-4ada-86e0-8751a6b28267`: **OK, nessun blocker concreto R16**; report
`/Users/asant/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/771e6f0c-c181-4ada-86e0-8751a6b28267/review/r16-acceptance.md`.
Il canary obbligatorio ha ricevuto il diniego sandbox previsto; nessuna scrittura o test del reviewer.

| Criterio R16 | Evidenza |
| --- | --- |
| Writer inizializza solo i tag mancanti nei contenitori MP3/WAV/AIFF riconosciuti; i contenitori tagless sconosciuti restano rifiutati | `src/muzilla/tags/writer.py::write_fields`; `tests/tags/test_writer.py::test_write_fields_initializes_missing_tags_for_supported_id3_containers`; `::test_write_fields_does_not_initialize_unknown_tagless_container`. |
| Round-trip ReviewBundle Apply/Undo da fixture inizialmente tagless, con audio preservato e outcome per file | `tests/tags/test_format_support.py::test_reviewed_apply_and_undo_round_trips_for_supported_formats` parametrizzato MP3/WAV/AIFF; `_audio_payload` confronta i byte audio dopo Apply e Undo; journal e risultati per-file verificati. |
| Journey UI reale per Apply e Undo espliciti su ciascun formato, con titolo iniziale assente e outcome per file | `e2e/tests/fixtures.ts::addTaglessAudioFile`; `e2e/tests/apply_undo.spec.ts` (MP3/WAV/AIFF). Le fixture sono copiate nella libreria scratch per-test. |

**Verifiche:** suite mirata `tests/tags/test_format_support.py tests/tags/test_writer.py tests/changes/test_writer_safety.py tests/changes/test_art_replacement_undo.py tests/services/test_atomic_review.py` → **228 passed, 2 skipped**; dopo la riformattazione `tests/tags/test_writer.py` → **93 passed**. Suite backend completa `uv run pytest -q --cov=muzilla --cov-report=term-missing` → **1667 passed, 9 skipped** (Python 3.12.13). `uv sync --locked --extra dev --extra audio`, `uv run ruff check src tests`, `uv run mypy src` (192 file), `uv run lint-imports` (4 contratti) e `git diff --check` → **PASS**. `cd e2e && npm run test -- tests/apply_undo.spec.ts` → **5 passed**, inclusi i tre flussi tagless.

**Diagnostica e residui:** dopo la review, un follow-up solo tipizzato in `tags/writer.py` ha sostituito l’accesso indiretto `mutagen.id3.Frames` con l’import esplicito `Frames` e ha annotato i container dinamici dopo `add_tags()`; i 12 errori Pyright sulle vie ID3/Vorbis sono assenti. Il follow-up non cambia il comportamento del writer; la suite mirata (**228 passed, 2 skipped**) e i gate backend completi (**1667 passed, 9 skipped**, Ruff, mypy su 192 file, import-linter) passano sul worktree corrente. L’errore di complessità Pyright in `scripts/perf_benchmark.py` resta non assegnato e non è un gate CI di questo goal; le altre segnalazioni LSP sono advisory. Nessun residuo R16; la ricevuta di chiusura è nel checkpoint R13.

## Verifica interna R24

R24 è `CLOSED`: criteri tecnici, gate locali frontend/E2E e review indipendente completati. R13 e R16 sono chiusi; la ricevuta di consegna e CI esatta è nel checkpoint R13.

**Candidato R24 esaminato:** HEAD `563368105cded5d63d1e8a68e5bf07d0f2bbe06c`, fingerprint pre-ricevuta `a8619f9d4526500be40dc1149b8e1803348071ef3509d8cea1967e4964002c04` (diff binario tracciato più hash dei file non tracciati; index vuoto, nessun conflitto). Reviewer Sol Medium, contesto fresh, run `37f82631-aae1-48ce-ab1d-f17977f38330`: **OK, nessun blocker concreto R24**; report `/Users/asant/.pi/agent/sessions/--Users-asant-Desktop-muzilla--/subagent-artifacts/outputs/37f82631-aae1-48ce-ab1d-f17977f38330/review/r24-acceptance.md`. Il canary prescritto ha ricevuto il diniego sandbox; Guardrails/path-access non erano applicabili. La review non ha eseguito comandi, test o browser.

| Criterio R24 | Evidenza |
| --- | --- |
| Dettaglio aperto durante la preparazione si aggiorna senza reload | `useReviews.ts::useReview` polla ogni 500 ms con stato bundle `preparing`/`applying`; `ReviewDetail` consuma direttamente la query. `review-polling.spec.ts` verifica `preparing → ready` senza navigazione. |
| Dettaglio aperto durante un retry enrichment si aggiorna e poi termina il polling | Task `pending`/`running` mantengono il poll; il test conserva il tentativo fallito precedente e verifica `In corso → Pronto`. |
| Undo già avviato si aggiorna senza dipendere dal job locale e il poll termina allo stato terminale | Run Undo più recente `pending`/`undoing` mantiene il poll; il test verifica indicatore attivo → `undone` e scomparsa dell’indicatore. Apply run più recente `pending`/`applying` è coperto dalla stessa predicate. |
| Stop del polling quando non rimane lavoro attivo | La callback ritorna `false` quando bundle, task e run sono terminali; tutti e tre gli E2E verificano nessuna GET aggiuntiva per 700 ms, oltre l’intervallo di 500 ms. |

**Verifiche sul candidato:** `cd frontend && npm run lint`, `npm run typecheck`, `npm run build` → PASS (warning preesistenti di lint/chunk, nessuno nuovo R24); `npm run test -- --run` → **169 passed**. `cd e2e && npm run test` → **81 passed** in Chromium 151.0.7922.34, un worker; include i tre scenari R24. La suite E2E completa è stata rieseguita anche sul worktree integrato corrente dopo la sola rimozione di una variabile mock inutilizzata: **81 passed**. La fixture Playwright possiede start/test/teardown nello stesso runner; il test di teardown è passato. Il log contiene il 422 atteso del test negativo di salvataggio R23; nessun page error o requestfailed nei relativi diagnostic hook. Il set backend completo è PASS (**1667 passed, 9 skipped**) sul worktree corrente; R24 non modifica il backend.

**Limiti non bloccanti:** gli E2E R24 usano risposte API intercettate deterministicamente per mantenere gli stati attivi; provano il polling/rendering client e lo stop, non i tempi reali dei worker, coperti dai test di job. Non è stato introdotto un redesign visivo. Nessun criterio residuo R24; la ricevuta sul candidato di codice esatto è registrata nel checkpoint R13.
