from calendar import monthrange
from datetime import datetime, time, timedelta
from math import ceil

try:  # pragma: no cover - shim for dev/test containers
    import pytz
except ModuleNotFoundError:  # pragma: no cover
    from odoo_shims import pytz

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class GearRmcMonthlyOrder(models.Model):
    """Monthly umbrella that orchestrates daily manufacturing orders."""

    _name = "gear.rmc.monthly.order"
    _description = "RMC Monthly Work Order"
    _order = "date_start desc, name"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Reference",
        required=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code("gear.rmc.monthly.order") or _("New"),
        tracking=True,
    )
    so_id = fields.Many2one(
        comodel_name="sale.order",
        string="Contract / SO",
        required=True,
        domain=[("state", "in", ["sale", "done"])],
        tracking=True,
    )
    workcenter_id = fields.Many2one(
        comodel_name="mrp.workcenter",
        string="Primary Work Center",
        help="Work center that should host the automatically generated work orders.",
        tracking=True,
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="RMC Product",
        required=True,
        help="Service/consumable product that represents the concrete mix for this contract.",
    )
    date_start = fields.Date(string="Start Date", required=True, tracking=True)
    date_end = fields.Date(string="End Date", required=True, tracking=True)
    x_window_start = fields.Datetime(
        string="Window Start",
        tracking=True,
        help="Exact datetime at which this monthly work order window begins.",
    )
    x_window_end = fields.Datetime(
        string="Window End",
        tracking=True,
        help="Exact datetime at which this monthly work order window ends.",
    )
    x_is_cooling_period = fields.Boolean(
        string="Cooling Period",
        tracking=True,
        help="Flag indicating the window falls inside the contract cooling period.",
    )
    x_auto_email_daily = fields.Boolean(
        string="Email Daily Reports",
        default=True,
        help="When checked, emailing a daily MO report will notify the customer automatically.",
    )
    x_monthly_mgq_snapshot = fields.Float(
        string="Monthly MGQ Snapshot",
        digits=(16, 2),
        help="Snapshot of the contract MGQ allocated to this window.",
        tracking=True,
    )
    last_generated_date = fields.Date(
        string="Last Generated Day",
        help="Most recent calendar day for which daily manufacturing orders were generated.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("scheduled", "Scheduled"),
            ("done", "Done"),
        ],
        string="Status",
        default="draft",
        tracking=True,
    )
    production_ids = fields.One2many(
        comodel_name="mrp.production",
        inverse_name="x_monthly_order_id",
        string="Daily Manufacturing Orders",
    )
    docket_ids = fields.One2many(
        comodel_name="gear.rmc.docket",
        inverse_name="monthly_order_id",
        string="Dockets",
        readonly=True,
    )
    monthly_target_qty = fields.Float(
        string="Monthly MGQ",
        digits=(16, 2),
        compute="_compute_monthly_target_qty",
        store=True,
    )
    adjusted_target_qty = fields.Float(
        string="Adjusted MGQ",
        digits=(16, 2),
        compute="_compute_adjusted_target",
        store=True,
    )
    prime_output_qty = fields.Float(
        string="Prime Output (m³)",
        digits=(16, 2),
        compute="_compute_prime_output",
        store=True,
    )
    optimized_standby_qty = fields.Float(
        string="Optimized Standby (m³)",
        digits=(16, 2),
        compute="_compute_optimized_standby",
        store=True,
    )
    ngt_hours = fields.Float(
        string="NGT Hours",
        digits=(16, 2),
        compute="_compute_relief_breakdown",
        store=True,
    )
    loto_hours = fields.Float(
        string="LOTO Hours",
        digits=(16, 2),
        compute="_compute_relief_breakdown",
        store=True,
    )
    waveoff_hours_applied = fields.Float(
        string="Wave-Off Applied",
        digits=(16, 2),
        compute="_compute_relief_breakdown",
        store=True,
    )
    waveoff_hours_chargeable = fields.Float(
        string="Wave-Off Chargeable",
        digits=(16, 2),
        compute="_compute_relief_breakdown",
        store=True,
    )
    downtime_relief_qty = fields.Float(
        string="NGT (m³)",
        digits=(16, 2),
        compute="_compute_downtime_relief_qty",
        store=True,
    )
    runtime_minutes = fields.Float(
        string="Runtime (min)",
        digits=(16, 2),
        compute="_compute_runtime_idle",
        store=True,
    )
    idle_minutes = fields.Float(
        string="Idle (min)",
        digits=(16, 2),
        compute="_compute_runtime_idle",
        store=True,
    )
    docket_count = fields.Integer(
        string="Dockets",
        compute="_compute_docket_count",
        store=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        related="so_id.company_id",
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            "check_monthly_dates",
            "CHECK(date_end >= date_start)",
            "End date must not be earlier than start date.",
        )
    ]

    @api.depends("x_window_start", "x_window_end", "date_start", "date_end", "so_id", "so_id.x_monthly_mgq")
    def _compute_monthly_target_qty(self):
        for order in self:
            prorated = order._gear_get_prorated_mgq()
            if prorated is not None:
                order.monthly_target_qty = prorated
            else:
                order.monthly_target_qty = order.so_id.x_monthly_mgq or 0.0

    @api.depends("production_ids.x_adjusted_target_qty")
    def _compute_adjusted_target(self):
        for order in self:
            order.adjusted_target_qty = sum(order.production_ids.mapped("x_adjusted_target_qty"))

    @api.depends("production_ids.x_prime_output_qty")
    def _compute_prime_output(self):
        for order in self:
            order.prime_output_qty = sum(order.production_ids.mapped("x_prime_output_qty"))

    @api.depends("adjusted_target_qty", "prime_output_qty", "x_is_cooling_period")
    def _compute_optimized_standby(self):
        for order in self:
            if order.x_is_cooling_period:
                order.optimized_standby_qty = 0.0
            else:
                order.optimized_standby_qty = max((order.adjusted_target_qty or 0.0) - (order.prime_output_qty or 0.0), 0.0)

    @api.depends(
        "production_ids.x_ngt_hours",
        "production_ids.x_loto_hours",
        "production_ids.x_waveoff_hours_applied",
        "production_ids.x_waveoff_hours_chargeable",
        "date_start",
        "date_end",
        "so_id",
    )
    def _compute_relief_breakdown(self):
        NgTLedger = self.env["gear.ngt.ledger"]
        LotoLedger = self.env["gear.loto.ledger"]
        for order in self:
            ngt_total = sum(order.production_ids.mapped("x_ngt_hours"))
            loto_total = sum(order.production_ids.mapped("x_loto_hours"))
            ledger_domain = []
            if order.so_id:
                ledger_domain = [("so_id", "=", order.so_id.id)]
            month_key = None
            if order.date_start:
                # ledger month is stored as first day of the month
                month_key = order.date_start.replace(day=1)

            if ledger_domain:
                ngt_domain = list(ledger_domain)
                loto_domain = list(ledger_domain)
                if month_key:
                    ngt_domain.append(("month", "=", month_key))
                    loto_domain.append(("month", "=", month_key))

                ngt_ledgers = NgTLedger.search(ngt_domain)
                if ngt_ledgers:
                    ngt_total = sum(ngt_ledgers.mapped("hours_relief"))

                loto_ledgers = LotoLedger.search(loto_domain)
                if loto_ledgers:
                    loto_total = sum(loto_ledgers.mapped("hours_total"))

            order.ngt_hours = ngt_total
            order.loto_hours = loto_total
            allowance = order.so_id.x_loto_waveoff_hours or 0.0
            order.waveoff_hours_applied = min(loto_total, allowance)
            order.waveoff_hours_chargeable = max(loto_total - allowance, 0.0)

    @api.depends(
        "production_ids.x_ngt_hours",
        "production_ids.x_waveoff_hours_chargeable",
        "production_ids.x_daily_target_qty",
        "monthly_target_qty",
        "prime_output_qty",
        "x_is_cooling_period",
    )
    def _compute_downtime_relief_qty(self):
        for order in self:
            if order.x_is_cooling_period:
                target = order.monthly_target_qty or 0.0
                prime = order.prime_output_qty or 0.0
                order.downtime_relief_qty = round(max(target - prime, 0.0), 2)
            else:
                total_qty = 0.0
                for production in order.production_ids:
                    if production.x_ngt_hours:
                        total_qty += production._gear_hours_to_qty(production.x_ngt_hours)
                    if production.x_waveoff_hours_chargeable:
                        total_qty += production._gear_hours_to_qty(production.x_waveoff_hours_chargeable)
                order.downtime_relief_qty = round(total_qty, 2)

    @api.depends("docket_ids.runtime_minutes", "docket_ids.idle_minutes")
    def _compute_runtime_idle(self):
        for order in self:
            order.runtime_minutes = sum(order.docket_ids.mapped("runtime_minutes"))
            order.idle_minutes = sum(order.docket_ids.mapped("idle_minutes"))

    @api.depends("docket_ids")
    def _compute_docket_count(self):
        for order in self:
            order.docket_count = len(order.docket_ids)

    def _gear_get_prorated_mgq(self):
        self.ensure_one()
        contract = self.so_id
        base_mgq = contract.x_monthly_mgq if contract else 0.0
        month_hours = self._gear_get_month_hours()
        window_hours = self._gear_get_window_hours()
        ratio = 0.0
        if month_hours:
            ratio = window_hours / month_hours if window_hours else 0.0
        else:
            month_days = self._gear_get_month_days()
            window_days = self._gear_get_window_days()
            ratio = window_days / month_days if month_days else 0.0
        ratio = max(min(ratio, 1.0), 0.0)
        if base_mgq:
            return base_mgq * ratio
        if self.x_monthly_mgq_snapshot:
            return self.x_monthly_mgq_snapshot
        return 0.0

    def _gear_get_window_hours(self):
        self.ensure_one()
        start = self.x_window_start
        end = self.x_window_end
        if not start and self.date_start:
            start = datetime.combine(self.date_start, time.min)
        if not end and self.date_end:
            end = datetime.combine(self.date_end, time(23, 59, 59))
        return self._gear_compute_hours(start, end)

    def _gear_get_month_hours(self):
        self.ensure_one()
        if not self.date_start:
            return 0.0
        month_start = self.date_start.replace(day=1)
        last_day = monthrange(month_start.year, month_start.month)[1]
        month_end = month_start.replace(day=last_day)
        start_dt = datetime.combine(month_start, time.min)
        end_dt = datetime.combine(month_end, time(23, 59, 59))
        return self._gear_compute_hours(start_dt, end_dt)

    def _gear_get_window_days(self):
        self.ensure_one()
        if not self.date_start or not self.date_end or self.date_end < self.date_start:
            return 0
        return (self.date_end - self.date_start).days + 1

    def _gear_get_month_days(self):
        self.ensure_one()
        if not self.date_start:
            return 0
        last_day = monthrange(self.date_start.year, self.date_start.month)[1]
        month_start = self.date_start.replace(day=1)
        month_end = self.date_start.replace(day=last_day)
        return (month_end - month_start).days + 1

    @staticmethod
    def _gear_compute_hours(start_dt, end_dt):
        if not start_dt or not end_dt or end_dt < start_dt:
            return 0.0
        return max(((end_dt - start_dt).total_seconds() + 1.0) / 3600.0, 0.0)

    def action_schedule_orders(self, until_date=False):
        """Generate or refresh the daily manufacturing orders for the month."""
        for order in self:
            processed = order._generate_daily_productions(until_date=until_date)
            if processed:
                order.state = "scheduled"

    def action_mark_done(self):
        for order in self:
            order.state = "done"
            if order.so_id:
                order.so_id.gear_generate_next_monthly_order()

    def _generate_daily_productions(self, until_date=False):
        Production = self.env["mrp.production"]
        Workorder = self.env["mrp.workorder"]
        processed_any = False
        for order in self:
            if not order.product_id:
                raise UserError(_("Please select an RMC product before scheduling daily orders."))

            workcenter = order.workcenter_id or order.so_id.x_workcenter_id or order.product_id.gear_workcenter_id
            if not workcenter:
                raise UserError(
                    _(
                        "Please assign a work center to either the monthly order, the sale order, or the product itself."
                    )
                )

            if not order.workcenter_id:
                order.workcenter_id = workcenter

            days_in_month = (
                (order.date_end - order.date_start).days + 1
                if order.date_start and order.date_end
                else 0
            )
            if days_in_month <= 0:
                raise UserError(_("The monthly order must span at least one day."))

            target_qty = order.monthly_target_qty or 0.0
            daily_target = round(target_qty / days_in_month, 2) if days_in_month else 0.0

            if daily_target <= 0:
                raise UserError(
                    _(
                        "Monthly MGQ must be a positive value before generating daily orders for %s. "
                        "Please update the contract's Monthly MGQ."
                    )
                    % (order.so_id.display_name or order.name)
                )

            user_tz = order._gear_get_user_tz()
            if not order.last_generated_date and order.production_ids:
                existing_dates = [
                    order._gear_datetime_to_local_date(prod.date_start, user_tz)
                    for prod in order.production_ids
                    if prod.date_start
                ]
                existing_dates = [d for d in existing_dates if d]
                if existing_dates:
                    order.last_generated_date = max(existing_dates)
            if until_date:
                generation_end = min(until_date, order.date_end)
            else:
                generation_end = order.date_end

            if not generation_end or generation_end < order.date_start:
                continue

            if until_date:
                start_day = order.last_generated_date + timedelta(days=1) if order.last_generated_date else order.date_start
                cursor = max(order.date_start, start_day)
            else:
                cursor = order.date_start

            if cursor > generation_end:
                continue

            # Clean up productions and dockets that fall outside the monthly window
            cleanup_productions = order.production_ids.filtered(lambda p: p.state not in ("done", "cancel"))
            for production in cleanup_productions:
                local_date = order._gear_datetime_to_local_date(production.date_start, user_tz)
                if not local_date:
                    continue
                if local_date < order.date_start or local_date > order.date_end:
                    if production.x_docket_ids:
                        continue
                    try:
                        production.unlink()
                    except Exception:
                        _logger.exception("Failed to remove out-of-window production %s", production.display_name)

            draft_dockets = order.docket_ids.filtered(lambda d: d.state == "draft" and d.date)
            if draft_dockets:
                before_start = draft_dockets.filtered(lambda d: d.date < order.date_start)
                if before_start:
                    before_start.write({"date": order.date_start})
                after_end = draft_dockets.filtered(lambda d: d.date > order.date_end)
                if after_end:
                    after_end.write({"date": order.date_end})

            existing_map = {
                order._gear_datetime_to_local_date(production.date_start, user_tz): production
                for production in order.production_ids
                if production.date_start
            }

            processed_order = False

            while cursor <= generation_end:
                start_dt, end_dt = order._gear_get_day_bounds(cursor, user_tz)

                production = existing_map.get(cursor)
                if production:
                    if production.state not in ("done", "cancel"):
                        production.write(
                            {
                                "product_qty": daily_target,
                                "x_daily_target_qty": daily_target,
                                "x_is_cooling_period": order.x_is_cooling_period,
                            }
                        )
                else:
                    production_vals = {
                        "name": f"{order.name}-{cursor.strftime('%Y%m%d')}",
                        "product_id": order.product_id.id,
                        "product_qty": daily_target,
                        "product_uom_id": order.product_id.uom_id.id,
                        "company_id": order.company_id.id,
                        "origin": order.so_id.name,
                        "date_start": start_dt,
                        "date_finished": end_dt,
                        "x_monthly_order_id": order.id,
                        "x_sale_order_id": order.so_id.id,
                        "x_daily_target_qty": daily_target,
                        "x_is_cooling_period": order.x_is_cooling_period,
                    }
                    production = Production.search(
                        [
                            ("name", "=", production_vals["name"]),
                            ("company_id", "=", order.company_id.id),
                        ],
                        limit=1,
                    )
                    if production:
                        production.write(production_vals)
                    else:
                        production = Production.create(production_vals)
                        production.action_confirm()

                if production.state not in ("done", "cancel"):
                    self._gear_sync_production_workorders(production, workcenter, start_dt, end_dt)
                    order._gear_ensure_daily_docket(production, start_dt, user_tz)
                processed_order = True
                cursor += timedelta(days=1)

            if processed_order:
                order.last_generated_date = generation_end
                processed_any = True
        return processed_any

    def _gear_compute_billing_summary(self):
        summary = {
            "cooling": {
                "target_qty": 0.0,
                "adjusted_target_qty": 0.0,
                "prime_output_qty": 0.0,
                "standby_qty": 0.0,
                "ngt_m3": 0.0,
                "ngt_hours": 0.0,
                "waveoff_applied_hours": 0.0,
                "waveoff_chargeable_hours": 0.0,
            },
            "normal": {
                "target_qty": 0.0,
                "adjusted_target_qty": 0.0,
                "prime_output_qty": 0.0,
                "standby_qty": 0.0,
                "ngt_m3": 0.0,
                "ngt_hours": 0.0,
                "waveoff_applied_hours": 0.0,
                "waveoff_chargeable_hours": 0.0,
            },
        }
        for order in self:
            bucket = "cooling" if order.x_is_cooling_period else "normal"
            data = summary[bucket]
            target = order.monthly_target_qty or 0.0
            prime = order.prime_output_qty or 0.0
            standby = 0.0 if order.x_is_cooling_period else (order.optimized_standby_qty or 0.0)
            ngt_m3 = order.downtime_relief_qty or 0.0
            data["target_qty"] += target
            data["adjusted_target_qty"] += order.adjusted_target_qty or target
            data["prime_output_qty"] += prime
            data["standby_qty"] += standby
            data["ngt_m3"] += ngt_m3
            data["ngt_hours"] += order.ngt_hours or 0.0
            data["waveoff_applied_hours"] += order.waveoff_hours_applied or 0.0
            data["waveoff_chargeable_hours"] += order.waveoff_hours_chargeable or 0.0
        return summary

    def _gear_reassign_productions_to_windows(self):
        """Move daily productions under the window that matches their execution date."""
        all_orders = self.filtered("so_id")
        if not all_orders:
            return
        all_orders = all_orders.sorted(key=lambda o: (o.date_start or fields.Date.today(), o.id))
        user_tz = all_orders[0]._gear_get_user_tz()
        for production in all_orders.mapped("production_ids"):
            if production.state in ("done", "cancel"):
                continue
            local_date = all_orders[0]._gear_datetime_to_local_date(production.date_start, user_tz)
            if not local_date:
                continue
            target = all_orders.filtered(
                lambda mo: mo.date_start and mo.date_end and mo.date_start <= local_date <= mo.date_end
            )
            if target:
                target = target[0]
                if production.x_monthly_order_id != target:
                    production.x_monthly_order_id = target.id
                if production.x_is_cooling_period != target.x_is_cooling_period:
                    production.x_is_cooling_period = target.x_is_cooling_period
    def _gear_ensure_daily_docket(self, production, start_dt, user_tz):
        """Ensure a draft docket exists for the given production day."""
        self.ensure_one()
        if not production:
            return
        local_date = self._gear_datetime_to_local_date(start_dt, user_tz)
        if not local_date:
            return

        Docket = self.env["gear.rmc.docket"]
        existing = production.x_docket_ids[:1] or Docket.search(
            [
                ("production_id", "=", production.id),
            ],
            limit=1,
        )

        workorder = production.workorder_ids[:1]
        target_workcenter = (
            (workorder.workcenter_id if workorder else False)
            or self.workcenter_id
            or self.so_id.x_workcenter_id
        )
        updates = {}
        docket = existing and existing[0] or False

        if docket:
            if docket.date != local_date:
                updates["date"] = local_date
            if workorder and docket.workorder_id != workorder:
                updates["workorder_id"] = workorder.id
            if target_workcenter and docket.workcenter_id != target_workcenter:
                updates["workcenter_id"] = target_workcenter.id
            if updates:
                docket.write(updates)
        else:
            docket_vals = {
                "so_id": self.so_id.id,
                "production_id": production.id,
                "workorder_id": workorder.id if workorder else False,
                "workcenter_id": target_workcenter.id if target_workcenter else False,
                "date": local_date,
                "docket_no": f"{production.name}-{local_date.strftime('%Y%m%d')}",
                "source": "cron",
                "state": "draft",
            }
            docket = Docket.create(docket_vals)

        if docket.source == "cron" and docket.state != "draft":
            docket.write({"state": "draft"})

    def _gear_get_user_tz(self):
        self.ensure_one()
        tz_name = self.env.context.get("tz") or self.env.user.tz or "UTC"
        try:
            return pytz.timezone(tz_name)
        except Exception:
            return pytz.utc

    @staticmethod
    def _gear_datetime_to_local_date(dt, tz):
        if not dt:
            return False
        if dt.tzinfo:
            dt_utc = dt.astimezone(pytz.utc)
        else:
            dt_utc = pytz.utc.localize(dt)
        return dt_utc.astimezone(tz).date()

    def _gear_get_day_bounds(self, day, tz):
        """Return UTC datetimes that correspond to local midnight → 23:59."""
        local_start = tz.localize(datetime.combine(day, time.min))
        local_end = tz.localize(datetime.combine(day, time(23, 59, 59)))
        return (
            local_start.astimezone(pytz.utc).replace(tzinfo=None),
            local_end.astimezone(pytz.utc).replace(tzinfo=None),
        )

    @api.onchange("so_id")
    def _onchange_so_id(self):
        if not self.so_id:
            return
        primary_product = self.so_id._gear_get_primary_product()
        if primary_product:
            self.product_id = primary_product
        if not self.workcenter_id and self.so_id.x_workcenter_id:
            self.workcenter_id = self.so_id.x_workcenter_id
        contract_start = self.so_id.x_contract_start
        if contract_start:
            start = contract_start.replace(day=1)
            last_day = monthrange(start.year, start.month)[1]
            end = start.replace(day=last_day)
            self.date_start = start
            self.date_end = end

    @api.onchange("date_start")
    def _onchange_date_start(self):
        if self.date_start and (not self.date_end or self.date_end < self.date_start):
            last_day = monthrange(self.date_start.year, self.date_start.month)[1]
            self.date_end = self.date_start.replace(day=last_day)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for order, vals in zip(records, vals_list):
            if not order.product_id and order.so_id:
                product = order.so_id._gear_get_primary_product()
                if product:
                    order.product_id = product
            if not order.workcenter_id and order.so_id.x_workcenter_id:
                order.workcenter_id = order.so_id.x_workcenter_id
            if not order.date_start and order.so_id.x_contract_start:
                start = order.so_id.x_contract_start.replace(day=1)
                last_day = monthrange(start.year, start.month)[1]
                order.date_start = start
                order.date_end = order.date_end or start.replace(day=last_day)
        return records

    def _gear_sync_production_workorders(self, production, workcenter, start_dt, end_dt):
        """Ensure only the current chunk work order exists while queueing the remaining ones."""
        Workorder = self.env["mrp.workorder"]
        param = self.env["ir.config_parameter"].sudo().get_param("gear_on_rent.workorder_max_qty", "7.0")
        try:
            max_chunk = float(param)
        except (TypeError, ValueError):
            max_chunk = 7.0
        if max_chunk <= 0:
            max_chunk = 7.0

        total_qty = float(production.product_qty or 0.0)
        chunks = self._gear_split_quantity(total_qty, max_chunk)

        base_name = f"{production.name} / {workcenter.display_name}"

        entries = []
        for idx, qty in enumerate(chunks):
            seq = idx + 1
            entries.append(
                {
                    "seq": seq,
                    "qty": qty,
                    "name": base_name if len(chunks) == 1 else f"{base_name} ({seq})",
                    "date_start": fields.Datetime.to_string(start_dt) if start_dt else False,
                    "date_finished": fields.Datetime.to_string(end_dt) if end_dt else False,
                }
            )

        max_seq = entries[-1]["seq"] if entries else 0
        done_workorders = production.workorder_ids.filtered(lambda wo: wo.state == "done")
        known_sequences = sorted(seq for seq in done_workorders.mapped("gear_chunk_sequence") if seq)
        if known_sequences:
            next_seq = known_sequences[-1] + 1
        else:
            next_seq = len(done_workorders) + 1

        current_entry = (
            next((entry for entry in entries if entry["seq"] == next_seq), None)
            if next_seq and next_seq <= max_seq
            else None
        )
        pending_entries = [entry for entry in entries if current_entry and entry["seq"] > current_entry["seq"]]
        production.x_pending_workorder_chunks = pending_entries

        active_candidates = production.workorder_ids.filtered(lambda wo: wo.state not in ("done", "cancel"))
        active = (
            active_candidates.filtered(lambda wo: wo.gear_chunk_sequence == next_seq) if current_entry else self.env["mrp.workorder"]
        )
        extras = (active_candidates - active) if active else active_candidates

        if current_entry:
            vals = {
                "name": current_entry["name"],
                "production_id": production.id,
                "workcenter_id": workcenter.id,
                "qty_production": current_entry["qty"],
                "date_start": start_dt,
                "date_finished": end_dt,
                "sequence": current_entry["seq"],
                "gear_chunk_sequence": current_entry["seq"],
                "gear_qty_planned": current_entry["qty"],
            }
            if active:
                target = active[:1]
                if target.state == "progress":
                    safe_vals = dict(vals)
                    safe_vals.pop("date_start", None)
                    safe_vals.pop("date_finished", None)
                    target.write(safe_vals)
                elif target.state not in ("done", "cancel"):
                    target.write(vals)
            else:
                Workorder.create(vals)

        for wo in extras:
            if wo.state in ("done", "cancel", "progress"):
                continue
            if wo.gear_docket_ids:
                try:
                    wo.gear_docket_ids.unlink()
                except Exception:
                    _logger.info(
                        "Skipping removal of work order %s due to linked dockets.",
                        wo.display_name,
                    )
                    continue
            try:
                wo.unlink()
            except Exception:
                _logger.info("Failed to remove surplus work order %s", wo.display_name)

    @staticmethod
    def _gear_split_quantity(total_qty, max_chunk):
        """Split quantity into chunks capped by max_chunk, returning at least one entry."""
        if max_chunk <= 0:
            return [round(total_qty or 0.0, 2)]
        total_qty = round(total_qty or 0.0, 2)
        if total_qty <= 0:
            return [0.0]

        parts = int(ceil(total_qty / max_chunk))
        quantities = []
        remaining = total_qty
        for _ in range(parts):
            chunk = max_chunk if remaining > max_chunk else remaining
            quantities.append(round(chunk, 2))
            remaining = round(remaining - chunk, 2)
        # Correct final chunk to ensure sum equals total
        adjustment = round(total_qty - sum(quantities), 2)
        if quantities:
            quantities[-1] = round(quantities[-1] + adjustment, 2)
        return quantities or [0.0]

    @api.model
    def _cron_schedule_due_orders(self):
        """Scheduled task to generate daily orders as windows progress."""
        today = fields.Date.context_today(self)
        domain = [
            ("state", "!=", "done"),
            ("date_start", "<=", today),
            ("date_end", ">=", today),
        ]
        orders = self.search(domain)
        if not orders:
            return
        for order in orders:
            try:
                order.action_schedule_orders(until_date=today)
            except Exception:
                _logger.exception("Failed to schedule monthly order %s", order.display_name)

    def action_open_prepare_invoice(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "gear.prepare.invoice.mrp",
            "view_mode": "form",
            "view_id": self.env.ref("gear_on_rent.view_prepare_invoice_from_mrp_form").id,
            "target": "new",
            "context": {
                "default_monthly_order_id": self.id,
                "default_invoice_date": fields.Date.context_today(self),
            },
        }
