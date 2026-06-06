# The append-only audit log

## Introduction

An audit log is a durable, time-ordered record of security-relevant events — a
user logging in or failing to, a password being changed, an account being
deleted — kept so that the history of who did what, and when, can be
reconstructed and trusted long after the events happened. This document explains
what it means for that log to be **append-only**: new rows may be added, but
existing rows are never altered or removed by the running program. The point of
the whole exercise is trust. A record you can quietly rewrite is worth nothing as
evidence, so the value of an audit log comes almost entirely from the guarantee
that nobody — not an attacker, not a careless line of code — can go back and edit
what it already says.

**Learning outcomes** — after reading this document you will be able to:

- Explain why an audit log must be append-only before it can serve as trustworthy evidence.
- Describe the two independent layers that enforce append-only: program code that only inserts, and database privileges that forbid changing or removing rows.
- Explain why each audit row is written inside the same transaction (an all-or-nothing bundle of database changes) as the action it records.
- Recognise the common mistakes when building an audit log and recover from them.

Prerequisites:

- [PostgreSQL 16 fundamentals](07-database-01-postgresql-fundamentals.md) — covers the relational model, transactions, and the role-and-privilege system this document builds on.
- [SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md) — covers the per-request database session that the audit writer shares with the action it records.

## Problem it solves

A system that handles accounts needs to answer questions like "when did this
account last log in", "who deleted this user", and "did someone try to reuse a
revoked credential". The concrete problem is producing a record of those events
that is complete, ordered, and — most importantly — **tamper-evident**, so that
the record can be believed during an incident investigation even if the program
that produced it was later compromised.

One common prior approach is to write these events into the program's ordinary
text logs. That record is fragile: logs are rotated and deleted on a schedule,
they are easy to edit or truncate, and they mix security events in with millions
of unrelated lines. A second common approach is to store the events in a normal
database table that the application can read and write freely. That is better —
the data is structured and queryable — but it carries a hidden weakness: if the
same program that inserts an event can also change or delete it, then a bug, a
malicious insider, or an attacker who has taken over the program can rewrite the
history to cover their tracks. A third approach is to rely on the discipline of
"never write code that edits the audit table". Discipline does not survive a
compromise, and it does not survive a teammate who has never heard the rule.

Append-only solves this by removing the ability to change history rather than
relying on a promise not to. The record can grow, but no existing entry can ever
be modified or erased.

## Mental model

Think of a bound paper ledger of the kind an accountant keeps. Entries are
written in ink, one after another, and the pages are sewn in so none can be torn
out. When a mistake is made, you do not erase it — you cannot — you write a new,
later entry that corrects it. Anyone reading the ledger sees the full sequence,
including the correction, and can trust that nothing earlier was quietly removed.
This is the same idea computer scientists call Write Once, Read Many (WORM)
storage: a medium you can add to and read from, but never overwrite.

Walk through how one event reaches the ledger:

1. The program performs an action — say, a successful login — and decides this is worth recording.
2. It writes a new entry describing the event: what kind of event it was, which account it belonged to, and when it happened.
3. The entry is added to the end of the ledger and is never given a way to be changed; older entries are left exactly as they were.
4. The act of recording is tied to the act it describes, so the system cannot end up with the login having happened but no entry, or an entry with no login.
5. Later, an investigator reads the entries in order and reconstructs the timeline, trusting that the sequence is complete because nothing could have been removed.

Step 3 is the property this document is about, and step 4 is what makes the entry
honest about whether the underlying action actually took effect.

## How it works

Append-only is a property enforced at two independent layers, and the strength of
the design comes from having both rather than either alone.

The first layer is the writing code. In a relational database, the four commands
that change a table's contents are, in Structured Query Language (SQL),
`INSERT` (add a new row), `SELECT` (read existing rows), `UPDATE` (change an
existing row), and `DELETE` (remove a row). There is also `TRUNCATE`, which empties
a whole table at once. An append-only writer uses only `INSERT` and `SELECT`. It
has no code path that issues `UPDATE`, `DELETE`, or `TRUNCATE` against the log, so
in normal operation the log only ever grows.

Code discipline alone is the "promise not to" approach, so the second layer makes
the promise impossible to break. A database hands out access through **roles** —
named logins — and each role is granted a specific set of **privileges**, the
permissions that say which commands it may run against which tables. A role can
be granted permission to insert and read while being denied permission to update,
delete, or truncate a particular table. Once those destructive privileges are
revoked from the role the program connects as, the database itself refuses any
attempt to change or remove a row in that table — and it refuses it regardless of
what the program's code tries to do. A bug that accidentally issues a destructive
command, or an attacker who has seized control of the program, hits a wall at the
database boundary. The log's immutability no longer depends on the correctness of
the code above it.

A third detail makes each entry honest. Databases group related changes into a
**transaction**: a bundle of statements that either all take effect together
(commit) or all undo together (roll back), with nothing in between ever visible
to others. When the entry that records an action is written inside the same
transaction as the action itself, the two share a single fate. If the action
commits, its record commits with it; if the action rolls back, its record
disappears too. That rules out the two dishonest outcomes an audit log must never
produce: an action that happened but was never recorded, and a record of an
action that never actually took effect.

Put together, the three pieces give a record that grows but never shrinks or
mutates, that resists tampering even when the program is compromised, and whose
every entry faithfully reflects whether the event it describes truly happened.

## MatchLayer Phase 1 usage

In Phase 1 the audit log is a single database table, `audit_events`, modelled in
`apps/api/src/matchlayer_api/db/models.py`. The model's own docstring records the
intent in one word — append-only:

Source: `apps/api/src/matchlayer_api/db/models.py`

```python
class AuditEvent(Base):
    """The ``audit_events`` table (4.4). Append-only."""

    __tablename__ = "audit_events"
```

The writing layer is one small service, `Audit_Service`, defined in
`apps/api/src/matchlayer_api/services/audit.py`. It is the only module in the
backend that writes to the table, and its single `emit` method does exactly one
thing: build a new row and stage it on the caller's database session. There is no
method that changes or removes a row. Crucially, `emit` does not commit on its own
— it adds the row to the session that the surrounding action already opened, so
the audit row commits in the same transaction as the action that produced it. If
that transaction rolls back, the audit row rolls back with it:

Source: `apps/api/src/matchlayer_api/services/audit.py`

```python
        row = AuditEvent(
            event_type=event_type,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            payload=payload_to_store,
        )
        session.add(row)
```

The payload stored alongside each event holds internal identifiers only. Restricted
data — Personally Identifiable Information (PII) such as a resume's text, a job
description, or a display name — is never placed in an audit payload; the event
references rows by their internal identifier instead.

The second enforcement layer lives in the database itself, set up by a migration —
a versioned, reviewed change to the database schema — at
`apps/api/alembic/versions/0001_users_and_auth.py`. After creating the table, the
migration revokes every privilege from the application's least-privilege role and
then grants back only the two it is allowed to keep, `INSERT` and `SELECT`:

Source: `apps/api/alembic/versions/0001_users_and_auth.py`

```python
    op.execute(f"REVOKE ALL ON TABLE audit_events FROM {_APP_ROLE}")
    op.execute(f"GRANT INSERT, SELECT ON TABLE audit_events TO {_APP_ROLE}")
```

Because the role the backend connects as has no `UPDATE`, `DELETE`, or `TRUNCATE`
privilege on `audit_events`, the database refuses any attempt to rewrite the log,
even one issued by buggy or compromised application code. The code-only-inserts
discipline and the privilege revocation are two independent guarantees of the same
property, which is why the table is forensically meaningful at the database
boundary regardless of what happens above it.

## Common pitfalls

- **Mistake:** Enforcing append-only only in application code, while the database role the program connects as still holds full read/write privileges on the audit table.
  **Symptom:** A code review shows no update or delete statements today, yet a single future bug, a careless script, or a compromised process can silently rewrite or wipe audit rows, and nothing stops it.
  **Recovery:** Revoke `UPDATE`, `DELETE`, and `TRUNCATE` from the application role on the audit table and grant back only `INSERT` and `SELECT`, so the database refuses destructive commands no matter what the code attempts.

- **Mistake:** Granting and revoking privileges against the role that owns the table instead of a separate least-privilege role the application uses.
  **Symptom:** The revoke statement appears to run successfully, but destructive commands still succeed, because a table's owner keeps implicit full access that bypasses the privilege checks entirely.
  **Recovery:** Connect the application as a dedicated least-privilege role that is not the table owner, and apply the grants and revokes to that role; verify by attempting a delete as that role and confirming it is rejected.

- **Mistake:** Writing the audit row in a separate transaction from the action it records (for example, committing the action first and emitting the audit event afterward).
  **Symptom:** During an incident you find actions that took effect with no matching audit row, or audit rows for actions that were rolled back, because a crash or error landed between the two commits.
  **Recovery:** Stage the audit row on the same session as the action and let them commit together, so the event and its record share one all-or-nothing transaction.

- **Mistake:** Putting sensitive values — email addresses, resume text, display names — directly into the audit payload to make events easier to read.
  **Symptom:** The append-only table you can never delete from now permanently holds restricted personal data, turning a security control into a long-lived data-exposure liability.
  **Recovery:** Store internal identifiers only and reference the related rows by their identifier; keep restricted data out of the payload so the immutable log never accumulates it.

## External reading

- [PostgreSQL 16 documentation: privileges](https://www.postgresql.org/docs/16/ddl-priv.html)
- [PostgreSQL 16 documentation: granting privileges](https://www.postgresql.org/docs/16/sql-grant.html)
- [PostgreSQL 16 documentation: revoking privileges](https://www.postgresql.org/docs/16/sql-revoke.html)
- [Open Worldwide Application Security Project (OWASP) Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
- [National Institute of Standards and Technology (NIST) glossary: audit log](https://csrc.nist.gov/glossary/term/audit_log)
