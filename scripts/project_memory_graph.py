"""Project canonical memory and code outbox events into Memgraph."""

from __future__ import annotations

import argparse
import time

from platform_app.config import settings
from platform_app.db import SessionLocal
from platform_app.graph_memory import (
    GraphUnavailable,
    MemgraphProjection,
    project_next,
    rebuild_scope,
)
from platform_app.memory import expire_due_facts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--rebuild-tenant")
    parser.add_argument("--rebuild-project")
    args = parser.parse_args()
    if bool(args.rebuild_tenant) != bool(args.rebuild_project):
        parser.error("Rebuild requires both tenant and project IDs")
    if args.serve and args.rebuild_project:
        parser.error("Rebuild is a one-shot operator action")
    if args.poll_seconds < 0.2 or args.poll_seconds > 60:
        parser.error("Poll interval must be between 0.2 and 60 seconds")
    config = settings()
    if not config.memgraph_uri:
        parser.error("AIP_MEMGRAPH_URI is required")
    graph = None
    try:
        while True:
            try:
                with SessionLocal() as db:
                    expired = expire_due_facts(db)
                if graph is None:
                    graph = MemgraphProjection(
                        config.memgraph_uri, config.memgraph_user, config.memgraph_password
                    )
                with SessionLocal() as db:
                    if args.rebuild_project:
                        count = rebuild_scope(
                            db, graph, args.rebuild_tenant, args.rebuild_project
                        )
                        print(f"Rebuilt {count} canonical records")
                        return 0
                    projected = project_next(db, graph)
                if projected is None and not args.serve and expired == 0:
                    return 0
                if projected is None and expired == 0:
                    time.sleep(args.poll_seconds)
            except GraphUnavailable:
                if graph is not None:
                    graph.close()
                    graph = None
                if not args.serve:
                    return 2
                time.sleep(args.poll_seconds)
    finally:
        if graph is not None:
            graph.close()


if __name__ == "__main__":
    raise SystemExit(main())
