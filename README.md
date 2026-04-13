# caddy-translator

`caddy-translator` is a small Dockerized FastAPI utility for trusted internal homelab use. It accepts a CSV upload or URL, parses the rows with pandas, renders a Caddyfile preview, writes the generated output to `/app/output/Caddyfile.generated`, and can optionally deploy that file into a real Caddy target path.

## Features

- HTML form and JSON API
- CSV upload or server-side URL fetch
- Google Sheets share-link support via CSV export conversion
- Row normalization, validation, and helpful error messages
- Preview of the generated Caddyfile in the browser
- Optional deployment with guarded target checks
- Optional `caddy validate` and `caddy reload` via `docker exec`
- Tests for translation, URL loading, and deployment safety

## Project layout

```text
caddy-translator/
├── app/
│   ├── main.py
│   ├── translator.py
│   ├── deploy.py
│   ├── models.py
│   ├── settings.py
│   ├── templates/
│   │   ├── index.html
│   │   └── result.html
│   └── static/
│       └── style.css
├── output/
├── scripts/
│   └── deploy.sh
├── tests/
│   ├── test_translator.py
│   └── test_api.py
├── sample/
│   └── sample.csv
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## CSV schema

Required columns:

- `host`
- `upstream` or `upstream_host`

Optional columns:

- `upstream_scheme`
- `upstream_port`
- `tls_mode`
- `skip_verify`
- `notes`
- `enabled`

Defaults:

- `tls_mode=internal`
- `skip_verify=false`
- `enabled=true`
- `upstream_scheme=http`

Boolean true values: `true`, `1`, `yes`, `y`, `on`

Boolean false values: `false`, `0`, `no`, `n`, `off`, empty

## Environment variables

Copy `.env.example` to `.env` and adjust values as needed.

```env
APP_ENV=production
HOST=0.0.0.0
PORT=8000
OUTPUT_DIR=/app/output
TEMP_DIR=/app/tmp
AUTO_DEPLOY=false
ALLOW_URL_FETCH=true
CADDY_TARGET_FILE=/deploy-target/Caddyfile
CADDY_CONTAINER_NAME=caddy
CADDY_CONTAINER_CONFIG_PATH=/etc/caddy/Caddyfile
CADDY_VALIDATE_AND_RELOAD=true
DOCKER_SOCKET_ENABLED=false
```

Important:

- If `AUTO_DEPLOY=false`, translation requests only generate output and do not deploy automatically.
- `POST /deploy/latest` can still be used for an explicit manual deployment.
- If `DOCKER_SOCKET_ENABLED=false`, validation and reload are blocked.
- Mounting `/var/run/docker.sock` gives the app broad host control. Treat this as a trusted admin-only utility.

## Run locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the app:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Run with Docker

```bash
cp .env.example .env
docker compose up --build
```

Optional mounts in [docker-compose.yml](/d:/projects/caddy-writer/docker-compose.yml:1):

- `/var/run/docker.sock:/var/run/docker.sock` to enable `docker exec`
- `/path/to/real/caddy/dir:/deploy-target` to expose the live target Caddyfile

## API endpoints

- `GET /` renders the HTML UI
- `POST /translate/upload` accepts multipart CSV uploads
- `POST /translate/url` accepts JSON or form submissions with a URL
- `GET /health` returns `{"status":"ok"}`
- `GET /preview/latest` returns the latest generated Caddyfile text
- `POST /deploy/latest` deploys the latest generated file

Example URL translation request:

```json
{
  "url": "https://docs.google.com/spreadsheets/d/.../edit#gid=0",
  "deploy": false,
  "preview_only": true
}
```

## Deployment flow

When automatic or manual deployment runs, the app:

1. Verifies that the generated file exists.
2. Refuses deployment if the configured target file is missing.
3. Refuses deployment if the Caddy container is not running or Docker access is disabled.
4. Backs up the target file.
5. Copies in the generated file.
6. Runs `caddy validate`.
7. Runs `caddy reload` only if validation succeeds.

## Tests

Run the test suite with:

```bash
pytest
```
