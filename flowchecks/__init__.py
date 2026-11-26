"""The flow checks of development-flow.md, section 2.3 (EPIC-11 spec, section 2.12).

Four checks, one module each: `failing_first`, `test_lock`, `mutation`, and `risk_gate`.
They are generic: other repositories call them with their own path list and test command,
so nothing here names a repository. Each module has importable functions and a command line
(`python -m flowchecks.<name> --help`) that exits with 0 when the check passes.
"""
