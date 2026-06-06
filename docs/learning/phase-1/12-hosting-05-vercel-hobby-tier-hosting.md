# Vercel hobby tier as the Phase 1 frontend host

## Introduction

This document explains how the project's web application is hosted in Phase 1 on
Vercel, a managed cloud platform built by the company that maintains Next.js (the
React framework the web application is written in) and designed to deploy
front-end applications directly from a connected version-control repository. The
"hobby tier" is Vercel's free plan, intended for personal and non-commercial
projects, and Phase 1 deliberately stays inside its limits to keep frontend
hosting at no cost. The document sits in the Hosting and deploy track because it
describes where the browser-facing half of the system actually runs.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a managed front-end hosting platform provides and how it differs from operating your own server.
- Describe how a repository-connected deployment turns a pushed commit into a live, globally served page.
- Identify the build script Vercel runs for this web application and the file that declares it.
- Recognise the common mistakes when deploying to the free tier and recover from them.

Prerequisites:

- [The Next.js App Router and Server vs Client Components](02-frontend-01-nextjs-app-router-and-rsc.md) — introduces the framework whose output Vercel serves.
- [The Next.js standalone build output](01-foundations-11-nextjs-standalone-build.md) — explains the production build mode this document refers to.

## Problem it solves

A web application that renders pages on the server has to run somewhere reachable
on the public internet, with a build step, a process to serve requests, encrypted
connections, and a safe way to push out new versions. Setting all of that up by
hand is the concrete problem this topic addresses.

The common prior approach is to rent a plain virtual server, then manually install
a runtime, configure a web server, obtain and renew certificates for encrypted
connections, write a script that pulls new code and restarts the process, and put
a content delivery network (a geographically distributed cache that serves files
from a location near each visitor) in front of it. That approach works, but it
carries real costs:

- Every one of those pieces is a separate thing to configure, monitor, and keep patched, which is a heavy burden for a solo developer.
- An always-on rented server bills continuously, which conflicts with a strict monthly cost ceiling even when traffic is near zero.
- Shipping a new version safely — building it, swapping it in without downtime, and being able to roll back — is extra machinery you have to build and maintain yourself.

A managed front-end hosting platform solves this by collapsing those steps into one
connected workflow: you link a repository, and the platform builds, serves,
secures, and globally distributes the application automatically, with a free tier
that comfortably covers a small project.

## Mental model

Think of the platform as a print-on-demand publisher for a website. You hand over
your manuscript (the source code in your repository); whenever you submit a revised
manuscript, the publisher automatically typesets it (runs the build), prints
copies, stocks them in warehouses around the world (the edge cache), and hands
every reader the copy from the nearest warehouse — all without you ever operating
a printing press.

A single deployment flows through these steps:

1. You push a commit to the connected repository, or open a pull request (a proposed change submitted for review).
2. The platform detects the push, inspects the project to discover its framework, and starts a build using the project's declared build command.
3. The build produces the optimized application output plus the static assets that browsers download.
4. The platform uploads that output to its globally distributed network and assigns the deployment its own web address.
5. A push to the main branch becomes the live production version; a pull request becomes an isolated preview at its own address, so a change can be reviewed before it is merged.

The pipeline is automatic end to end: the only action you take is pushing code.

## How it works

A managed front-end hosting platform is a form of platform as a service (PaaS): a
hosting product that runs and operates your application for you, exposing a small
set of project settings instead of raw servers. For a front-end framework, the
platform specialises further — it knows how popular frameworks build and what they
emit, so it can deploy them with little or no manual configuration. This is often
called "zero-configuration" deployment: the platform reads the project, recognises
the framework from its declared dependencies, and infers the build command and the
output location automatically, so no extra hosting file is required in the common
case.

The build runs the framework's standard production build command in a temporary,
isolated build environment. That command compiles the application and emits two
kinds of output: static assets that never change between requests, and the code
needed to render pages dynamically on the server. The serving model then splits
along the same line. Static assets are served straight from the edge cache — copies
held at many locations close to visitors — so they load quickly and cost almost
nothing to deliver. Dynamic, server-rendered responses run in short-lived
serverless functions: units of code the platform starts on demand to handle a
request and tears down afterward, so you are not billed for an idle process.

Connecting a repository wires the platform to the version-control host. From then
on, each push triggers a build, and the platform distinguishes the production
branch from every other branch: production pushes update the live address, while
other branches and pull requests get their own preview addresses. Encrypted
connections are provisioned and renewed automatically, and the content delivery
network is included by default, so security and global distribution are not
separate setup tasks.

The free tier exists for personal and non-commercial use. It comes with allowances
— bandwidth served, build minutes consumed, and fair-use limits on how heavily the
serverless functions run — that are generous for a small project but are not meant
to carry production traffic for a paid product. Staying within those allowances is
what keeps the hosting free.

## MatchLayer Phase 1 usage

In Phase 1, MatchLayer hosts its web application on the Vercel hobby tier, the free
plan, which keeps the frontend's hosting cost at zero and fits the project's
sub-$20 monthly budget. There is no `vercel.json` configuration file committed to
the repository: the deployment relies on Vercel's zero-configuration detection,
which recognises the project as a Next.js application and runs the standard build.
The script Vercel runs is the `build` script declared in `apps/web/package.json`:

Source: `apps/web/package.json`

```json
  "scripts": {
    "build": "next build",
    "start": "next start",
```

When Vercel detects a Next.js project it invokes this `build` script (`next build`)
and serves the result from its own globally distributed network, supplying the
serving, edge caching, and encrypted connections so that no server process is
operated by the project itself. The web application's deploy notes in
`apps/web/README.md` document this same `build` script and the production build it
performs.

The build configuration in `apps/web/next.config.mjs` additionally sets
`output: "standalone"`, but that setting is required by the container image used
for the self-hosted backend path, not by Vercel — Vercel uses its own Next.js
build adapter and does not depend on the standalone folder. Hosting the frontend on
Vercel while containerising only the backend is what lets Phase 1 stay inside the
free tier while keeping a clean migration path open for later phases.

## Common pitfalls

- **Mistake:** Pointing the Vercel project at the repository root in a monorepo (a single repository that holds multiple applications and packages) instead of at the web application's own subfolder.
  **Symptom:** The build fails because the platform cannot find a Next.js application at the root, or it builds the wrong package.
  **Recovery:** Set the project's Root Directory to the web application's folder (`apps/web`) so the build runs where the framework and its build script live.

- **Mistake:** Assuming environment variables that exist in local development are present in production without configuring them in the hosting platform.
  **Symptom:** The application builds and serves, but pages that need configuration fail at runtime with missing-value errors that never appear locally.
  **Recovery:** Add every required variable in the Vercel project's environment-variable settings for the production and preview environments, then redeploy.

- **Mistake:** Treating the free hobby tier as production hosting for commercial traffic, or pushing build and bandwidth past its fair-use allowances.
  **Symptom:** Deployments are throttled or paused, and the dashboard prompts you to upgrade to a paid plan.
  **Recovery:** Keep usage within the hobby-tier limits for a non-commercial project, and move to a paid plan before the application carries commercial load.

- **Mistake:** Debugging a Vercel deployment as though it needs the `output: "standalone"` bundle, because the build configuration sets it.
  **Symptom:** Time is lost chasing the standalone server file on Vercel, which never uses it, instead of inspecting the platform's own build logs.
  **Recovery:** Read the platform's build and function logs directly; remember the standalone setting serves the container image path, not Vercel.

## External reading

- [Vercel: deploying a Next.js project](https://vercel.com/docs/frameworks/nextjs)
- [Vercel: the Hobby plan and its limits](https://vercel.com/docs/plans/hobby)
- [Next.js: deploying to Vercel and other platforms](https://nextjs.org/docs/app/getting-started/deploying)
