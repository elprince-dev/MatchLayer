#!/bin/sh
# LocalStack "ready" init hook — runs inside the localstack container once
# the emulator is up (mounted at /etc/localstack/init/ready.d).
#
# Creates the Phase 4 agent Job_Queue so it exists as soon as the stack is
# started; the API enqueues analyze jobs on it and the Agent_Worker consumes
# from it (phase-4-agentic Requirement 11.3). The docker-compose healthcheck
# for the `localstack` service asserts this queue resolves, so
# `docker compose up --wait` only reports healthy after this hook has run.
#
# `awslocal` is the LocalStack-bundled aws-cli wrapper preconfigured with
# dummy credentials and the local endpoint. create-queue is idempotent for
# an existing queue with identical attributes, so restarts are safe.

set -eu

awslocal sqs create-queue --queue-name matchlayer-agent-jobs

echo "localstack-init: SQS queue 'matchlayer-agent-jobs' ready"
