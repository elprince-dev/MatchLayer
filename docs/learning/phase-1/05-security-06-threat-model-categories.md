# Threat-model categories

## Introduction

This document explains what a threat model is and walks through the specific
categories of attack this project has decided to defend against. A threat model
is a deliberate, written list of the ways a system could be attacked, chosen so
that design and review effort goes where the real risks are rather than being
spread evenly over imagined ones. The point of naming categories is focus: a team
that has written down "these eight things are what we worry about" can check every
change against that list. The sensitive data at the centre of most of these
categories is Personally Identifiable Information (PII) — data that identifies a
specific person, such as an email address or the text of an uploaded resume. This
topic sits in the Security track because the threat model is the lens through
which every other security control is justified.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a threat model is and why naming a finite set of categories beats trying to defend against everything. Focus comes from a written list.
- Describe each threat category this project defends against in plain language. Each category names a concrete attacker goal.
- Explain the value of also writing down what is explicitly out of scope. A boundary prevents endless, unfocused hardening.
- Recognise common mistakes when reasoning about threats and recover from them. A threat model is only useful if it is kept current.

Prerequisites:

- [Personally Identifiable Information as a privacy defense in structured logging](05-security-03-structured-logging-as-pii-defense.md) — introduces the PII concept and one concrete control that several of these categories motivate.

## Problem it solves

Security work has no natural stopping point. A team can always add another check,
another layer, another audit — and without a guiding decision about what actually
matters, that effort scatters: too much energy on exotic risks, too little on the
mundane ones that attackers actually use. The concrete problem is deciding, before
writing controls, which attacks are worth defending against for this system, with
this data, at this stage.

The approach that fails is implicit security — each contributor defends against
whatever they personally happen to worry about, with no shared list. Gaps open
where everyone assumed someone else was covering a risk, and effort is wasted
where two people independently harden the same unlikely path. There is also no way
to review a change against "our risks" because nobody has written down what those
are.

A threat model solves this by making the decision explicit and shared. The team
enumerates the categories of attack it will defend against, states plainly which
ones it is not defending against yet, and uses that list as the standard every
design and code review measures against. The list is not eternal — it grows as the
system grows — but at any moment it is the agreed definition of what "secure
enough" means here.

## Mental model

Think of how a homeowner decides on security rather than buying every gadget sold.
They sit down and list the realistic risks for their home: a break-in through an
unlocked door, a package stolen from the porch, a fire in the kitchen. They also
note, deliberately, what they are not planning for — a tunnelling burglar, a
meteor — because chasing those would drain the budget that should go to good locks
and a smoke alarm. Then every purchase is judged against the list: does this
address a risk we named, or not?

Applying that to a software system:

1. List the concrete goals an attacker might pursue against this system and its data, in plain language.
2. For each goal, name it as a category so design and review can refer to it consistently.
3. Write down, equally deliberately, the attacks that are out of scope for now, so effort is not drained defending them.
4. Judge every new feature and change against the list: which category does it touch, and does it weaken any defense?
5. Revisit the list as the system gains new capabilities, since new features create new categories of risk.

Step 3 is the one teams skip and later regret: stating the boundary is what keeps
the model finite and the effort focused.

## How it works

A threat model is built by reasoning about three things together: the assets worth
protecting, the actors who might attack them, and the goals those actors pursue.
Assets here include private personal data and confidential system credentials.
Actors range from opportunistic attackers probing for weak points to insiders
making honest mistakes. Goals are the concrete outcomes an attacker wants. Naming
each goal as a category turns a vague unease into a checklist.

The categories this project defends against, each re-explained at an introductory
level, are:

- **Account takeover** — an attacker gaining control of a legitimate user's account, for instance by guessing or reusing leaked passwords, or stealing a session credential. The defense centres on strong password handling, sensible session lifetimes, and limits on repeated guessing.
- **Private-data exfiltration** — private personal information escaping the system through any channel: an over-sharing endpoint, a leaky error message, a misconfigured storage bucket, or a backup. The defense is to classify such data, never expose it by default, and reference it by identifier rather than by content.
- **Cost-driven denial of service** — an attacker running up the system's bill or exhausting its capacity by triggering expensive operations repeatedly, rather than knocking it offline directly. The defense is hard caps, per-user quotas, and bounds on the size of any single request.
- **Weaponized file uploads** — a hostile file crafted to harm the system that parses it, such as a malformed document that crashes a parser or an archive that expands to an enormous size when unpacked. The defense validates a file's true type, bounds its size, and isolates the parsing work.
- **Prompt injection** — adversarial text hidden inside user-supplied content that tries to hijack an automated language model into ignoring its instructions. The defense keeps trusted instructions distinctly separated from untrusted content and never lets the untrusted text issue commands. This becomes relevant once such models enter the system in a later phase.
- **Cross-tenant leakage** — one user being shown another user's data because a boundary between accounts was not enforced. The defense is to scope every data access to its owner. This grows in importance as the system serves more users.
- **Supply-chain compromise** — danger arriving through third-party code, such as a malicious package update or an imitation package with a deceptive name. The defense is pinned, scanned dependencies and a human check on unfamiliar package names.
- **Operator mistake** — a well-meaning insider accidentally weakening security, for example by logging private data, exposing a storage bucket, or committing a secret. The defense is automation that makes the safe path the default and catches the common slips.

Equally important is what the model explicitly does not try to defend against yet
— for example, highly resourced, targeted attackers and certain hardware-level
attacks. Naming those out of scope is not negligence; it is the boundary that
keeps the in-scope work finite and focused. As the system matures, categories move
across that boundary deliberately, by decision rather than by drift.

## MatchLayer Phase 1 usage

The full threat model for this project — the authoritative list these categories
are drawn from — lives in the security steering document at
[`.kiro/steering/security.md`](../../../.kiro/steering/security.md); read it for
the complete, current wording. Rather than copy that prose, this section shows one
place where a single category is realised in committed configuration.

The operator-mistake category — specifically, accidentally committing a secret —
is defended in part by the gitleaks secret-scanning hook declared in
`.pre-commit-config.yaml`. The hook runs before each commit so a secret-shaped
value is caught before it can enter history:

Source: `.pre-commit-config.yaml`

```yaml
- repo: https://github.com/gitleaks/gitleaks
  rev: v8.30.1
  hooks:
    - id: gitleaks-system
```

That one hook is a concrete instance of the broader principle behind the
operator-mistake category: make the safe path automatic so a routine slip is caught
by tooling rather than relying on every contributor to remember the rule. Each
other category maps to its own set of controls described elsewhere in this
sub-library and governed by the steering document linked above.

## Common pitfalls

- **Mistake:** Trying to defend against every conceivable attack instead of a named, finite set.
  **Symptom:** Security effort spreads thin, exotic risks get attention while ordinary ones go uncovered, and reviews have no consistent standard to apply.
  **Recovery:** Write down a finite list of categories worth defending, state what is out of scope, and measure every change against that list.

- **Mistake:** Omitting the explicit out-of-scope boundary from the threat model.
  **Symptom:** Hardening never feels finished because there is no agreed line, and time drains into defending unlikely or irrelevant attacks.
  **Recovery:** Record the attacks deliberately not defended against yet, and treat moving one in-scope as a conscious decision rather than a drift.

- **Mistake:** Writing the threat model once and never revisiting it as the system grows.
  **Symptom:** New capabilities — a language-model feature, a multi-user mode — introduce whole categories of risk that the stale model never named, so nobody defends them.
  **Recovery:** Review the model whenever a significant capability is added, and add or promote categories as the system's data and surface change.

## External reading

- [Open Worldwide Application Security Project (OWASP): Threat Modeling Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Threat_Modeling_Cheat_Sheet.html)
- [Open Worldwide Application Security Project (OWASP) Top Ten](https://owasp.org/www-project-top-ten/)
- [MDN Web Docs: Website security](https://developer.mozilla.org/en-US/docs/Learn_web_development/Extensions/Server-side/First_steps/Website_security)
