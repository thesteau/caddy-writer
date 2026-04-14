Replicate the .env.example from the root directory for use with deployment.

Rename the file to .env and make the necessary changes

Afterwards, deploy with `docker compose up -d` for your caddy configuration.

Ensure that you make the necessary changes to the mounted Caddyfile volume.

The normal deploy flow writes `Caddyfile` into the mounted directory.

Use preview-only when you want to inspect output without overwriting the mounted live Caddyfile.
