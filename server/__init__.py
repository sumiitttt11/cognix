"""The server side of Cognix.

serve.py is the entry point and owns the socket, the static files and the
model-gateway proxy. This package is everything that arrived with accounts:

    config      one place for every setting, and the local/cloud decision
    crypto      cookie signing, constant-time compares, token generation
    hclient     a small JSON HTTP client over urllib, with retries
    supa        Supabase Auth (GoTrue) and Postgres (PostgREST) calls
    sessions    the HttpOnly session cookie and the CSRF pair
    limits      per-IP and per-user rate limiting
    api         /api/* — auth, profile, chats, maps, usage
    admin       /api/admin/* — everything an owner needs, role-gated

Import order runs one way only: api and admin may import everything below
them, nothing below them imports back up.
"""
