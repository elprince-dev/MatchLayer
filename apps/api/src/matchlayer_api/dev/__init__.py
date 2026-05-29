"""Dev-mode-only surfaces.

Modules in this package exist exclusively to support local development.
Phase 1 only contains the password-reset link surface (see
``reset_links.py``); future dev-only ergonomics land here too.

The package is *imported* unconditionally -- Python module loading is
cheap and the contained code carries no runtime side effects beyond
allocating a process-singleton store. The *router* in this package
(see ``router.py``, added in task 7.3) is mounted onto the FastAPI
app only when ``MATCHLAYER_ENVIRONMENT == "development"``; the gate
lives in :mod:`matchlayer_api.main`, not inside the router (Design
Dev-Mode Reset-Link Surface §12.3, Architecture).
"""
