from datetime import datetime, timedelta, time

import pytest

try:  # pragma: no cover - skip when the Odoo framework is unavailable
    from odoo import fields
    from odoo.tests import Form, SavepointCase
    from odoo.tests.common import new_test_request
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "The Odoo test framework is not available in this execution environment.",
        allow_module_level=True,
    )


class TestGearOnRentMrp(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref("gear_on_rent.group_gear_on_rent_manager")

        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Test Customer",
                "email": "customer@example.com",
            }
        )

        cls.product = cls.env["product.product"].create(
            {
                "name": "RMC Service",
                "type": "service",
                "list_price": 200.0,
            }
        )
        cls.product.gear_is_production = True

        cls.bom = cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.product.product_tmpl_id.id,
                "product_qty": 1.0,
                "type": "phantom",
            }
        )

        cls.workcenter = cls.env["mrp.workcenter"].create(
            {
                "name": "Batching Plant 1",
                "code": "PLANT-1",
                "capacity": 1,
                "time_start": 0,
                "time_stop": 0,
                "x_ids_external_id": "PLANT-1",
            }
        )

        contract_start = fields.Date.to_date("2025-03-01")
        contract_end = fields.Date.to_date("2025-03-31")

        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": cls.partner.id,
                "x_workcenter_id": cls.workcenter.id,
            }
        )

        cls.order_line = cls.env["sale.order.line"].create(
            {
                "order_id": cls.order.id,
                "product_id": cls.product.id,
                "product_uom_qty": 240.0,
                "price_unit": 200.0,
                "start_date": fields.Datetime.to_datetime("2025-03-01 00:00:00"),
                "return_date": fields.Datetime.to_datetime("2025-03-31 23:59:59"),
            }
        )

        cls.order.invalidate_recordset()
        cls.assertEqual(cls.order.x_billing_category, "rmc")
        cls.assertEqual(cls.order.x_monthly_mgq, 240.0)
        cls.assertEqual(cls.order.x_contract_start, contract_start)
        cls.assertEqual(cls.order.x_contract_end, contract_end)

        cls.order.action_confirm()
        cls.order.gear_generate_monthly_orders()
        cls.monthly_order = cls.env["gear.rmc.monthly.order"].search(
            [
                ("so_id", "=", cls.order.id),
                ("date_start", "<=", contract_start),
                ("date_end", ">=", contract_start),
            ],
            limit=1,
        )
        cls.monthly_order.action_schedule_orders()

    def _get_first_production(self):
        self.monthly_order.flush()
        production = self.monthly_order.production_ids.sorted(key=lambda p: p.date_start or datetime.min)[:1]
        self.assertTrue(production, "Expected a daily manufacturing order to exist")
        return production

    def _allocate_ngt(self, hours):
        start = fields.Datetime.to_datetime("2025-03-01 00:00:00")
        end = start + timedelta(hours=hours)
        ngt = self.env["gear.ngt.request"].create(
            {
                "so_id": self.order.id,
                "date_start": start,
                "date_end": end,
            }
        )
        ngt.action_submit()
        ngt.action_approve()
        return ngt

    def _allocate_loto(self, hours):
        start = fields.Datetime.to_datetime("2025-03-05 00:00:00")
        end = start + timedelta(hours=hours)
        loto = self.env["gear.loto.request"].create(
            {
                "so_id": self.order.id,
                "date_start": start,
                "date_end": end,
            }
        )
        loto.action_submit()
        loto.action_approve()
        return loto

    def test_ngt_relief_reduces_adjusted_target(self):
        ngt_hours = 12.0
        self._allocate_ngt(ngt_hours)
        daily_target = self.monthly_order.monthly_target_qty / len(self.monthly_order.production_ids)
        expected_relief_qty = daily_target * (ngt_hours / 24.0)
        expected_adjusted = self.monthly_order.monthly_target_qty - expected_relief_qty
        self.assertAlmostEqual(self.monthly_order.adjusted_target_qty, expected_adjusted, places=2)

    def test_loto_waveoff_applies_allowance(self):
        loto = self._allocate_loto(60.0)
        self.assertAlmostEqual(loto.hours_waveoff_applied, 48.0, places=2)
        self.assertAlmostEqual(loto.hours_chargeable, 12.0, places=2)
        self.assertAlmostEqual(self.monthly_order.waveoff_hours_applied, 48.0, places=2)
        self.assertAlmostEqual(self.monthly_order.waveoff_hours_chargeable, 12.0, places=2)

    def test_daily_orders_have_default_dockets(self):
        production = self._get_first_production()
        docket = production.x_docket_ids[:1]
        self.assertTrue(docket, "Expected scheduler to prepare a default docket for the daily MO.")
        self.assertEqual(
            docket.date,
            self.monthly_order.date_start,
            "Default docket should align with the monthly start date.",
        )

    def test_invoice_builder_uses_mrp_metrics(self):
        production = self._get_first_production()
        workorder = production.workorder_ids[:1]
        payload = {
            "produced_m3": 30.0,
            "timestamp": fields.Datetime.to_string(production.date_start or fields.Datetime.now()),
            "runtime_min": 45,
            "idle_min": 15,
            "alarms": ["LOW_WATER"],
        }
        workorder.gear_register_ids_payload(payload)

        wizard = Form(self.env["gear.prepare.invoice.mrp"])
        wizard.monthly_order_id = self.monthly_order
        wizard.invoice_date = fields.Date.to_date("2025-03-31")
        prepare = wizard.save()
        action = prepare.action_prepare_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])

        self.assertAlmostEqual(invoice.gear_prime_output_qty, 30.0, places=2)
        payload = invoice._gear_get_month_end_payload()
        self.assertEqual(len(payload.get("dockets", [])), 1)
        self.assertGreater(payload.get("optimized_standby"), -0.01)
        self.assertIn("cooling_totals", payload)
        self.assertIn("normal_totals", payload)
        self.assertAlmostEqual(payload.get("normal_totals", {}).get("prime_output_qty", 0.0), 30.0, places=2)
        self.assertAlmostEqual(payload.get("cooling_totals", {}).get("target_qty", 0.0), 0.0, places=2)

    def test_daily_mo_report_payload(self):
        production = self._get_first_production()
        workorder = production.workorder_ids[:1]
        payload = {
            "produced_m3": 28.0,
            "timestamp": fields.Datetime.to_string(production.date_start or fields.Datetime.now()),
            "runtime_min": 50,
            "idle_min": 10,
            "alarms": ["IDLE_HIGH"],
        }
        workorder.gear_register_ids_payload(payload)

        self._allocate_ngt(6.0)
        self.monthly_order.invalidate_recordset()
        production.invalidate_recordset()

        report_payload = production._gear_get_daily_report_payload()
        self.assertEqual(report_payload.get("invoice_name"), production.name)
        self.assertAlmostEqual(report_payload.get("prime_output_qty", 0.0), 28.0, places=2)
        self.assertAlmostEqual(report_payload.get("target_qty", 0.0), production.x_daily_target_qty or 0.0, places=2)
        self.assertGreater(report_payload.get("ngt_qty", 0.0), 0.0)
        self.assertEqual(report_payload.get("waveoff_allowance"), self.order.x_loto_waveoff_hours)

        mos = report_payload.get("manufacturing_orders", [])
        self.assertEqual(len(mos), 1)
        self.assertEqual(mos[0].get("reference"), production.name)
        self.assertAlmostEqual(mos[0].get("prime_output", 0.0), 28.0, places=2)

        dockets = report_payload.get("dockets", [])
        self.assertEqual(len(dockets), 1)
        self.assertTrue(dockets[0].get("timestamp"))
        self.assertAlmostEqual(dockets[0].get("qty_m3", 0.0), 28.0, places=2)
        self.assertAlmostEqual(dockets[0].get("runtime_minutes", 0.0), 50.0, places=2)
        self.assertFalse(report_payload.get("show_cooling_totals"))

    def test_daily_mo_report_payload_without_dockets(self):
        production = self._get_first_production()
        production.x_docket_ids.unlink()
        workorder = production.workorder_ids[:1]
        start_dt = fields.Datetime.to_datetime("2025-03-02 08:00:00")
        finish_dt = start_dt + timedelta(minutes=45)
        workorder.write(
            {
                "qty_produced": 12.5,
                "date_start": start_dt,
                "date_finished": finish_dt,
                "duration": 45.0,
            }
        )

        production.x_is_cooling_period = False
        self.monthly_order.write({"x_is_cooling_period": True})
        production.invalidate_recordset()
        report_payload = production._gear_get_daily_report_payload()

        self.assertAlmostEqual(report_payload.get("prime_output_qty", 0.0), 12.5, places=2)
        dockets = report_payload.get("dockets", [])
        self.assertEqual(len(dockets), 1)
        self.assertEqual(dockets[0].get("docket_no"), workorder.name)
        self.assertAlmostEqual(dockets[0].get("qty_m3", 0.0), 12.5, places=2)
        self.assertAlmostEqual(dockets[0].get("runtime_minutes", 0.0), 45.0, places=2)
        self.assertTrue(dockets[0].get("timestamp"))
        self.assertTrue(report_payload.get("show_cooling_totals"))
        self.assertAlmostEqual(report_payload.get("optimized_standby", 0.0), 0.0, places=2)

    def test_daily_mo_report_uses_monthly_order_when_unlinked(self):
        production = self._get_first_production()
        monthly_order = self.monthly_order
        production.x_monthly_order_id = False
        production.x_is_cooling_period = False
        monthly_order.write({"x_is_cooling_period": True})
        production.invalidate_recordset()

        payload = production._gear_get_daily_report_payload()
        self.assertTrue(payload.get("show_cooling_totals"))
        mos = payload.get("manufacturing_orders", [])
        self.assertEqual(len(mos), 1)
        self.assertTrue(mos[0].get("is_cooling"))

    def test_invoice_builder_adds_ngt_line(self):
        production = self._get_first_production()
        workorder = production.workorder_ids[:1]
        payload = {
            "produced_m3": 40.0,
            "timestamp": fields.Datetime.to_string(production.date_start or fields.Datetime.now()),
            "runtime_min": 60,
            "idle_min": 0,
            "alarms": [],
        }
        workorder.gear_register_ids_payload(payload)

        self._allocate_ngt(12.0)
        self.monthly_order.invalidate_recordset()

        wizard = Form(self.env["gear.prepare.invoice.mrp"])
        wizard.monthly_order_id = self.monthly_order
        wizard.invoice_date = fields.Date.to_date("2025-03-31")
        prepare = wizard.save()
        action = prepare.action_prepare_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])

        expected_qty = self.monthly_order.downtime_relief_qty
        self.assertGreater(expected_qty, 0.0)

        ngt_lines = invoice.invoice_line_ids.filtered(lambda l: "NGT Relief" in (l.name or ""))
        self.assertTrue(ngt_lines, "Expected an invoice line capturing NGT relief.")
        self.assertAlmostEqual(sum(ngt_lines.mapped("quantity")), expected_qty, places=2)
        self.assertTrue(all(abs(line.price_unit) < 1e-6 for line in ngt_lines))

        payload = invoice._gear_get_month_end_payload()
        self.assertAlmostEqual(
            payload.get("normal_totals", {}).get("ngt_m3", 0.0),
            expected_qty,
            places=2,
        )

    def test_incremental_monthly_order_generation(self):
        start = fields.Datetime.to_datetime("2025-03-01 00:00:00")
        end = fields.Datetime.to_datetime("2025-04-30 23:59:59")

        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "x_workcenter_id": self.workcenter.id,
            }
        )

        self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product.id,
                "product_uom_qty": 480.0,
                "price_unit": 200.0,
                "start_date": start,
                "return_date": end,
            }
        )

        order.invalidate_recordset()
        self.assertEqual(order.x_billing_category, "rmc")
        order.action_confirm()

        monthly_orders = self.env["gear.rmc.monthly.order"].search([("so_id", "=", order.id)])
        self.assertEqual(len(monthly_orders), 1, "Confirm should only prepare the first monthly WMO.")
        self.assertEqual(
            fields.Datetime.to_datetime(monthly_orders.x_window_start).time(),
            time(0, 0),
            "Window should start at local midnight.",
        )

        monthly_orders.action_mark_done()

        updated_orders = self.env["gear.rmc.monthly.order"].search([("so_id", "=", order.id)])
        self.assertEqual(len(updated_orders), 2, "Next monthly WMO should be created once the previous is done.")
        start_dates = sorted(updated_orders.mapped("date_start"))
        self.assertEqual(start_dates[0], fields.Date.to_date("2025-03-01"))
        self.assertEqual(start_dates[1], fields.Date.to_date("2025-04-01"))
        for monthly in updated_orders:
            production_dates = [
                fields.Datetime.to_datetime(prod.date_start).date() for prod in monthly.production_ids if prod.date_start
            ]
            self.assertTrue(
                all(monthly.date_start <= date <= monthly.date_end for date in production_dates),
                "Daily MOs must stay within the monthly window.",
            )

    def test_ids_controller_creates_docket(self):
        production = self._get_first_production()
        timestamp = (production.date_start or fields.Datetime.now()) + timedelta(minutes=5)
        self.env["ir.config_parameter"].sudo().set_param("gear_on_rent.ids_token", "secret-token")

        payload = {
            "workcenter_external_id": self.workcenter.x_ids_external_id,
            "timestamp": fields.Datetime.to_string(timestamp),
            "date": fields.Date.to_string(fields.Date.to_date("2025-02-28")),
            "produced_m3": 18.0,
            "runtime_min": 35,
            "idle_min": 10,
            "alarms": ["BATCH_DELAY"],
        }

        from odoo.addons.gear_on_rent.controllers.ids import GearIdsController

        with new_test_request(self.env, headers={"X-IDS-Token": "secret-token"}):
            response = GearIdsController().ids_workcenter_update(**payload)

        self.assertEqual(response.get("status"), "ok")
        workorder = production.workorder_ids[:1]
        self.assertAlmostEqual(workorder.gear_prime_output_qty, 18.0, places=2)
        self.assertTrue(workorder.gear_docket_ids)
        docket = workorder.gear_docket_ids[:1]
        self.assertEqual(
            docket.date,
            self.monthly_order.date_start,
            "Docket date should clamp to the monthly window start.",
        )

    def test_scheduler_clamps_stray_dockets(self):
        monthly = self.monthly_order
        production = monthly.production_ids[:1]
        workorder = production.workorder_ids[:1]
        stray = self.env["gear.rmc.docket"].create(
            {
                "so_id": monthly.so_id.id,
                "production_id": production.id,
                "workorder_id": workorder.id,
                "workcenter_id": workorder.workcenter_id.id,
                "docket_no": "STRAY-DOCKET",
                "date": fields.Date.to_date("2025-02-28"),
                "source": "manual",
            }
        )

        monthly.action_schedule_orders()
        stray.invalidate_recordset()
        self.assertEqual(
            stray.date,
            monthly.date_start,
            "Scheduler should clamp stray dockets inside the monthly window.",
        )
