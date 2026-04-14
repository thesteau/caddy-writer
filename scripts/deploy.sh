#!/bin/sh
set -eu

SOURCE_PATH="${1:-/app/output/Caddyfile.generated}"
python - "$SOURCE_PATH" <<'PY'
from __future__ import annotations

import json
import sys

from app.deploy import deploy_generated_file


source_path = sys.argv[1]
result = deploy_generated_file(source_path)
print(json.dumps(result.model_dump(mode="json"), indent=2))
raise SystemExit(0 if result.succeeded else 1)
PY
