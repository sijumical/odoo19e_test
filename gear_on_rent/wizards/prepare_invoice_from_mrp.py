from calendar import monthrange

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PrepareInvoiceFromMrp(models.TransientModel):
    """Aggregate MRP work orders and dockets into an invoice."""

    _name = "gear.prepare.invoice.mrp"
    _description = "Prepare Gear On Rent Invoice (MRP)"

    monthly_order_id = fields.Many2one(
        comodel_name="gear.rmc.monthly.order",
        string="Monthly Work Order",
        domain=[("state", "!=", "draft")],
        required=True,
    )
    so_id = fields.Many2one(
        comodel_name="sale.order",
        related="monthly_order_id.so_id",
        store=False,
        readonly=True,
    )
    invoice_date = fields.Date(
        string="Invoice Date",
        required=True,
        default=lambda self: fields.Date.context_today(self),
    )
    x_unproduced_delta = fields.Float(
        string="Unproduced Rate Delta",
        default=50.0,
        help="Deduction from the base rate when billing unproduced MGQ.",
    )
    prime_output_qty = fields.Float(
        string="Prime Output (m³)",
        related="monthly_order_id.prime_output_qty",
        store=False,
        readonly=True,
    )
    optimized_standby_qty = fields.Float(
        string="Optimized Standby (m³)",
        related="monthly_order_id.optimized_standby_qty",
        store=False,
        readonly=True,
    )
    adjusted_target_qty = fields.Float(
        string="Adjusted MGQ",
        related="monthly_order_id.adjusted_target_qty",
        store=False,
        readonly=True,
    )
    downtime_relief_qty = fields.Float(
        string="NGT Relief (m³)",
        related="monthly_order_id.downtime_relief_qty",
        store=False,
        readonly=True,
    )
    ngt_hours = fields.Float(
        string="NGT Hours",
        related="monthly_order_id.ngt_hours",
        store=False,
        readonly=True,
    )
    loto_chargeable_hours = fields.Float(
        string="LOTO Chargeable Hours",
        related="monthly_order_id.waveoff_hours_chargeable",
        store=False,
        readonly=True,
    )

    def action_prepare_invoice(self):
        self.ensure_one()
        monthly = self.monthly_order_id
        order = monthly.so_id
        if not order:
            raise UserError(_("Please select a monthly work order linked to a sale order."))
        if order.x_billing_category != "rmc":
            raise UserError(_("This wizard can only prepare invoices for RMC contracts."))
        billable_lines = order.order_line.filtered(lambda l: not l.display_type and l.product_id)
        if not billable_lines:
            raise UserError(_("The sale order must have at least one billable product line."))

        def _classify(line):
            parts = [
                line.product_id.display_name or "",
                " ".join(line.product_id.product_template_attribute_value_ids.mapped("name") or []),
                line.name or "",
            ]
            label = " ".join(parts).lower()
            if "ngt" in label or "no-generation" in label:
                return "ngt"
            if "standby" in label or "shortfall" in label or "optimized" in label:
                return "standby"
            if "prime" in label:
                return "prime"
            return ""

        line_by_mode = {"prime": None, "standby": None, "ngt": None}
        for line in billable_lines:
            mode = _classify(line)
            if mode and not line_by_mode[mode]:
                line_by_mode[mode] = line

        main_line = line_by_mode.get("prime") or billable_lines[:1]

        def _extract_taxes(line):
            taxes_field = getattr(line, "tax_id", False) or getattr(line, "taxes_id", self.env["account.tax"])
            return taxes_field

        def _extract_analytic(line):
            distribution = getattr(line, "analytic_distribution", {}) or {}
            return {str(key): value for key, value in distribution.items() if value}

        def _compose_line_name(product, label):
            product_label = product.display_name or product.name or _("Unnamed Product")
            return f"{product_label} - {label}"

        taxes_prime = _extract_taxes(line_by_mode["prime"] or main_line)
        taxes_standby = _extract_taxes(line_by_mode["standby"] or main_line)
        taxes_ngt = _extract_taxes(line_by_mode["ngt"] or main_line)
        analytic_prime = _extract_analytic(line_by_mode["prime"] or main_line)
        analytic_standby = _extract_analytic(line_by_mode["standby"] or main_line)
        analytic_ngt = _extract_analytic(line_by_mode["ngt"] or main_line)

        if monthly.date_start:
            month_start = monthly.date_start.replace(day=1)
        else:
            today = fields.Date.context_today(self)
            month_start = today.replace(day=1)
        last_day = monthrange(month_start.year, month_start.month)[1]
        month_end = month_start.replace(day=last_day)
        period_start = monthly.date_start or month_start
        period_end = monthly.date_end or month_end

        month_orders = self.env["gear.rmc.monthly.order"].search(
            [
                ("so_id", "=", order.id),
                ("date_start", ">=", period_start),
                ("date_start", "<=", period_end),
            ]
        )
        if not month_orders:
            month_orders = monthly
        else:
            month_orders |= monthly

        summary = month_orders._gear_compute_billing_summary()
        cooling = summary["cooling"]
        normal = summary["normal"]
        prime_output = cooling["prime_output_qty"] + normal["prime_output_qty"]
        standby_qty = normal["standby_qty"]
        downtime_qty = cooling["ngt_m3"] + normal["ngt_m3"]
        adjusted_target_qty = cooling["adjusted_target_qty"] + normal["adjusted_target_qty"]
        target_qty = cooling["target_qty"] + normal["target_qty"]
        ngt_hours = cooling["ngt_hours"] + normal["ngt_hours"]
        waveoff_applied = cooling["waveoff_applied_hours"] + normal["waveoff_applied_hours"]
        waveoff_chargeable = cooling["waveoff_chargeable_hours"] + normal["waveoff_chargeable_hours"]

        if prime_output <= 0 and standby_qty <= 0 and downtime_qty <= 0:
            raise UserError(_("Nothing to invoice: no prime output, standby, or NGT quantities computed."))

        invoice_vals = {
            "move_type": "out_invoice",
            "partner_id": order.partner_invoice_id.id or order.partner_id.id,
            "currency_id": order.currency_id.id,
            "invoice_origin": order.name,
            "invoice_date": self.invoice_date,
            "x_billing_category": "rmc",
            "gear_monthly_order_id": monthly.id,
            "gear_target_qty": target_qty,
            "gear_adjusted_target_qty": adjusted_target_qty,
            "gear_prime_output_qty": prime_output,
            "gear_optimized_standby_qty": standby_qty,
            "gear_ngt_hours": ngt_hours,
            "gear_loto_chargeable_hours": waveoff_chargeable,
            "gear_waveoff_applied_hours": waveoff_applied,
            "gear_waveoff_allowance_hours": order.x_loto_waveoff_hours,
        }

        period_start_label = fields.Date.to_string(period_start)
        period_end_label = fields.Date.to_string(period_end)

        line_commands = []
        if prime_output > 0:
            prime_product = (line_by_mode["prime"] or main_line).product_id
            prime_sale_line_ids = (line_by_mode["prime"] or main_line).ids
            prime_price_unit = (line_by_mode["prime"] or main_line).price_unit
            prime_label = _("Prime Output for %s - %s") % (period_start_label, period_end_label)
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "name": _compose_line_name(prime_product, prime_label),
                        "product_id": prime_product.id,
                        "quantity": prime_output,
                        "price_unit": prime_price_unit,
                        "tax_ids": [(6, 0, taxes_prime.ids)] if taxes_prime else False,
                        "analytic_distribution": analytic_prime or False,
                        "sale_line_ids": [(6, 0, prime_sale_line_ids)],
                    },
                )
            )

        if standby_qty > 0:
            standby_line = line_by_mode["standby"]
            if standby_line:
                standby_product = standby_line.product_id
                standby_price_unit = standby_line.price_unit
                standby_sale_line_ids = standby_line.ids
            else:
                standby_product = (line_by_mode["prime"] or main_line).product_id
                standby_price_unit = max(main_line.price_unit - self.x_unproduced_delta, 0.0)
                standby_sale_line_ids = main_line.ids
            standby_label = _("MGQ Shortfall Adjustment (%s - %s)") % (period_start_label, period_end_label)
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "name": _compose_line_name(standby_product, standby_label),
                        "product_id": standby_product.id,
                        "quantity": standby_qty,
                        "price_unit": standby_price_unit,
                        "tax_ids": [(6, 0, taxes_standby.ids)] if taxes_standby else False,
                        "analytic_distribution": analytic_standby or False,
                        "sale_line_ids": [(6, 0, standby_sale_line_ids)],
                    },
                )
            )

        if downtime_qty > 0:
            ngt_line = line_by_mode["ngt"]
            ngt_product = (ngt_line or main_line).product_id
            ngt_sale_line_ids = (ngt_line or main_line).ids
            ngt_price_unit = (ngt_line.price_unit if ngt_line else 0.0)
            ngt_label = _("NGT Relief (%s - %s)") % (period_start_label, period_end_label)
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "name": _compose_line_name(ngt_product, ngt_label),
                        "product_id": ngt_product.id,
                        "quantity": downtime_qty,
                        "price_unit": ngt_price_unit,
                        "tax_ids": [(6, 0, taxes_ngt.ids)] if taxes_ngt else False,
                        "analytic_distribution": analytic_ngt or False,
                        "sale_line_ids": [(6, 0, ngt_sale_line_ids)],
                    },
                )
            )

        invoice_vals["invoice_line_ids"] = line_commands

        invoice = self.env["account.move"].create(invoice_vals)
        message = _(
            "Prime output: %(prime).2f m³, optimized standby: %(standby).2f m³, "
            "NGT billed: %(ngt_qty).2f m³, NGT hours: %(ngt_hours).2f h, LOTO chargeable: %(loto).2f h."
        ) % {
            "prime": prime_output,
            "standby": standby_qty,
            "ngt_qty": downtime_qty,
            "ngt_hours": ngt_hours,
            "loto": waveoff_chargeable,
        }
        invoice.message_post(body=message)

        action = {
            "type": "ir.actions.act_window",
            "name": _("Invoice"),
            "res_model": "account.move",
            "res_id": invoice.id,
            "view_mode": "form",
            "context": {"default_move_type": "out_invoice"},
        }
        return action
