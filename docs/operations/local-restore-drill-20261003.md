# Isolated local PostgreSQL restore drill — 2026-10-03

This drill used the development PostgreSQL 17 container and synthetic project
data. It does not qualify a hosted backup schedule, RPO, RTO, object storage,
Memgraph rebuild, identity provider or application failover.

1. `pg_dump -Fc` captured database `aip` from the healthy local container.
   The private dump is `artifacts/aip_restore_20261003.dump` (106,988 bytes),
   SHA-256 `0484f4db38a96d22c16411cce3b1b0d1a5b0a0d6a0b1f72693fb74cfa8616f2d`.
2. Created the previously absent database `aip_restore_20261003` from
   `template0`; `pg_restore --exit-on-error --single-transaction` completed.
   The active `aip` database was not overwritten.
3. Alembic `check` reported no new upgrade operations at revision
   `c78b82d1fa40`. All 34 non-system tables existed in both databases, with
   matching row counts at verification time. The restored database held one
   tenant, three runs and two recording deletion tombstones. There were zero
   unvalidated foreign keys and zero run/event sequence mismatches.
4. In a new isolated artifact directory, created two synthetic media objects
   matching those restored deletion tombstones. The deletion reconciler
   checked two tombstones, cleaned two objects and reported zero failures.

For another local drill, use a new uniquely named restore database and artifact
directory. Verify that both are absent before creation. Keep the dump private;
it contains database rows. Before serving any restored media, replay deletion
ledgers, reconcile remote media deletions and rebuild derived graph state.

The remaining release gate is a timed restore from the selected hosted backup
and object store into an isolated environment, followed by authorization,
cross-tenant, worker, media deletion, graph rebuild and recovery checks under
the declared load. No RPO or RTO claim follows from this local drill.
