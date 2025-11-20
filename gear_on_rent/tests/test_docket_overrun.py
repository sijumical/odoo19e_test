import pytest

try:  # pragma: no cover - skip when framework missing
    from odoo import fields
    from odoo.exceptions import UserError
    from odoo.tests import SavepointCase
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "The Odoo test framework is not available in this execution environment.",
        allow_module_level=True,
    )


class TestDocketOverrun(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref("gear_on_rent.group_gear_on_rent_manager")

        cls.partner = cls.env["res.partner"].create({"name": "Overrun Client"})
        cls.product = cls.env["product.product"].create(
            {"name": "RMC Service", "type": "service", "list_price": 100.0}
        )
        cls.product.gear_is_production = True
        cls.workcenter = cls.env["mrp.workcenter"].create({"name": "Plant B", "code": "PL-B"})

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

    def test_overrun_computation_uses_contract_defaults(self):
        order, monthly_order = self._create_contract()
        docket = self.env["gear.rmc.docket"].create(
            {
                "so_id": order.id,
                "monthly_order_id": monthly_order.id,
                "docket_no": "DKT-OVR-01",
                "actual_loading_minutes": 35.0,
            }
        )

        self.assertAlmostEqual(docket.actual_loading_minutes, 35.0)
        self.assertAlmostEqual(docket.excess_minutes, 15.0)
        self.assertAlmostEqual(docket.excess_diesel_litre, 3.75)
        self.assertAlmostEqual(docket.excess_diesel_amount, 412.5)

    def test_operator_cannot_override_computed_overrun(self):
        order, monthly_order = self._create_contract()
        docket = self.env["gear.rmc.docket"].create(
            {
                "so_id": order.id,
                "monthly_order_id": monthly_order.id,
                "docket_no": "DKT-OVR-02",
            }
        )

        operator = self.env["res.users"].create(
            {
                "name": "Docket Operator",
                "login": "operator_overrun",
                "company_id": self.env.ref("base.main_company").id,
                "groups_id": [
                    (6, 0, [self.env.ref("base.group_user").id, self.env.ref("gear_on_rent.group_gear_on_rent_user").id])
                ],
            }
        )

        with self.assertRaises(UserError):
            docket.with_user(operator).write({"excess_minutes": 5.0})
