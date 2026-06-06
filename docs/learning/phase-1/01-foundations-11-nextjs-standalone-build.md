# The Next.js standalone build output

## Introduction

This document explains a build setting that makes a web framework emit a small,
self-contained server bundle — a folder that contains the compiled application
plus only the dependencies it actually needs to run, and nothing else. The
setting is the standalone output mode of Next.js (a React framework for building
web applications that render on the server). Turning it on changes what the build
produces: instead of a build that still needs the project's full dependency tree
present to start, you get a trimmed folder you can copy into a container image
and run on its own. This belongs in the Foundation and tooling track because it
shapes how the web application is packaged for deployment.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a self-contained server bundle is and why it is smaller than a normal build plus its dependencies.
- Describe what the standalone output mode produces and why it suits container images.
- Read the configuration line that turns the mode on and name the server file it emits.
- Recognise the common mistakes around standalone builds and recover from them.

Prerequisites: this document builds on
[Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md), which
introduces the single-repository layout the web application lives in. No other
prerequisites; container packaging itself is covered later in the
Containerization track.

## Problem it solves

To run a server-rendered web application in production you have to ship the
compiled application together with the code it depends on at runtime. The
straightforward way is to copy the whole project — the build output plus the
entire installed dependency tree — into the runtime environment. That works, but
it is wasteful, and the waste is the concrete problem when you package the
application into a container image (a self-contained, runnable snapshot of an
application and its environment).

The common prior approach — copy the build output and run a full dependency
install in the runtime image — has real costs:

- The full dependency tree includes packages needed only to build the application, not to run it, so the runtime image carries code it never executes.
- A larger image is slower to build, slower to push and pull, and presents a larger surface of installed code to keep patched.
- Reproducing the exact runtime dependency set inside the image requires the package manager and lockfile to be present and an install step to run, adding moving parts to the image build.

The standalone output mode solves this by having the framework compute, at build
time, exactly which files and dependencies the server needs to run, and copying
only those into one output folder. The runtime image then contains only that
folder.

## Mental model

Think of a normal build as packing for a trip by bringing your entire wardrobe
"in case you need it", and the standalone build as a packing assistant who looks
at your itinerary and packs one small bag with exactly the clothes those
activities require. You arrive with far less to carry, and nothing you packed
goes unused.

When the framework produces a standalone build, it works like this:

1. The build compiles the application as usual, producing the optimized output.
2. The framework traces, from the server's entry point, every file and dependency the running server actually imports.
3. It copies that traced set — the compiled app plus only the needed dependencies — into a dedicated standalone output folder.
4. It writes a minimal server entry file into that folder that starts the application using only what was copied.
5. The runtime image copies only that folder and starts the server by running the entry file, with no separate dependency install step.

That tracing-and-copying in steps 2 and 3 is the whole idea: the framework, which
knows precisely what the server imports, assembles a minimal runnable bundle so
the deployment does not have to carry the rest.

## How it works

A server-rendering web framework normally produces a build artifact that still
relies on the surrounding project: the compiled pages and the runtime server are
present, but the dependencies they import at runtime are expected to be installed
alongside. Starting the server therefore requires the full installed dependency
tree to be present in the runtime environment.

Standalone output mode changes the final packaging step. After compiling, the
framework performs dependency tracing: starting from the server's entry point, it
follows every import to determine the exact set of files — application code and
third-party dependencies — that the running server needs. It then assembles a
self-contained output folder containing only that traced set, plus a minimal
server entry file that boots the application. The static assets the browser
downloads are handled separately, but the runnable server side is reduced to this
one folder.

The payoff is in packaging. A container image build can copy the standalone
folder and run its entry file directly, with no package manager, no lockfile, and
no install step inside the image. Because only the traced dependencies are
included, the image is markedly smaller than one carrying the full tree, which
makes it faster to build and transfer and reduces the amount of installed code
that has to be maintained. The trade-off is that the bundle is computed at build
time from what the code imports, so a dependency that is loaded in an unusual,
untraceable way may need to be accounted for explicitly — but for the common case
the tracing is accurate and the bundle is complete.

## MatchLayer Phase 1 usage

In MatchLayer the web application enables standalone output in its build
configuration file, `apps/web/next.config.mjs`. The configuration object sets the
output mode and the framework's strict development checks:

Source: `apps/web/next.config.mjs`

```javascript
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
```

Setting `output: "standalone"` is what makes the build emit the self-contained
server bundle. The production container definition relies on this: the image
copies the standalone folder the build produces and starts the application by
running the emitted server entry file (`server.js`), which only exists when this
option is turned on. That keeps the web application's production image small and
free of any in-image dependency install. The same configuration file also
defines a development-only request rewrite so the web application and the
back-end interface share one origin during local development, but the
`output: "standalone"` line is the part that governs how the application is
packaged for deployment.

## Common pitfalls

- **Mistake:** Removing or forgetting the `output: "standalone"` line while the container image still expects the standalone server entry file.
  **Symptom:** The image build cannot find the emitted server file and the container fails to start, because the build no longer produced the self-contained folder.
  **Recovery:** Restore `output: "standalone"` in the build configuration so the build emits the bundle the image copies, and rebuild.

- **Mistake:** Assuming the standalone folder also contains the browser-downloaded static assets and copying only the standalone folder when those assets are served separately.
  **Symptom:** The server runs but pages load without their styling or client-side assets, because the static files were not included.
  **Recovery:** Copy the static asset folders into the image alongside the standalone folder as the framework's deployment guidance describes, rather than the standalone folder by itself.

- **Mistake:** Relying on a dependency that is loaded in a way the build cannot trace, then shipping only the standalone bundle.
  **Symptom:** The server starts but fails at runtime with a missing-module error for code the tracing did not detect and therefore did not copy.
  **Recovery:** Make the dependency traceable (import it normally) or configure the build to include the extra files explicitly, then rebuild and confirm the bundle now contains it.

- **Mistake:** Treating the standalone build as a way to skip the lockfile and reproducible install during the build itself.
  **Symptom:** The build runs against unpinned dependency versions, so the traced bundle differs between builds even though the configuration is unchanged.
  **Recovery:** Keep building from the committed lockfile with a frozen install; standalone output trims the runtime image, it does not replace reproducible dependency resolution at build time.

## External reading

- [Next.js: output configuration and standalone mode](https://nextjs.org/docs/app/api-reference/config/next-config-js/output)
- [Next.js: deploying with a self-hosted Node.js server](https://nextjs.org/docs/app/getting-started/deploying)
- [Node.js: running a server entry file](https://nodejs.org/api/cli.html)
