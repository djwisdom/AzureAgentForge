"""Put the governor package root on sys.path so the offline tests can import it
as ``memory.*`` (and ``governor.*``) without installing the service or its
FastAPI/asyncpg dependencies."""

import pathlib
import sys

_GOVERNOR_SRC = (
    pathlib.Path(__file__).resolve().parents[2]
    / "services"
    / "memory-governor"
    / "src"
    / "governor"
)
sys.path.insert(0, str(_GOVERNOR_SRC))
