"""Pytest configuration for the gear_on_rent module.

This file keeps pytest runs safe when the Odoo framework is not available in
the execution environment. If Odoo is missing, the entire suite is skipped
cleanly without attempting to start an Odoo server.
"""

import importlib.util

import pytest


odoo_spec = importlib.util.find_spec("odoo")
if odoo_spec is None:  # pragma: no cover - exercised only when Odoo missing
    pytest.skip("The Odoo framework is not available.", allow_module_level=True)

import odoo  # pylint: disable=wrong-import-position


@pytest.fixture(scope="session", autouse=True)
def configure_odoo():
    """Ensure Odoo test flags are enabled without starting the server."""

    odoo.tools.config["test_enable"] = True
    odoo.tools.config["without_demo"] = True
    odoo.tools.config["test_file"] = True
