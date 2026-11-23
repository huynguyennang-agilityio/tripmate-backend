"""Rate limits counted in Redis (EPIC-11 spec, section 2.2).

Empty until card F6: the general ceiling on every route, per-route limits
with their own key, `429 rate.limited` with `Retry-After`, and
`503 server.unavailable` when Redis does not answer.
"""
