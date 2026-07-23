# LME Scraper API

Backend FastAPI para capturar precios públicos diferidos de LME.com dos veces al día.

## Metales configurados

- Plomo: `https://www.lme.com/metals/non-ferrous/lme-lead#Summary`
- Níquel: `https://www.lme.com/metals/non-ferrous/lme-nickel#Summary`
- Cobre: `https://www.lme.com/metals/non-ferrous/lme-copper#Overview`

## Horarios

El scheduler corre en zona `America/Bogota`:

- 07:00
- 14:00

## Instalación

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
```

## Ejecutar

```powershell
py -m uvicorn app.main:app --reload --port 8010
```

## Endpoints

- `GET /api/health`
- `GET /api/lme/prices`
- `GET /api/lme/prices?refresh=1`
- `POST /api/lme/scrape`
- `GET /api/lme/history?metal_key=lme_copper`
- `GET /api/lme/daily-prices`
- `GET /api/lme/runs`

## Base de datos

La base SQLite se guarda por defecto en `data/lme_prices.db`.

- `lme_scrape_runs`: registra cada corrida del scraper.
- `lme_price_snapshots`: guarda cada captura cruda, incluyendo fallos.
- `lme_daily_prices`: guarda el histórico limpio del día a día, una fila por metal y fecha de scraping.

Si se hacen varias capturas el mismo día, `lme_daily_prices` actualiza la fila del metal con el último dato válido y evita duplicados.

## Nota

LME puede mostrar protecciones anti-bot. El backend guarda el último dato válido y registra fallos de captura para auditoría.
