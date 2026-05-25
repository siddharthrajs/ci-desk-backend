# ci-desk-backend

FastAPI backend for the CI Desk crude oil trading dashboard.

## Stack

| Layer | Library |
|---|---|
| Web framework | FastAPI + uvicorn |
| Async HTTP | httpx |
| Cache | Redis (redis-py async) |
| Settings | pydantic-settings |
| Scheduler | APScheduler (AsyncIO) |
| Logging | python-json-logger |

## Quick start

```bash
cp .env.example .env          # fill in API keys
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/health` — returns `{"status":"ok",...}` when Redis is up.

Interactive docs at `http://localhost:8000/docs` (dev mode only).

## Project structure

```
app/
  main.py            FastAPI app, lifespan, CORS, router registration
  config.py          pydantic-settings (reads .env)
  core/
    cache.py         Redis client + get/set/delete helpers
    http_client.py   Shared async httpx client (singleton)
    logging.py       JSON structured logging setup
  routers/           One APIRouter per dashboard tab
  services/          One service class per external data source
  models/            Pydantic v2 response schemas
  scheduler/
    setup.py         APScheduler instance + job registration
    jobs.py          Async job functions
tests/
```

## Adding a new data source

1. Implement the service class in `app/services/<name>.py`.
2. Add routes in the relevant `app/routers/<tab>.py`.
3. Register the dependency in the router using `Depends(get_http_client)` and `Depends(get_cache)`.
4. Optionally add a refresh cron job in `app/scheduler/jobs.py` and register it in `setup.py`.

## Environment variables

See `.env.example` for all supported variables.

## Known limitations

### Baker Hughes oil/gas rig count breakdown

`BakerHughesService` sources the **total** U.S. rig count from the FRED proxy
series `RIGTNXUS`. The **oil and gas breakdown** (`oil`, `gas` fields in
`get_rig_count()`) is always `null` because no reliable, stable free endpoint
exists for the split:

- Baker Hughes publishes a weekly Excel spreadsheet, but the download URL and
  sheet structure change without notice and cannot be used as a production
  data source.
- FRED does not carry the oil/gas split separately.

To populate these fields a paid data provider is required (Bloomberg,
Refinitiv, or a direct Baker Hughes data-sharing agreement). The
`available` flag in the response will be `true` as long as FRED is
reachable — downstream consumers must check `oil`/`gas` for `null`
independently.

## Docker

```bash
docker build -t ci-desk-backend .
docker run -p 8000:8000 --env-file .env ci-desk-backend
```
# ci-desk-backend
