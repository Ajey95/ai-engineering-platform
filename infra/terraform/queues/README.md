# Dispatch queues

This module creates encrypted standard SQS work queues and DLQs for agent,
media and graph projection events. Each queue long-polls and moves a message
to its matching DLQ after five failed receives. The DLQ retains messages for
14 days, longer than the work queue's four days.

Standard SQS can deliver duplicates and out of order. The database run lease,
fence and effect keys own execution. `platform_app.queue_dispatch` publishes
only an outbox event ID and run/tenant identifiers, then records a separate
transport receipt. If SQS accepts a message and that database commit is lost,
the same event may be published twice. `platform_app.queue_consumer` rechecks
the canonical event and deletes the SQS message only after the ledger reports
a terminal processing result. It extends visibility while a handler runs.

The included `scripts/consume_run_dispatch.py` binds this transport only to the
synthetic development fixture worker. Customer repository admission remains
disabled. The media and projection queues are infrastructure contracts; their
workers do not yet consume SQS. Configure a selected AWS account/region and
grant separate producer and consumer IAM roles before deployment. Never place
provider keys, source code or report text in SQS messages.
