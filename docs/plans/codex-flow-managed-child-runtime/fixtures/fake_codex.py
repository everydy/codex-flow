#!/usr/bin/env python3
"""Local fake Codex CLI for the managed-child raw CLI smoke test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


args = sys.argv[1:]

if args == ["--version"]:
    print("codex-cli smoke-1.0")
    raise SystemExit(0)

if args == ["app-server", "--listen", "stdio://"]:
    child_home = Path(os.environ["CODEX_HOME"])
    skill_ids = ("plan-first-implementation", "review-all-in-one")
    for line in sys.stdin:
        request = json.loads(line)
        if request["method"] == "initialize":
            response = {
                "id": request["id"],
                "result": {"codexHome": str(child_home)},
            }
        elif request["method"] == "skills/list":
            cwd = request["params"]["cwds"][0]
            response = {
                "id": request["id"],
                "result": {
                    "data": [
                        {
                            "cwd": cwd,
                            "errors": [],
                            "skills": [
                                {
                                    "name": skill_id,
                                    "path": str(
                                        child_home / "skills" / skill_id / "SKILL.md"
                                    ),
                                    "enabled": True,
                                }
                                for skill_id in skill_ids
                            ],
                        }
                    ]
                },
            }
        else:
            response = {
                "id": request["id"],
                "error": {"message": "unexpected method"},
            }
        print(json.dumps(response), flush=True)
    raise SystemExit(0)

output = Path(args[args.index("--output-last-message") + 1])
if "resume" in args:
    output.write_text(
        'REVIEW_GATE status="pass" blockers=0 important=0 minor=0 '
        'reason="raw CLI fixture clean"\n'
        'COMMIT_UNIT_READY title="Raw CLI fixture" '
        'summary="managed child executed fixture"\n',
        encoding="utf-8",
    )
else:
    marker = Path("fixture/work.txt")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("managed child executed\n", encoding="utf-8")
    output.write_text("implementation phase\n", encoding="utf-8")
print('{"session_id":"raw-cli-smoke"}')
