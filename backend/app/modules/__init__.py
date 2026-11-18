"""Business modules. Importing this package registers every table on Base.metadata."""
from . import people  # noqa: F401  (must be first: other modules reference employees)
from . import assets, files, technical  # noqa: F401
from . import journey, finance  # noqa: F401
from . import automation, migration, ops  # noqa: F401
from ..integrations import google  # noqa: F401
