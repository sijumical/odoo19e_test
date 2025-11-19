"""Top-level package init with optional Odoo imports."""

from importlib.util import find_spec
import warnings

_ODOO_AVAILABLE = find_spec("odoo") is not None

if _ODOO_AVAILABLE:  # pragma: no cover - executed inside real Odoo runtimes
    from . import models  # noqa: F401 (imported for side effects)
    from . import wizards  # noqa: F401
    from . import controllers  # noqa: F401
else:  # pragma: no cover - only triggered in lightweight CI
    warnings.warn(
        "Odoo framework not available; skipping gear_on_rent submodule imports.",
        RuntimeWarning,
        stacklevel=1,
    )
