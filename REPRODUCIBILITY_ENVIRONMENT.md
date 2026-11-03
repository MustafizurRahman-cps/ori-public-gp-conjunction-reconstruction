# Reproducibility environment

Validated frozen scientific runtime:

- Python: 3.12.14
- NumPy: 2.3.5
- SciPy: 1.18.1
- sgp4: 2.27

`.python-version` records the Python version. `requirements-replay.lock.txt` records the exact externally imported
scientific package pins. No package was installed, upgraded, downgraded, or downloaded while generating R1.

The original author-side environment inventories, `SANITIZED_AUTHOR_ENVIRONMENT.json` and
`AUTHOR_ENVIRONMENT_PIP_FREEZE.txt`, are not distributed in this public software release.
The reproducible version specifications are provided in `.python-version` and
`requirements-replay.lock.txt`.
