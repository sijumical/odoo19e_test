from datetime import datetime

import pytest

try:  # pragma: no cover - skip when framework missing
    from odoo import fields
    from odoo.tests import SavepointCase
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "The Odoo test framework is not available in this execution environment.",
        allow_module_level=True,
    )


class TestDieselDefaults(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref("gear_on_rent.group_gear_on_rent_manager")

        cls.partner = cls.env["res.partner"].create({"name": "Diesel Client"})
        cls.product = cls.env["product.product"].create(
            {"name": "RMC Service", "type": "service", "list_price": 100.0}
        )
        cls.product.gear_is_production = True
        cls.workcenter = cls.env["mrp.workcenter"].create({"name": "Plant A", "code": "PL-A"})

    def _create_contract(self):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "x_workcenter_id": self.workcenter.id,
                "standard_loading_minutes": 20.0,
                "diesel_burn_rate_per_hour": 15.0,
                "diesel_rate_per_litre": 110.0,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product.id,
                "product_uom_qty": 100.0,
                "price_unit": 100.0,
                "start_date": fields.Datetime.to_datetime("2025-05-01 00:00:00"),
                "return_date": fields.Datetime.to_datetime("2025-05-31 23:59:59"),
            }
        )
        order.action_confirm()
        order.gear_generate_monthly_orders()
        monthly_order = self.env["gear.rmc.monthly.order"].search(
            [
                ("so_id", "=", order.id),
                ("date_start", "<=", fields.Date.to_date("2025-05-01")),
            ],
            limit=1,
        )
        monthly_order.action_schedule_orders()
        return order, monthly_order

    def test_contract_defaults_propagate_to_monthly_and_production(self):
        order, monthly_order = self._create_contract()
        self.assertAlmostEqual(monthly_order.standard_loading_minutes, 20.0)
        self.assertAlmostEqual(monthly_order.diesel_burn_rate_per_hour, 15.0)
        self.assertAlmostEqual(monthly_order.diesel_rate_per_litre, 110.0)

        production = monthly_order.production_ids.sorted(key=lambda p: p.date_start or datetime.min)[:1]
        self.assertTrue(production)
        self.assertAlmostEqual(production.standard_loading_minutes, 20.0)
        self.assertAlmostEqual(production.diesel_burn_rate_per_hour, 15.0)
        self.assertAlmostEqual(production.diesel_rate_per_litre, 110.0)
