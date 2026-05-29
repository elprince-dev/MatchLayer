"""Service layer for the MatchLayer API.

Per the phase-1-auth design "Components and Interfaces" section, the
service layer owns *all* mutating business logic against the auth
tables. Routers in :mod:`matchlayer_api.auth.router` and
:mod:`matchlayer_api.dev.router` are pure HTTP-shape concerns that
delegate to the services here.

Submodules:
  * :mod:`.audit` -- ``Audit_Service``. The ONLY module that inserts
    into ``audit_events``. Emits in the caller's session so the audit
    row commits in the same transaction as the auth mutation that
    produced it (Audit Log §11.3, Requirement 15.4).
  * ``.auth`` -- ``Auth_Service``. The ONLY module that writes to
    ``users``, ``refresh_tokens``, and ``password_reset_tokens``.
    Added in a later sub-task; see ``tasks.md`` 6.2 / 6.3 / 6.4 / 6.5.
"""
