# caddy-writer

`caddy-writer` is a small Dockerized FastAPI utility for trusted internal homelab use. It accepts a CSV upload or URL, parses the rows with pandas, renders a Caddyfile preview, writes a generated file to `/app/output/Caddyfile.generated`, and when preview mode is off copies that result into a mounted Caddy config directory as the live `Caddyfile`.

## Features

- HTML form and JSON API
- CSV upload or server-side URL fetch
- Google Sheets share-link support via CSV export conversion
- Row normalization, validation, and helpful error messages
- Preview of the generated Caddyfile in the browser
- Writes a staging file to `OUTPUT_DIR/Caddyfile.generated`
- Copies the staging file into a mounted Caddy directory unless `preview_only=true`
- Uses `preview_only=true` when you want generation without replacing the mounted live file
- Writes the mounted target as `Caddyfile` during the normal deploy flow
- Simple shell helper that copies the latest generated file into the mounted target
- Tests for translation, URL loading, output writing, and deploy copying

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
CADDY_OUTPUT_DIR=/deploy-target
CADDY_OUTPUT_FILENAME=Caddyfile
```

Important:

- The app writes the generated file to `OUTPUT_DIR/Caddyfile.generated`.
- When `preview_only=false` (the default), the app also copies that file to `CADDY_OUTPUT_DIR/CADDY_OUTPUT_FILENAME`.
- With the current defaults, that mounted target file is `CADDY_OUTPUT_DIR/Caddyfile`.
- If you do not want to overwrite the mounted live file, use `preview_only=true`.
- If the Caddy directory is not mounted, generation still succeeds and the API/UI returns a warning instead of failing the translation.

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

To copy generated output into a mounted Caddy directory, uncomment and adjust the optional volume:

```yaml
services:
  caddy-writer:
    volumes:
      - ./output:/app/output
      - /path/to/real/caddy/dir:/deploy-target
```

With `.env.example`, the generated preview is stored at `./output/Caddyfile.generated` and the mounted copy is written to `/path/to/real/caddy/dir/Caddyfile`.

If you want preview without replacing the mounted live file, submit with `preview_only=true`.

## API endpoints

- `GET /` renders the HTML UI
- `POST /translate/upload` accepts multipart CSV uploads
- `POST /translate/url` accepts JSON or form submissions with a URL
- `GET /health` returns `{"status":"ok"}`
- `GET /preview/latest` returns the latest generated Caddyfile text
- `POST /deploy/latest` copies the latest generated staging file into the mounted Caddy directory and returns the destination path

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
3. Copies that file to `CADDY_OUTPUT_DIR/CADDY_OUTPUT_FILENAME` unless `preview_only=true`.
4. Replaces the mounted target file during the normal deploy flow.

If you want to inspect before copying, use `preview_only=true` with either translation endpoint and then call `POST /deploy/latest` later when you are ready.

## Tests

Run the test suite with:

```bash
python -m pytest tests
```
