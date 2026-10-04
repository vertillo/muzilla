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
la skill di progetto `ui-ux-pro-max`. La richiesta successiva dell'utente
autorizza esplicitamente la persistenza di questo rapporto sotto docs e il suo
aggiornamento dopo i gate Docker. Le verifiche non autorizzano fix del prodotto,
deployment, commit o push. Non sono state utilizzate musica, configurazioni,
credenziali, backup o dati personali come fixture.

Questo documento conserva il rapporto del candidato ed è ora il backlog attivo
per i goal di remediation: R01–R30 sono direttamente le unità implementative.
La completion matrix precedente è stata evasa e non va ripopolata con questi finding.
Il rapporto non sostituisce le specifiche normative o i gate di
[production-readiness.md](production-readiness.md); il workflow dei goal è definito
in [AGENTS.md](../AGENTS.md).

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

Tutti i finding partono aperti; questo aggiornamento documentale non ne risolve nessuno.
Il worker aggiorna solo gli ID verificati dopo acceptance e review indipendente,
con i gate applicabili sul candidato esatto. Conservare le descrizioni e le evidenze
originali sotto: descrivono il candidato storico, non lo stato dei fix successivi.
Per ogni chiusura aggiungere una nota identificata dall'ID con revisione verificata,
evidenza per ogni criterio di accettazione, comandi/esiti, reviewer, browser se
applicabile e rischio residuo. Non basta cambiare lo stato nella tabella.

| ID | Stato | Evidenza di chiusura |
| --- | --- | --- |
| R01 | Aperto | — |
| R02 | Aperto | — |
| R03 | Aperto | — |
| R04 | Aperto | — |
| R05 | Aperto | — |
| R06 | Aperto | — |
| R07 | Aperto | — |
| R08 | Aperto | — |
| R09 | Aperto | — |
| R10 | Aperto | — |
| R11 | Aperto | — |
| R12 | Aperto | — |
| R13 | Aperto | — |
| R14 | Aperto | — |
| R15 | Aperto | — |
| R16 | Aperto | — |
| R17 | Aperto | — |
| R18 | Aperto | — |
| R19 | Aperto | — |
| R20 | Aperto | — |
| R21 | Aperto | — |
| R22 | Aperto | — |
| R23 | Aperto | — |
| R24 | Aperto | — |
| R25 | Aperto | — |
| R26 | Aperto | — |
| R27 | Aperto | — |
| R28 | Aperto | — |
| R29 | Aperto | — |
| R30 | Aperto | — |

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

### R19 — [P2] Restituisci lo stesso contratto dalla cache e dalla rete

**Riferimento:** [providers/cache.py](../src/muzilla/providers/cache.py), riga 398.

`cached_get_track()` restituisce ReleaseCandidate dalla rete e dict dalla cache;
import URL usa `.source` e `.ref`. Confermato il cambio di tipo tra prima e
seconda chiamata: riuso in altra review o offline può fallire.

**Intervento:** codec del contratto provider, preservando rappresentante e provenance.

**Accettazione:** cache fredda/calda/stale, offline e restart; import dello stesso
candidato in review differenti con risultato equivalente.

### R20 — [P2] Separa scadenza di freschezza e cancellazione della cache

**Riferimento:** [pipeline/retention.py](../src/muzilla/pipeline/retention.py), riga 80.

La sweep elimina tutte le entry scadute, anche utilizzabili come stale. Un payload
leggibile da `cache_get_stale()` è scomparso dopo sweep. Avvio e manutenzione
possono quindi eliminare il fallback durante indisponibilità del provider.

**Intervento:** politiche distinte per freschezza e conservazione massima, con
limiti espliciti di crescita.

**Accettazione:** offline e provider indisponibile dopo sweep/restart; dato
conservato utilizzabile, provenance stale visibile e crescita limitata.

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

### R28 — [P3] Allinea README ai comandi e al modello correnti

**Riferimento:** [README](../README.md), riga 214, tabella storage e sezione Undo.

README descrive ancora `muzilla changes apply`, `muzilla changes undo`,
`apply --backup` e retention ChangeSet. CLI attuale non registra questi comandi;
l'operatore può affidarsi a funzionalità inesistenti.

**Intervento:** Apply esclusivamente UI, percorso reale dei backup e retention
ReviewBundle. Documentazione segue il comportamento implementato.

**Accettazione:** esempi confrontati con CLI help e flussi verificati; nessuna
istruzione obsoleta o contraddizione con le specifiche.

## Piano dettagliato di esecuzione

La matrice era vuota al momento della review. I finding R01–R30 sono ora le
unità esplicite dei goal, con Intervention e Acceptance già definite sopra.
Seguire le dipendenze del piano qui sotto: le dipendenze di fase valgono per
ogni finding della fase, oltre alle dipendenze esplicite fra finding. Verificarne
la chiusura nella tabella di stato prima di implementare un ID dipendente.
Non chiudere un finding modificando soltanto la descrizione normativa. Non
implementare dipendenze fuori dal pacchetto autorizzato.
R15 (logout) è indipendente e va incluso nella fase 1 come intervento di sicurezza;
non è un prerequisito aggiuntivo delle altre fasi.

| Fase | Finding | Dipendenze e risultato |
| --- | --- | --- |
| 1. Concorrenza e supervisione | R06, R07, R08, R09, R14 | Acquisizione esclusiva, ownership lease, arresto verificato e stato terminale coerente. R09 dipende da R06; R08 deve usare il ciclo di vita di R07. |
| 2. Reset | R05, R30 | R05 dipende dalla fase 1; R30 corregge la cancellazione amministrativa contro trigger/FK. Nessuna pulizia con worker vivo; entrambi gli scope con review e recovery verificati. |
| 3. Writer e inversi | R01, R12, R11, R02, R03, R22, R27 | Prima temporanei e inversi durevoli, poi errori e guard al ripristino; copie limitate e rename usano gli stessi confini. |
| 4. Undo e retention | R04, R10 | Fasi 1 e 3: manifest congelato, checkpoint riprendibili e journal protetti fino a riconciliazione. |
| 5. Preflight e formati | R13, R16 | Preflight tipizzato, conflitti al confine API e formati supportati senza tag iniziali. |
| 6. Matching e cache | R17, R18, R19, R20 | Prima classificazione effettiva e codec cache; poi spareggi e conservazione stale. |
| 7. UI e performance | R23, R24, R21 | R23 indipendente; R24 sugli stati job corretti. R21 precede benchmark completo. |
| 8. Build, audit, misure, istruzioni | R25, R26, R29, R28 | Inventario riproducibile e audit coerente; R29 prima di certificare i massimi del nuovo benchmark; istruzioni verificate. |
| 9. Accettazione | Tutti | Un SHA e una immagine identificata; gate generici e acceptance specifica completati. |

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
6. Review indipendente di requisiti e implementazione; browser per condizioni visibili.
7. Eseguire i gate sul candidato esatto, conservando evidenza per ogni acceptance.
8. Aggiornare stato ed evidenza di chiusura del finding e la documentazione pertinente
   solo quando l'implementazione è realmente completa; non ricreare righe nella matrix.

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

Il riesame mantiene aperti R01–R28, rafforza R25 con il confronto effettivo,
precisa R26 distinguendo lockfile e runtime, e aggiunge R29/P2 e R30/P1.
Il candidato **resta non approvabile per produzione**: i percorsi positivi e
il PASS del benchmark non eliminano le riproduzioni negative già confermate.
La prova completa dei reset fallisce ora anche nell’immagine distribuita.

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
