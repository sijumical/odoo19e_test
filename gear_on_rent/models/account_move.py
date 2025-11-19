import base64
from calendar import monthrange

from odoo import _, api, fields, models
from odoo.tools import format_date, format_datetime


class AccountMove(models.Model):
    """Extends invoices with Gear On Rent billing metadata."""

    _inherit = "account.move"

    x_billing_category = fields.Selection(
        selection=[
            ("rental", "Rental"),
            ("rmc", "RMC"),
            ("plant", "Plant"),
        ],
        string="Billing Category",
        copy=False,
        tracking=True,
    )
    gear_month_end_version = fields.Integer(
        string="Month-End Report Version",
        default=1,
        copy=False,
    )
    gear_monthly_order_id = fields.Many2one(
        comodel_name="gear.rmc.monthly.order",
        string="Monthly Work Order",
        copy=False,
    )
    gear_target_qty = fields.Float(string="Monthly MGQ", copy=False)
    gear_adjusted_target_qty = fields.Float(string="Adjusted MGQ", copy=False)
    gear_prime_output_qty = fields.Float(string="Prime Output (m³)", copy=False)
    gear_optimized_standby_qty = fields.Float(string="Optimized Standby (m³)", copy=False)
    gear_ngt_hours = fields.Float(string="NGT Hours", copy=False)
    gear_loto_chargeable_hours = fields.Float(string="LOTO Chargeable Hours", copy=False)
    gear_waveoff_applied_hours = fields.Float(string="Wave-Off Applied (Hours)", copy=False)
    gear_waveoff_allowance_hours = fields.Float(string="Wave-Off Allowance (Hours)", copy=False)
    gear_log_summary_attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Log Summary Attachment",
        copy=False,
    )
    gear_month_end_attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Month-End Attachment",
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        for move in moves:
            if not move.x_billing_category:
                move._gear_sync_category_from_sale_orders()
        return moves

    def write(self, vals):
        res = super().write(vals)
        missing_category_moves = self.filtered(lambda m: not m.x_billing_category)
        if missing_category_moves:
            missing_category_moves._gear_sync_category_from_sale_orders()
        return res

    def _gear_sync_category_from_sale_orders(self):
        for move in self:
            if move.x_billing_category:
                continue
            orders = move._gear_get_related_sale_orders()
            categories = [cat for cat in orders.mapped("x_billing_category") if cat]
            if categories:
                move.x_billing_category = categories[0]

    def _gear_get_related_sale_orders(self):
        self.ensure_one()
        return self.invoice_line_ids.mapped("sale_line_ids.order_id")

    def _gear_get_month_end_payload(self):
        self.ensure_one()
        month_date = self.invoice_date or fields.Date.context_today(self)
        month_date = fields.Date.to_date(month_date)
        month_start = month_date.replace(day=1)
        last_day = monthrange(month_start.year, month_start.month)[1]
        month_end = month_start.replace(day=last_day)

        orders = self._gear_get_related_sale_orders()
        contract = orders[:1]

        month_orders = self.gear_monthly_order_id
        if not month_orders:
            month_orders = self.env["gear.rmc.monthly.order"]
            if contract:
                month_orders = month_orders.search(
                    [
                        ("so_id", "=", contract.id),
                        ("date_start", ">=", month_start),
                        ("date_start", "<=", month_end),
                    ]
                )

        if month_orders:
            summary = month_orders._gear_compute_billing_summary()
            cooling = summary["cooling"]
            normal = summary["normal"]
            total_target = cooling["target_qty"] + normal["target_qty"]
            total_adjusted = cooling["adjusted_target_qty"] + normal["adjusted_target_qty"]
            total_prime = cooling["prime_output_qty"] + normal["prime_output_qty"]
            total_standby = normal["standby_qty"]
            total_ngt_m3 = cooling["ngt_m3"] + normal["ngt_m3"]
            ngt_hours = self.gear_ngt_hours or (cooling["ngt_hours"] + normal["ngt_hours"])
            waveoff_applied = self.gear_waveoff_applied_hours or (
                cooling["waveoff_applied_hours"] + normal["waveoff_applied_hours"]
            )
            loto_chargeable = self.gear_loto_chargeable_hours or (
                cooling["waveoff_chargeable_hours"] + normal["waveoff_chargeable_hours"]
            )
            waveoff_allowance = self.gear_waveoff_allowance_hours or (contract.x_loto_waveoff_hours if contract else 0.0)

            target_qty = self.gear_target_qty or total_target
            adjusted_target = self.gear_adjusted_target_qty or total_adjusted or target_qty
            prime_output = self.gear_prime_output_qty or total_prime
            optimized_standby = self.gear_optimized_standby_qty or total_standby

            dockets = month_orders.mapped("docket_ids").filtered(lambda d: month_start <= d.date <= month_end)
            dockets = dockets.sorted(key=lambda d: (d.date, d.id))
            productions = month_orders.mapped("production_ids").filtered(
                lambda p: p.date_start and month_start <= fields.Datetime.to_datetime(p.date_start).date() <= month_end
            )
            productions = productions.sorted(key=lambda p: (p.date_start or fields.Datetime.now(), p.id))
            manufacturing_orders = [
                {
                    "date_start": format_datetime(self.env, production.date_start) if production.date_start else "",
                    "reference": production.name,
                    "is_cooling": bool(production.x_is_cooling_period),
                    "daily_mgq": production.x_daily_target_qty or 0.0,
                    "adjusted_mgq": production.x_adjusted_target_qty or 0.0,
                    "prime_output": production.x_prime_output_qty or 0.0,
                    "optimized_standby": production.x_optimized_standby_qty or 0.0,
                    "ngt_hours": production.x_ngt_hours or 0.0,
                    "loto_hours": production.x_loto_hours or 0.0,
                }
                for production in productions
            ]
            cooling_totals = {
                "target_qty": cooling["target_qty"],
                "prime_output_qty": cooling["prime_output_qty"],
                "ngt_m3": cooling["ngt_m3"],
            }
            normal_totals = {
                "target_qty": normal["target_qty"],
                "prime_output_qty": normal["prime_output_qty"],
                "standby_qty": normal["standby_qty"],
                "ngt_m3": normal["ngt_m3"],
            }
        else:
            docket_env = self.env["gear.rmc.docket"]
            dockets = docket_env.search(
                [
                    ("so_id", "in", orders.ids),
                    ("date", ">=", month_start),
                    ("date", "<=", month_end),
                ],
                order="date asc",
            )
            target_qty = self.gear_target_qty or (contract.x_monthly_mgq if contract else 0.0)
            adjusted_target = self.gear_adjusted_target_qty or target_qty
            prime_output = self.gear_prime_output_qty or sum(dockets.mapped("qty_m3"))
            optimized_standby = self.gear_optimized_standby_qty or max(adjusted_target - prime_output, 0.0)
            ngt_hours = self.gear_ngt_hours or 0.0
            waveoff_applied = self.gear_waveoff_applied_hours or 0.0
            loto_chargeable = self.gear_loto_chargeable_hours or 0.0
            waveoff_allowance = self.gear_waveoff_allowance_hours or (contract.x_loto_waveoff_hours if contract else 0.0)
            total_ngt_m3 = 0.0
            cooling_totals = {
                "target_qty": 0.0,
                "prime_output_qty": 0.0,
                "ngt_m3": 0.0,
            }
            normal_totals = {
                "target_qty": target_qty,
                "prime_output_qty": prime_output,
                "standby_qty": optimized_standby,
                "ngt_m3": 0.0,
            }
            manufacturing_orders = []
        if month_orders:
            total_ngt_m3 = cooling["ngt_m3"] + normal["ngt_m3"]

        payload = {
            "invoice_name": self.name,
            "month_label": format_date(self.env, month_start, date_format="MMMM yyyy"),
            "version_label": f"v{self.gear_month_end_version}",
            "contract_name": contract.name if contract else "",
            "customer_name": self.partner_id.display_name,
            "target_qty": target_qty,
            "adjusted_target_qty": adjusted_target,
            "ngt_hours": ngt_hours,
            "ngt_qty": total_ngt_m3 if month_orders else 0.0,
            "loto_total_hours": waveoff_applied + loto_chargeable,
            "waveoff_allowance": waveoff_allowance,
            "waveoff_applied": waveoff_applied,
            "loto_chargeable_hours": loto_chargeable,
            "prime_output_qty": prime_output,
            "optimized_standby": optimized_standby,
            "cooling_totals": cooling_totals,
            "normal_totals": normal_totals,
            "materials_shortage": contract.gear_materials_shortage_note if contract else "",
            "manpower_notes": contract.gear_manpower_note if contract else "",
            "asset_notes": contract.gear_asset_note if contract else "",
            "dockets": [
                {
                    "docket_no": docket.docket_no,
                    "date": format_date(self.env, docket.date),
                    "qty_m3": docket.qty_m3,
                    "workcenter": docket.workcenter_id.display_name,
                    "runtime_minutes": docket.runtime_minutes,
                    "idle_minutes": docket.idle_minutes,
                    "slump": docket.slump,
                    "alarms": ", ".join(docket.alarm_codes or []),
                    "notes": docket.notes,
                }
                for docket in dockets
            ],
            "manufacturing_orders": manufacturing_orders,
        }
        return payload

    def action_post(self):
        res = super().action_post()
        self._gear_attach_month_end_report()
        return res

    def _gear_attach_log_summary(self):
        report = self.env.ref("gear_on_rent.action_report_log_summary", raise_if_not_found=False)
        if not report:
            return
        for move in self:
            pdf_content, report_type = report._render_qweb_pdf(report.id, res_ids=move.ids)
            if report_type != "pdf":
                continue
            filename = "%s - %s.pdf" % (move.name or _("Invoice"), _("Log Summary"))
            attachment_vals = {
                "name": filename.replace("/", "_"),
                "type": "binary",
                "datas": base64.b64encode(pdf_content),
                "mimetype": "application/pdf",
                "res_model": move._name,
                "res_id": move.id,
            }
            attachment = move.gear_log_summary_attachment_id
            if attachment:
                attachment.write(attachment_vals)
            else:
                attachment = self.env["ir.attachment"].create(attachment_vals)
                move.gear_log_summary_attachment_id = attachment.id

    def _gear_attach_month_end_report(self):
        report = self.env.ref("gear_on_rent.action_report_month_end", raise_if_not_found=False)
        if not report:
            return
        for move in self.filtered(lambda m: m.x_billing_category == "rmc"):
            pdf_content, report_type = report._render_qweb_pdf(report.id, res_ids=move.ids)
            if report_type != "pdf":
                continue
            filename = "%s - %s.pdf" % (move.name or _("Invoice"), _("Month-End Report"))
            attachment_vals = {
                "name": filename.replace("/", "_"),
                "type": "binary",
                "datas": base64.b64encode(pdf_content),
                "mimetype": "application/pdf",
                "res_model": move._name,
                "res_id": move.id,
            }
            attachment = move.gear_month_end_attachment_id
            if attachment:
                attachment.write(attachment_vals)
            else:
                attachment = self.env["ir.attachment"].search(
                    [
                        ("res_model", "=", move._name),
                        ("res_id", "=", move.id),
                        ("name", "=", filename.replace("/", "_")),
                    ],
                    limit=1,
                )
                if attachment:
                    attachment.write(attachment_vals)
                else:
                    attachment = self.env["ir.attachment"].create(attachment_vals)
                move.gear_month_end_attachment_id = attachment.id
