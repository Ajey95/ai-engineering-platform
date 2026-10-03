# Local capacity measurements — 2026-10-03

These measurements used one Windows-hosted Uvicorn process and loopback clients.
They exercise development authentication and synthetic runs, not hosted OIDC,
load balancing, TLS, availability, recovery or customer repositories.

| Database / setup | Probe | Result |
|---|---|---|
| SQLite development API | 50 project reads/s for 5 s | 250/250 HTTP 200; p95 19.8 ms |
| SQLite development API | 100 simultaneous SSE replay viewers | 100/100 received an event ID; initial replay p95 1,909.9 ms |
| Isolated restored PostgreSQL, default 5+10 pool | 50 project reads/s for 5 s | 250/250 HTTP 200; p95 24.6 ms |
| Isolated restored PostgreSQL, default 5+10 pool | 100 simultaneous SSE replay viewers | 100/100 received an event ID; initial replay p95 4,546.1 ms |
| Isolated restored PostgreSQL, 20+20 pool and short empty polls | 50 project reads/s for 5 s | 250/250 HTTP 200; p95 27.4 ms |
| Isolated restored PostgreSQL, 20+20 pool and short empty polls | 100 simultaneous SSE replay viewers | 100/100 received an event ID; initial replay p95 3,469.8 ms |
| Isolated restored PostgreSQL, 20+20 pool and short empty polls | 100 already connected viewers, one new durable event | 100/100 received it; four p95 trials: 1,129.7, 1,178.7, 1,177.6 and 1,131.9 ms |
| Same setup | 50 already connected viewers, one new durable event | 50/50 received it; p95 623.9 ms |

The first 100-viewer tests exposed a stream-dependency lifetime deadlock: 50
simultaneous viewers timed out while 20 succeeded. The API now closes its
initial authorization session before streaming and runs short poll queries in
a thread. It rechecks authorization before delivering each event batch and at
least every five seconds while idle. Empty polls use one query. The final
connected-viewer 100-client p95 **does not meet** the PRD target below one
second in this single-process local setup. The 50-client measurement cannot
be extrapolated to the proposed two-instance hosted service without a live
load-balancer and identity-provider test.

The repeatable probes are `python -m scripts.verify_local_capacity` and
`python -m scripts.verify_local_sse_delivery`. The latter deliberately refuses
non-loopback databases and appends a synthetic event only to an isolated
`aip_restore_` or `aip_verify_` PostgreSQL database. Results depend on local
CPU, connection pool, event count and network. They establish neither monthly
availability nor recovery objectives.
