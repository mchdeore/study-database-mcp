"""life-vault-mcp vault server.

Phase 0 foundations: a Markdown vault is the source of truth; the relational DB
(SQLite by default, Postgres+pgvector optional) is a derived index that can be
rebuilt from the vault at any time. See docs/vision/ for the full design.
"""
