"""A called job cannot have a permission its caller does not grant: GitHub then refuses to start
the run (startup_failure). Every caller in this repository must grant what its called jobs ask."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
LEVEL = {"none": 0, "read": 1, "write": 2}


def _load(name: str) -> dict[str, object]:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    assert isinstance(data, dict)
    return data


def test_every_caller_grants_the_permissions_its_called_jobs_ask_for() -> None:
    problems: list[str] = []
    for caller_file in sorted(WORKFLOWS.glob("*.yml")):
        caller = _load(caller_file.name)
        jobs = caller.get("jobs", {})
        assert isinstance(jobs, dict)
        for job_id, job in jobs.items():
            uses = job.get("uses", "") if isinstance(job, dict) else ""
            if not uses.startswith("./.github/workflows/"):
                continue
            granted = job.get("permissions", caller.get("permissions", {})) or {}
            called = _load(Path(uses).name)
            for called_id, called_job in called.get("jobs", {}).items():
                wanted = called_job.get("permissions", called.get("permissions", {})) or {}
                for scope, level in wanted.items():
                    if LEVEL[level] > LEVEL[granted.get(scope, "none")]:
                        problems.append(
                            f"{caller_file.name} job {job_id} does not grant {scope}: {level} "
                            f"that {Path(uses).name} job {called_id} asks for"
                        )
    assert problems == []
