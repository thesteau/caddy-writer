# caddy-writer

`caddy-writer` is a small Dockerized FastAPI utility for trusted internal homelab use. It accepts a CSV upload or URL, parses the rows with pandas, renders a Caddyfile preview, and writes the generated output to `/app/output/Caddyfile.generated` for manual copy/paste into your real Caddy config.

## Features

- HTML form and JSON API
- CSV upload or server-side URL fetch
- Google Sheets share-link support via CSV export conversion
- Row normalization, validation, and helpful error messages
- Preview of the generated Caddyfile in the browser
- Save generated output for manual copy/paste
- Simple shell helper that prints the latest generated file
- Tests for translation, URL loading, and output writing

## Project layout

```text
caddy-writer/
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
ALLOW_URL_FETCH=true
```

Important:

- The app writes the generated file to `OUTPUT_DIR/Caddyfile.generated`.
- Apply the generated config yourself by copying the preview or the saved file into your real Caddyfile.
- `POST /deploy/latest` now returns manual copy/paste instructions instead of changing host files.

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

The default compose file only mounts `./output` so generated files persist on the host.

## API endpoints

- `GET /` renders the HTML UI
- `POST /translate/upload` accepts multipart CSV uploads
- `POST /translate/url` accepts JSON or form submissions with a URL
- `GET /health` returns `{"status":"ok"}`
- `GET /preview/latest` returns the latest generated Caddyfile text
- `POST /deploy/latest` returns the latest generated file plus manual copy/paste instructions

Example URL translation request:

```json
{
  "url": "https://docs.google.com/spreadsheets/d/.../edit#gid=0",
  "preview_only": true
}
```

## Manual update flow

After generation, the app:

1. Saves the generated file to `OUTPUT_DIR/Caddyfile.generated`.
2. Shows the preview in the browser.
3. Lets you print the file with `scripts/deploy.sh`.
4. Leaves the final copy/paste into your real Caddyfile to you.

## Tests

Run the test suite with:

```bash
pytest
```
