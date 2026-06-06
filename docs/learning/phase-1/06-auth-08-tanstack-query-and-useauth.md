# TanStack Query and the useAuth Server-State Hook

## Introduction

This document explains how a web application keeps a local copy of data that
truly lives somewhere else — on a remote server — and how it keeps that copy
fresh, shared, and consistent across every part of the screen. The tool that
does this is TanStack Query (a library for fetching, caching, and synchronising
server data inside an application built with React, the JavaScript library for
building user interfaces). On top of it, this document explains a single custom
function — an authentication hook named `useAuth` — that any part of the
interface can call to ask "is the visitor signed in, and who are they?" A hook,
in React, is a function whose name starts with `use` and that lets a component
tap into framework features such as remembered state and data fetching.

The two ideas belong together because the signed-in user is a perfect example of
_server state_: the authoritative record of who the user is lives on the server,
the browser only ever holds a cached copy of it, and many separate pieces of the
screen need to read that copy at the same time without each one fetching it
independently.

**Learning outcomes** — after reading this document you will be able to:

- Explain what server state is and why it needs different handling from ordinary in-component state. Server state is a cached copy of remote data, so it can go stale and must be re-synchronised.
- Describe the core TanStack Query building blocks — the client, the cache, a query, a mutation, and the query key — and how they fit together. These five pieces are the whole working vocabulary of the library.
- Read an authentication hook that composes a query, several mutations, and an external token store into one consistent answer about the current user. This is the concrete pattern the rest of the application depends on.
- Recognise and recover from the most common mistakes made with caching and query keys. Most early bugs in this area trace back to a key that does not change when the underlying data changes.

Prerequisites: this document builds on
[The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md),
which introduces the split between a Server Component (interface code that runs
only on the server) and a Client Component (interface code that is also sent to
the browser so it can respond to interaction). TanStack Query and the `useAuth`
hook both run only in the browser, so that boundary matters here. No other
prerequisites.

## Problem it solves

Fetching data from a server and showing it on screen sounds straightforward, and
the first approach most developers reach for is to fetch the data by hand inside
each component that needs it, store the result in that component's own local
state, and track loading and error flags alongside it. That prior approach works
for one screen and one request, but it falls apart as an application grows, and
it creates several concrete problems.

The first problem is duplicated fetching. If three separate components each need
the current user, and each fetches it independently, the application makes three
identical network requests for one piece of data, and the three copies can
disagree if one finishes before another.

The second problem is staleness with no plan. Hand-written fetching gives the
data a single moment of truth — the instant it arrived. Nothing decides when that
copy is too old to trust, when it should be re-fetched, or what should happen
when the user switches back to the browser tab after an hour away.

The third problem is repetitive plumbing. Every hand-fetched screen re-implements
the same loading flag, the same error flag, the same "cancel the request if the
component disappears" logic. That boilerplate is copied, drifts, and rots.

TanStack Query addresses all three by introducing a single shared cache that
remembers each piece of server data under a name, hands the same cached copy to
every component that asks for it, and applies a consistent policy for when a copy
is considered stale and should be refreshed. The authentication hook then builds
on that foundation so that the whole application has one agreed answer to "who is
signed in?" instead of many competing ones.

## Mental model

A useful analogy is a library with a single front desk. Instead of every reader
walking into the stacks to find a book (each making their own trip to the
server), readers ask the front desk. The desk keeps recently requested books on a
nearby shelf — the cache. When someone asks for a book, the desk checks the shelf
first: if a fresh-enough copy is there, it is handed over immediately with no trip
to the stacks; if the copy is old or missing, the desk fetches it, puts it on the
shelf, and hands it over. Every reader who asks for the same book gets the same
copy from the same shelf. A _query key_ is the call number that tells the desk
exactly which book is being requested.

Here is the step-by-step flow when a component asks for a piece of server data:

1. The component calls a query and supplies a **query key** — a label, usually an array of values, that uniquely names the data it wants (for example, "the current user for this token").
2. The client looks in its **cache** for an entry stored under that key. If a fresh entry exists, the component receives it immediately and no network request happens.
3. If there is no entry, or the entry is older than the configured **stale time**, the client runs the supplied fetch function, stores the result in the cache under the key, and notifies every component subscribed to that key.
4. When something changes the data on the server, a **mutation** runs the change and then either writes the new value straight into the cache under the right key or marks the relevant keys for re-fetching, so every subscriber updates together.
5. Because the cache is keyed, changing the key (for example, when the signed-in identity changes) automatically points every reader at a different cache entry, with no manual cache-clearing step.

That cycle — key, cache lookup, conditional fetch, notify subscribers, mutate by
key — is the entire model. Everything else is configuration on top of it.

## How it works

### Server state versus client state

The central idea is a distinction between two kinds of state. _Client state_ is
data the application itself owns and fully controls: whether a menu is open, what
text is typed into a box, which tab is selected. _Server state_ is data whose
authoritative copy lives on a remote machine; the browser only ever holds a
borrowed, possibly out-of-date snapshot of it. The current signed-in user, a list
of records, a profile — these are server state. The insight behind a data-fetching
library is that server state needs fundamentally different handling: it can become
stale without the browser doing anything, it is shared by many parts of the
screen, and it must be re-synchronised rather than edited in place directly.

### The client and the provider

A data-fetching library of this kind centres on one object, usually called the
_client_, that owns the cache and the policies that govern it. Components do not
talk to the client directly; instead the client is placed at the top of the
component tree by a _provider_ — a wrapper component that makes one value
available to everything rendered inside it. Any component below the provider can
reach the client through a hook; a component that is not below a provider cannot,
and the library raises an error to say so. Placing the provider once near the root
of the tree means every screen shares one cache.

There is a subtlety about _where_ the client is created. In a framework that
renders on the server as well as in the browser — a technique called server-side
rendering (SSR), where the server produces finished markup before the browser
takes over — a fresh client must be created for every server request, so that one
visitor's cached data can never leak into another visitor's response. In the
browser, by contrast, the client is created exactly once and reused across every
navigation, so the cache survives as the user moves between screens. The standard
pattern is therefore: on the server, always build a new client; in the browser,
build it the first time and keep a single shared instance afterwards.

### Queries, mutations, and keys

A _query_ is a read. It pairs a query key with a function that knows how to fetch
the data, and it returns the cached value together with status flags such as
"currently loading" and "errored". A query can be turned off with an _enabled_
flag, so that a read which depends on some precondition (such as the presence of a
credential) does not run until that precondition is met.

A _mutation_ is a write — a request that changes data on the server, such as
signing in or signing out. A mutation does more than send the request; it also
carries callbacks that fire on success, on error, or when the request settles
either way. Those callbacks are where the cache is reconciled with the new
reality: a successful sign-in can write the new user straight into the cache,
while a sign-out can remove the user's cache entries entirely.

The query key is what ties it together. Because every cache entry is filed under
its key, including a changing value inside the key (such as the current
credential) makes the cache automatically switch entries when that value changes.
Swapping the credential points reads at a brand-new cache entry, so the previous
user's cached data is never shown to the next one — without any explicit
"clear the cache" call.

### Bridging an external value into a query

Sometimes a piece of state lives outside the React tree entirely — in a plain
module-level variable, for instance, so that non-component code can read and write
it too. React provides a dedicated hook for subscribing a component to such an
external store, so that the component re-renders the moment the external value
changes. Feeding that external value into a query key is what lets an out-of-tree
value (like an in-memory credential) drive an in-cache query: when the external
value flips, the subscribed component re-renders, the query key changes, and the
query re-reads under the new key. This composition — an external store feeding a
keyed query whose results feed the rest of the screen — is the backbone of the
authentication pattern in the next section.

## MatchLayer Phase 1 usage

MatchLayer mounts a single TanStack Query client near the root of the web
application and exposes a `useAuth` hook that every authenticated screen reads
from. The two files involved are the provider at
`apps/web/src/components/providers.tsx` and the hook at
`apps/web/src/lib/auth.ts`. The root layout at `apps/web/src/app/layout.tsx`
renders the provider so that the client is available on every route.

### The client and provider

The provider builds the client with a small default stale window and disables
automatic retries, so a failed request surfaces immediately instead of being
retried behind the user's back:

Source: `apps/web/src/components/providers.tsx`

```tsx
function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
```

It then follows the App Router guidance for _where_ the client is built — a fresh
client per request on the server, a single reused client in the browser:

Source: `apps/web/src/components/providers.tsx`

```tsx
let browserQueryClient: QueryClient | undefined;

function getQueryClient(): QueryClient {
  if (isServer) {
    return makeQueryClient();
  }
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}
```

The exported `Providers` wrapper places the client at the top of the tree with
`QueryClientProvider`, so every component below it can reach the cache:

Source: `apps/web/src/components/providers.tsx`

```tsx
export function Providers({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  const queryClient = getQueryClient();
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
```

### The `useAuth` hook

The hook reads the current access token (a short-lived credential string the
server issues at sign-in, sent back on each request to prove the caller's
identity) from a module-level store, and feeds that token into the query key of a
read against the identity endpoint. Keying on the token means a token swap
invalidates the prior cache entry with no explicit clear step, and the `enabled`
flag keeps the request from firing while the visitor is anonymous:

Source: `apps/web/src/lib/auth.ts`

```typescript
const meQuery = useQuery<AuthUser | null>({
  queryKey: ["auth", "me", token ?? null],
  queryFn: async () => {
    if (token === null) {
      return null;
    }
    return fetchMe(token);
  },
  enabled: token !== null,
  staleTime: 30_000,
  retry: false,
});
```

The write side is a set of mutations whose callbacks keep the cache and the token
store in lockstep. The sign-out mutation, for example, clears the in-memory token
and removes the cached identity regardless of whether the network call succeeded,
so the interface re-renders as anonymous either way:

Source: `apps/web/src/lib/auth.ts`

```typescript
const signOutMutation = useMutation({
  mutationFn: async () => {
    await postLogout();
  },
  onSettled: () => {
    setAccessToken(null);
    queryClient.removeQueries({ queryKey: ["auth", "me"] });
  },
});
```

The hook composes these with a subscription to the external token store, so that
flipping the token (at sign-in, refresh, or sign-out) re-renders every consumer
and re-points the query at the correct cache entry. The result is one agreed
answer to "who is signed in?" shared across the whole application.

## Common pitfalls

- **Mistake:** Calling the `useAuth` hook (or any query hook) from a component that is not rendered below the `QueryClientProvider`.
  **Symptom:** The application throws at runtime with a message like "No QueryClient set, use QueryClientProvider to set one", and the screen fails to render.
  **Recovery:** Ensure the provider is mounted near the root so the failing component is somewhere inside it; in this codebase that means rendering inside `Providers` from `apps/web/src/components/providers.tsx`.

- **Mistake:** Leaving a value that the data depends on out of the query key — for example, keying the identity read on a constant instead of including the current token.
  **Symptom:** After the signed-in identity changes, the screen keeps showing the previous user's data, because the cache entry under the unchanged key was never invalidated.
  **Recovery:** Put every value the fetched data depends on inside the query key (here, the token), so a change to that value automatically selects a different cache entry.

- **Mistake:** Creating a new query client on every render, or sharing one server-built client across requests.
  **Symptom:** Either the cache resets constantly and refetches never settle (a per-render client), or one visitor briefly sees another visitor's data during server rendering (a shared server client).
  **Recovery:** Build the client lazily once in the browser and reuse it, and build a fresh client per request on the server, following the `getQueryClient` split shown above.

- **Mistake:** Updating data on the server through a mutation but never reconciling the cache afterwards.
  **Symptom:** The change succeeds on the server, yet the screen still shows the old value until a full page reload forces a fresh fetch.
  **Recovery:** In the mutation's success or settled callback, either write the new value into the cache under the right key or remove the affected keys so the next read re-fetches.

## External reading

- [TanStack Query: Overview](https://tanstack.com/query/latest/docs/framework/react/overview)
- [TanStack Query: Important defaults (staleness and caching)](https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults)
- [TanStack Query: Query keys](https://tanstack.com/query/latest/docs/framework/react/guides/query-keys)
- [React: useSyncExternalStore](https://react.dev/reference/react/useSyncExternalStore)
- [MDN Web Docs: Using the Fetch API](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch)
