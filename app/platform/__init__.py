"""The platform every module uses (EPIC-11 spec, section 2).

One file per part, so cards that run at the same time each fill their own
file. `platform` imports no other module: modules register with it, and it
finds them by scanning `app/` (see `modules.py`).
"""
