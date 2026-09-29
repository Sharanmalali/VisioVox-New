"""Test-wide isolation from a running development stack.

The suite loads the same `.env` the application does, so whatever the machine
is currently configured to do, the tests do too. That is fine for the database
- they share one deliberately - but not for the job queue.

With `PIPELINE_MODE=real` the API enqueues onto the real queue, a GPU worker
running on the same machine takes the job, and a test that also drives the
pipeline inline ends up with the same job executed twice. It surfaces as

    duplicate key value violates unique constraint "uq_job_stage_version"

on a stage row, in whichever test happened to lose the race - so it looked
like an intermittent fault in the product rather than two processes doing the
same work. It reproduced in roughly two runs out of three with a worker up,
and not once in three runs with none.

These variables are set before anything imports the settings, because they are
read at import time in the worker modules and cached thereafter. Environment
beats the env file in pydantic-settings, so this wins without touching `.env`.
"""

import os

# A broker no development worker listens on. Even with the full stack running,
# nothing else can consume what the suite enqueues.
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/15")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/14")

# The suite exercises the mock pipeline by design: the real one needs a GPU, a
# checkpoint and minutes per job. Pinning it here means a machine configured for
# real inference still runs the tests the way CI does.
os.environ["PIPELINE_MODE"] = "mock"
