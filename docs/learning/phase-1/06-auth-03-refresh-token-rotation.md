# Refresh-token rotation and reuse detection

## Introduction

This document explains how a web application keeps a user signed in for days
without leaving a long-lived credential lying around to be stolen, and how it
notices when a credential has been copied and replayed by an attacker. The
mechanism has two halves: rotation, where each use of a long-lived token swaps it
for a brand-new one, and family-based reuse detection, where replaying an
already-used token triggers the revocation of every token descended from the same
original sign-in. A token here is a string the client presents to prove it is
allowed to act on a user's behalf; a refresh token is the long-lived one whose only
job is to obtain fresh short-lived tokens.

This topic sits in the Authentication and accounts track because the refresh
token is the most valuable credential a signed-in session holds: it outlives every
short-lived token and, if stolen, grants weeks of access unless the system can
detect the theft.

**Learning outcomes** — after reading this document you will be able to:

- Explain why a refresh token is rotated on every use instead of being reused. Each rotation shortens the window a stolen token stays valid.
- Describe how a token family links every rotation back to one original sign-in. The family is the unit that gets revoked on reuse.
- Explain how replaying a revoked token reveals a theft and what the system does in response. It revokes the whole family so neither party keeps access.
- Recognise the common mistakes in implementing rotation and recover from them. Most stem from treating the refresh token as stateless.

Prerequisites:

- [SQLAlchemy async engine and the per-request session](03-backend-04-sqlalchemy-async-and-session-dependency.md) — covers the database session and row-locking primitives the rotation logic relies on.
- [Append-only audit log](06-auth-06-append-only-audit-log.md) — covers the security-event record this document writes to when a replay is detected.

## Problem it solves

An application that signs a user in has to keep them signed in. The short-lived
token that authorizes each request — an access token, typically valid for only a
few minutes — expires quickly on purpose, so the client needs a way to get a fresh
one without asking for the password again. The refresh token fills that gap: it is
a longer-lived credential whose sole purpose is to mint new access tokens. That
makes it concentrated, durable power. If it never changed and an attacker copied
it, the attacker would hold valid access for the token's entire lifetime, and the
legitimate user would never know.

The earliest approach issued one long-lived token at sign-in and accepted it
unchanged until it expired. This is simple but fragile: a stolen token is
indistinguishable from the real one, every replay looks legitimate, and there is no
signal that a copy is circulating. A refinement keeps a list of valid tokens on the
server so any one can be revoked, but on its own that still does not tell the system
_which_ tokens are compromised or _when_ a theft happened.

Rotation plus reuse detection closes that gap. By replacing the refresh token on
every use and remembering which tokens have already been spent, the system turns a
silent theft into a loud, detectable event: the moment a spent token is presented
again, exactly one of two parties is replaying an old credential, and the safe
response is to invalidate the entire lineage so neither side keeps access.

## Mental model

Think of a numbered cloakroom ticket that can be exchanged but never reused. You
hand in your coat and receive ticket #1. To check on your coat you present ticket
#1; the attendant takes it, tears it up, and hands you ticket #2 in exchange. Next
time you present #2, get #3, and so on. Each ticket works exactly once, and holding
the current ticket is the only proof of ownership.

Now suppose someone photocopies ticket #2 while you are not looking. One of you
uses #2 and receives #3. When the other later presents their copy of #2, the
attendant sees a ticket that was already torn up. That can only mean a copy is
circulating — so the attendant cancels the whole chain of tickets tied to your
original coat check and asks everyone to re-identify. The thief is locked out, and
so are you, which is the point: better a forced re-login than a silent intruder.

Walking through the lifecycle step by step:

1. A fresh sign-in creates a new lineage and issues the first refresh token in it.
2. Presenting a valid, unspent refresh token marks it spent and issues a successor in the same lineage.
3. The successor carries the same lineage identifier as its predecessor, so the whole chain is traceable to one sign-in.
4. Presenting a token that is already marked spent is treated as a replay: every unspent token in that lineage is revoked at once.
5. A separate fresh sign-in starts an entirely new lineage, so compromising one does not bleed into another.

The detail newcomers miss is step 3: rotation alone is not enough. Without a shared
lineage identifier the system could retire the replayed token but would have no way
to revoke the successor the attacker may already be holding.

## How it works

Two kinds of token cooperate. The short-lived access token authorizes individual
requests and expires within minutes. The refresh token lives far longer and is
spent only to obtain a new access token (and, under rotation, a new refresh token).
Splitting the two means the powerful, durable credential is presented rarely, while
the credential presented on every request is nearly worthless once it expires.

Rotation means the refresh token is single-use. When a client presents one, the
server verifies it, marks that specific token as spent (revoked), and issues a new
refresh token in return. The client stores the new one and discards the old. The
practical effect is that any given refresh-token value is valid for one exchange
only, so the window in which a stolen copy is useful shrinks from the token's full
lifetime down to the gap before its next legitimate use.

For rotation to be enforceable the server must remember tokens rather than trust
them blindly. This is what "stateful" means here: the server keeps a record per
issued refresh token — its identifier, the lineage it belongs to, who it belongs
to, when it expires, and whether it has been revoked. A token is accepted only when
its record exists, is unexpired, belongs to the presenting user, and is not yet
revoked. Storing a per-token record is what lets the server distinguish an unspent
token from a spent one, which is the whole basis of detection.

The lineage is called a token family. Every refresh token records a family
identifier. The first token a sign-in issues gets a brand-new family identifier;
each rotation copies the predecessor's family identifier onto the successor. The
result is a chain: sign-in creates token A in family F, A rotates to B in family F,
B rotates to C in family F, and so on. One sign-in equals one family; a second
independent sign-in gets its own family, which keeps a compromise contained to a
single session lineage.

Reuse detection is the payoff. Under normal use only the newest token in a family
is ever presented, because each rotation hands the client a successor and retires
the predecessor. So when a token that has already been revoked is presented again,
something is wrong: either the legitimate client and an attacker both obtained the
same token and one of them already spent it, or a stolen older token is being
replayed. The server cannot tell which party is honest, so it takes the only safe
action — it revokes every still-valid token in that family at once. Whoever holds
the current token loses it too, forcing a fresh sign-in that starts a clean family.
The event is recorded in the security audit trail so an operator can investigate.

A subtle but important rule is that the lookup and revoke must be atomic. Because
two requests carrying the same token could arrive at nearly the same instant, the
server locks the token's record while it reads and updates it, so two concurrent
rotations cannot both believe they were first. Without that lock, a race could let
an attacker's replay slip through as if it were the legitimate rotation.

## MatchLayer Phase 1 usage

In MatchLayer's Phase 1 backend the refresh-token state lives in the
`refresh_tokens` table, defined as a SQLAlchemy model in
`apps/api/src/matchlayer_api/db/models.py`. Every column the rotation logic relies
on is here: the per-token identifier `jti` (the primary key), the `family_id` that
links a lineage, the owning `user_id`, the `expires_at` bound, and the nullable
`revoked_at` stamp that distinguishes a live token from a spent one:

Source: `apps/api/src/matchlayer_api/db/models.py`

```python
class RefreshToken(Base):
    """The ``refresh_tokens`` table (4.2)."""

    __tablename__ = "refresh_tokens"

    jti: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    family_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
```

The rotation and reuse-detection decision lives in the `Auth_Service` method
`rotate_refresh_token` in `apps/api/src/matchlayer_api/services/auth.py`. It locks
the presented token's row, rejects a missing, expired, or wrong-owner token as
invalid, treats an already-revoked token as a replay by revoking every unspent
sibling in the family, and on the happy path revokes the predecessor and issues a
successor in the same family:

Source: `apps/api/src/matchlayer_api/services/auth.py`

```python
    async def rotate_refresh_token(
        self,
        session: AsyncSession,
        *,
        presented_jti: UUID,
        user_id: UUID,
    ) -> RefreshOutcome:
        """Rotate a refresh token or detect reuse per §7.3."""
        result = await session.execute(
            select(RefreshToken).where(RefreshToken.jti == presented_jti).with_for_update()
        )
        row = result.scalar_one_or_none()

        if row is None:
            return RefreshOutcome(status=_RefreshStatus.INVALID)

        now = _now()

        if row.user_id != user_id or row.expires_at < now:
            return RefreshOutcome(status=_RefreshStatus.INVALID)

        if row.revoked_at is not None:
            # Reuse detected — revoke every non-revoked sibling in the family.
            await session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.family_id == row.family_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            await self._audit.emit(
                session,
                event_type="refresh_token_reuse_detected",
                user_id=row.user_id,
                payload={"family_id": str(row.family_id)},
            )
            return RefreshOutcome(status=_RefreshStatus.REUSED)

        # Happy path: revoke predecessor, issue successor in same family.
        row.revoked_at = now
        access_token, refresh_token, new_jti = self._issue_token_pair_for_user(
            session=session, user_id=row.user_id, family_id=row.family_id
        )
        return RefreshOutcome(
            status=_RefreshStatus.ROTATED,
            access_token=access_token,
            refresh_token=refresh_token,
        )
```

The `with_for_update()` clause is the atomic lock described above: it locks the
token's row for the duration of the transaction so two requests carrying the same
token cannot both pass the "is it revoked?" check. A registration or login elsewhere
in the same service allocates a brand-new family by calling the shared
`_issue_token_pair_for_user` helper with a fresh family identifier, so each sign-in
opens its own lineage while each rotation preserves the predecessor's.

## Common pitfalls

- **Mistake:** Accepting the refresh token without marking the old one spent, so the same token keeps working after a rotation.
  **Symptom:** A captured refresh token continues to mint access tokens indefinitely, and the audit trail shows no rotation events even though the client keeps refreshing.
  **Recovery:** Revoke the presented token in the same transaction that issues its successor, and add a test that asserts a second use of the same token is rejected.

- **Mistake:** Rotating tokens but giving each successor a new family identifier instead of inheriting the predecessor's.
  **Symptom:** Replaying a stolen older token is retired quietly but never triggers a family-wide revocation, so the attacker's current token survives.
  **Recovery:** Copy the predecessor's family identifier onto every successor so the whole lineage shares one identifier, and revoke by family on reuse.

- **Mistake:** Reading and updating the token record without locking the row, under the assumption that concurrent refreshes will not collide.
  **Symptom:** Under load, two near-simultaneous requests with the same token both succeed, and occasionally a legitimate replay is accepted as if it were the rotation.
  **Recovery:** Lock the row during the read-modify-write (for example with a select-for-update) so exactly one of the racing requests can mark the token spent.

- **Mistake:** Treating reuse detection as a logging-only event and leaving the rest of the family valid.
  **Symptom:** The system records that a replay happened but the attacker keeps a working token, so the alert fires while access continues.
  **Recovery:** On reuse, revoke every unspent token in the family so both parties are forced to re-authenticate, and record the event for investigation.

## External reading

- [Request for Comments (RFC) 6749: The OAuth 2.0 Authorization Framework (refresh tokens)](https://datatracker.ietf.org/doc/html/rfc6749)
- [RFC 9700: Best Current Practice for OAuth 2.0 Security (refresh token rotation and replay detection)](https://datatracker.ietf.org/doc/html/rfc9700)
- [RFC 7519: JSON Web Token (JWT)](https://datatracker.ietf.org/doc/html/rfc7519)
- [Mozilla Developer Network (MDN) Web Docs: Using Hypertext Transfer Protocol (HTTP) cookies](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Cookies)
