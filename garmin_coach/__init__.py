"""Local-first Garmin health and performance journal."""
import hashlib
from pathlib import Path

__version__ = '0.3.0'
INSTALLATION_ID = hashlib.sha256(str(Path(__file__).resolve().parent.parent).encode()).hexdigest()[:16]
