"""Export the OpenAPI schema as a build/CI artifact (NG-NFR-04).

Usage:  cd backend && python scripts/export_openapi.py [out.json]
CI diffs the artifact between commits; a breaking change to a released
/api path must fail review.
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("NETGEO_AUTH_STORE", "")  # never touch a real auth store

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app

out = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
schema = app.openapi()
# /api/v2/* is a path alias (ApiV2AliasMiddleware); document it explicitly.
schema.setdefault("info", {})["x-api-v2"] = "every /api/* path is also served at /api/v2/*"
with open(out, "w") as f:
    json.dump(schema, f, indent=2, sort_keys=True)
print(f"wrote {out}: {len(schema.get('paths', {}))} paths")
