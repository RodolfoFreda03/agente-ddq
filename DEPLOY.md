# Pubblicare la demo pubblica su Streamlit Community Cloud

Questa guida pubblica una versione demo separata dell'interfaccia (modalità
`DEMO_PUBLIC`): nessuna email reale, nessuna chiamata al modello, 3 DDQ
già pronte da `demo_data/`. La tua copia di lavoro locale (con Gmail reale,
la tua chiave Gemini, i tuoi dati) resta separata e non viene toccata.

## 0. Prima di iniziare — cosa è già pronto

- `requirements.txt` nella cartella del progetto è già quello che Streamlit
  Cloud userà per installare le dipendenze (non serve crearne un altro).
- `.gitignore` esclude già `.env`, `credentials.json`, `token.json`,
  `data/`, `mailbox/`, `output/`, `venv/` — nessuno di questi finirà su
  GitHub.
- `demo_data/` **non** è escluso: contiene solo dati finti (3 DDQ di
  esempio, note del vault, cache delle risposte del modello) e deve essere
  pubblicato perché la demo funzioni.

## 1. Crea il repository su GitHub (privato)

1. Vai su **https://github.com/new**.
2. Nome repository: ad esempio `agente-ddq` (o quello che preferisci).
3. Seleziona **Private**.
4. Non aggiungere README/.gitignore/licenza da GitHub (li abbiamo già).
5. Clicca **Create repository**.
6. Copia l'indirizzo del repository che GitHub ti mostra, tipo:
   `https://github.com/<tuo-utente>/agente-ddq.git`

## 2. Collega il repository locale e fai il push

Nel terminale, dentro `~/Desktop/agente-ddq`:

```
git remote add origin https://github.com/<tuo-utente>/agente-ddq.git
git branch -M main
git push -u origin main
```

Se richiesto, autenticati con GitHub (username + personal access token, non
la password — GitHub te lo spiega se non l'hai mai fatto).

## 3. Collega il repository su Streamlit Community Cloud

1. Vai su **https://share.streamlit.io** e accedi con il tuo account
   GitHub (gratuito).
2. Clicca **Create app** (o **New app**).
3. Scegli **"Deploy a public app from GitHub"**.
4. Seleziona:
   - **Repository**: `<tuo-utente>/agente-ddq`
   - **Branch**: `main`
   - **Main file path**: `app.py`
5. Clicca su **"Advanced settings"** prima di avviare il deploy (serve per
   il punto 4 qui sotto).

## 4. Imposta `DEMO_PUBLIC=true` nei secrets dell'app

Ancora nelle **Advanced settings** (o, se l'app è già creata, nel menu
**⋮ → Settings → Secrets** della tua app su share.streamlit.io):

1. Nel campo **Secrets**, scrivi (formato TOML, una riga):

   ```toml
   DEMO_PUBLIC = "true"
   ```

2. Salva.

Non serve impostare nient'altro (niente `GEMINI_API_KEY`, niente
`GMAIL_USER`): in modalità demo il modello non viene mai chiamato e Gmail
non viene mai usato, quindi quei secrets non servono a questa app.

## 5. Scegli l'indirizzo pubblico

Nelle stesse impostazioni (o alla creazione dell'app), Streamlit chiede un
**"App URL"**: prova a impostare

```
alken-ddq-agent
```

Se è già occupato da qualcun altro, Streamlit te lo segnala subito e puoi
scegliere la variante più vicina disponibile, ad esempio
`alken-ddq-agent-demo` o `alken-ddq-agent-hult`.

## 6. Avvia il deploy e verifica

1. Clicca **Deploy**. La prima build richiede qualche minuto (installa le
   dipendenze da `requirements.txt`).
2. Quando l'app si apre, controlla:
   - il banner in alto **"Public demo — fictional data, no emails are sent."**
   - 3 DDQ già presenti nella lista (Northgate, Harbourline Excel,
     Harbourline Word)
   - **"Check mailbox"** mostra il messaggio di modalità demo, non prova a
     contattare Gmail
   - apri una DDQ, approva, clicca **"Submit final version to the agent"**,
     scarica il Word con **"Download Word document"**
   - clicca **"Send"** → **"Yes, send"**: deve apparire il messaggio di
     invio disabilitato, mai un invio reale
   - **"Reset demo"** riporta tutto allo stato iniziale

## 7. Il link da mettere nella presentazione

Una volta pubblicata, l'app è raggiungibile su:

```
https://alken-ddq-agent.streamlit.app
```

(o l'indirizzo effettivamente assegnato al punto 5). Il link è pubblico:
chiunque lo apra vede la demo, senza bisogno di credenziali.

## Limite da sapere

La copia dati della demo è condivisa da tutti i visitatori contemporanei
del link pubblico (non è isolata per singola persona): se più persone
usano la demo nello stesso momento, vedono le modifiche l'una dell'altra.
Per una presentazione con un pubblico ristretto non è un problema; il
bottone **"Reset demo"** riporta comunque tutto allo stato pulito in
qualsiasi momento.

## Aggiornare la demo in futuro

Per pubblicare modifiche: `git add`, `git commit`, `git push` sul branch
`main` — Streamlit Community Cloud ridistribuisce l'app automaticamente ad
ogni push.
