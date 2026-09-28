"""Every ORM model on the shared ``Base``, imported in one place.

Domain packages outside ``saas_models`` (P0 wave: voice, locations, voicemail)
define tables on the same ``Base``; Alembic autogeneration, schema creation and
the RLS coverage test import this module so none of them sees a partial
metadata.
"""

from __future__ import annotations

from backend.database.saas_models import Base
from backend.locations import models as _locations  # noqa: F401
from backend.voice import models as _voice  # noqa: F401
from backend.voicemail import models as _voicemail  # noqa: F401

__all__ = ["Base"]
