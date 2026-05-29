"""Cross-cutting security primitives for the MatchLayer API.

Each module in this package owns one narrow primitive and is, by
design, the *only* importer of its underlying third-party library:

* ``passwords.py``    — Argon2id hashing + password blocklist.
                        Only module that imports ``argon2-cffi``.
* ``jwt.py``          — JWT_Service (issue / verify with HS256 allowlist).
                        Only module that imports ``jwt`` (PyJWT).
* ``cookies.py``      — Set/clear helpers for ``matchlayer_refresh`` and
                        ``matchlayer_csrf``. Only place that calls
                        ``Response.set_cookie`` for those names.
* ``password_blocklist.txt`` — sorted top-1000 plaintext file consumed
                        by ``passwords.py`` via ``bisect``.

The import-boundary rules are spelled out in the phase-1-auth
design "Components and Interfaces" section and enforced by
``tests/unit/test_import_boundaries.py``.
"""
