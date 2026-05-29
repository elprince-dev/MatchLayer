-- Create the least-privilege application role used by the auth stack
-- (task 16.4). The dev-stack ``matchlayer`` user from docker-compose's
-- ``POSTGRES_USER`` is the database *owner* and therefore has implicit
-- ALL privileges on every table — meaning the migration's
-- ``REVOKE UPDATE, DELETE, TRUNCATE`` against it has no effect. INV-1
-- (Requirement 11.2) requires that the application role *cannot* rewrite
-- ``audit_events``; that contract only holds against a role separate from
-- the table owner.
--
-- This script is mounted into the Postgres container at
-- ``/docker-entrypoint-initdb.d/`` (see ``docker-compose.yml``) and runs
-- on first boot of the data volume. It is idempotent via the
-- ``DO $$ ... IF NOT EXISTS ... CREATE ROLE ...`` pattern so a manual
-- replay is safe. Once the role exists the migration's GRANT/REVOKE
-- block has the intended privilege graph to operate on.
--
-- Password is dev-only and matches the documented default in
-- ``.env.example`` (``MATCHLAYER_DATABASE_APP_ROLE_PASSWORD``). Production
-- (Phase 6+) provisions the role via AWS Secrets Manager-backed bootstrap.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'matchlayer_app') THEN
        CREATE ROLE matchlayer_app LOGIN PASSWORD 'dev_only_app_role_password';
    END IF;
END
$$;

-- Default schema-level privileges so newly created tables are visible to
-- the app role for SELECT/INSERT. Per-table fine-grained grants (the
-- audit-events INSERT/SELECT-only grant and the matching REVOKE for
-- UPDATE/DELETE/TRUNCATE) are emitted by the Alembic migration itself.
GRANT USAGE ON SCHEMA public TO matchlayer_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO matchlayer_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO matchlayer_app;
