"""SodaMem HTTP service layer.

Imports `sodamem`, never the reverse (CI invariant I3). Lives behind the
`[server]` extra so the core library install stays free of any ASGI stack (I1).
"""
