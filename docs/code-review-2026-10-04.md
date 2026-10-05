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
attivo per i goal di remediation: R01–R30 sono direttamente le unità implementative
registrate dalla review iniziale. La completion matrix precedente è stata evasa e
non va ripopolata con questi finding. Durante la successiva acceptance è emerso il
residuo separato R31, registrato in coda senza alterare il conteggio storico di 30
finding o le descrizioni originali. Il rapporto non sostituisce le specifiche
normative o i gate di [production-readiness.md](production-readiness.md); il workflow
dei goal è definito in [AGENTS.md](../AGENTS.md).

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
chiude esclusivamente gli ID con acceptance, gate e review indipendente documentati
sul candidato esatto. Conservare le descrizioni e le evidenze originali: descrivono
il candidato storico, non lo stato dei fix successivi. Per ogni chiusura, l'evidenza
identifica revisione verificata, criteri di accettazione, comandi/esiti, reviewer,
browser se applicabile e rischio residuo; non basta cambiare lo stato nella tabella.

| ID | Stato | Evidenza di chiusura |
| --- | --- | --- |
| R01 | Chiuso | [Evidenza R01](#evidenza-r01) |
| R02 | Chiuso | [Evidenza R02](#evidenza-r02) |
| R03 | Chiuso | [Evidenza R03](#evidenza-r03) |
| R04 | Aperto | — |
| R05 | Chiuso | [Evidenza R05](#evidenza-r05) |
| R06 | Chiuso | [Evidenza R06](#evidenza-r06) |
| R07 | Chiuso | [Evidenza R07](#evidenza-r07) |
| R08 | Chiuso | [Evidenza R08](#evidenza-r08) |
| R09 | Chiuso | [Evidenza R09](#evidenza-r09) |
| R10 | Aperto | — |
| R11 | Chiuso | [Evidenza R11](#evidenza-r11) |
| R12 | Chiuso | [Evidenza R12](#evidenza-r12) |
| R13 | Aperto | — |
| R14 | Chiuso | [Evidenza R14](#evidenza-r14) |
| R15 | Aperto | — |
| R16 | Aperto | — |
| R17 | Aperto | — |
| R18 | Aperto | — |
| R19 | Aperto | — |
| R20 | Aperto | — |
| R21 | Aperto | — |
| R22 | Chiuso | [Evidenza R22](#evidenza-r22) |
| R23 | Aperto | — |
| R24 | Aperto | — |
| R25 | Chiuso | [Evidenza R25](#evidenza-r25) |
| R26 | Chiuso | [Evidenza R26](#evidenza-r26) |
| R27 | Chiuso | [Evidenza R27](#evidenza-r27) |
| R28 | Aperto | — |
| R29 | Aperto | — |
| R30 | Chiuso | [Evidenza R30](#evidenza-r30) |
| R31 | Aperto | — |

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
6. Review indipendente di requisiti e implementazione; browser per condizioni visibili.
7. Eseguire i gate sul candidato esatto, conservando evidenza per ogni acceptance.
8. Aggiornare stato ed evidenza di chiusura del finding e la documentazione pertinente
   solo quando l'implementazione è realmente completa; non ricreare righe nella matrix.

## Evidenze di chiusura: R06, R07, R08, R09, R14

Implementazione verificata su SHA `b3b950513efedd2c2456cfb1372f913fd35b7314` (`fix(jobs): fence leases and supervise cancellation-safe execution`). Questo checkpoint precede le note di chiusura; i gate qui registrati sono stati rieseguiti dopo l'aggiornamento del ledger.

**Gate comuni:** `uv run ruff check src tests` PASS; `uv run mypy src` PASS, 190 file; `uv run lint-imports` PASS, 4 contratti; `uv run pytest -q --cov=muzilla --cov-report=term-missing` PASS, 1.422 passed/8 skipped; `cd e2e && npm run test` PASS, 70 passed. Build Linux/ARM64 e runtime native POSIX verificati sull'immagine `sha256:80c6b971650a28c8a27ccad2a3539837353d79a34ab626ed860957d9ab8b21b3`; schema DB/migrazioni e contratto OpenAPI non cambiano, e la rigenerazione TypeScript in scratch è identica (`cmp`). Nessuna modifica frontend.

**Review e limiti comuni:** reviewer indipendente `0d7efd4b`, verdetto **OK** (artifact `74df38c2-486d-40cd-bcb8-25ed99a85f5c/review/final.md`); browser tester `a6cb405b`, **PASS limitato** (artifact `final-browser.md` nello stesso output), tre file Apply/Undo, Activity, collisione e navigazione mobile. Il report browser non afferma prove manuali di cancellazione/heartbeat in-flight o delle race R09/R14: quelle condizioni hanno evidenza automatizzata sotto. `flock` è advisory e POSIX sul filesystem locale, non un mutex distribuito multi-host/shared-DB. R04 resta aperto: cancellazione dopo Undo parziale fallisce chiusa con `recovery_required`; retry/convergenza dell'Undo non è dichiarata risolta. Questa chiusura di cinque ID non certifica la produzione; gli altri finding restano aperti.

### Evidenza R06

**SHA verificato:** `b3b950513efedd2c2456cfb1372f913fd35b7314`. Acquisizione condizionale con ownership/attempt fence: `tests/jobs/test_queue.py::test_competing_sessions_claim_one_selected_candidate` verifica una sola claim fra sessioni indipendenti; `tests/jobs/test_worker.py::test_competing_workers_run_one_handler_once` verifica un solo handler/effetto; `tests/jobs/test_queue.py::test_stale_worker_cannot_renew_or_complete_reclaimed_lease` verifica che il vecchio owner non rinnovi né completi la lease. Tutti inclusi nel gate backend comune: **PASS**.

### Evidenza R07

**SHA verificato:** `b3b950513efedd2c2456cfb1372f913fd35b7314`. `tests/jobs/test_worker.py::test_timeout_joins_thread_owned_session_before_terminal_state` prova session ownership e assenza di stato terminale mentre il thread è vivo; `::test_supervised_sync_work_joins_after_repeated_cancellation` copre cancellazioni ripetute; `::test_quiesce_cancels_and_joins_active_sync_work` e `::test_provider_lease_releases_only_after_supervised_thread_joins` coprono quiesce e risorse; `tests/jobs/test_execution_lock.py::test_process_exit_releases_job_execution_lock` prova il rilascio del lock alla morte del processo. Gate backend comune: **PASS**.

### Evidenza R08

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
