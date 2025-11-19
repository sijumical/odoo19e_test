from collections import defaultdict
from calendar import monthrange
from datetime import datetime, time, timedelta

try:  # pragma: no cover - keep optional deps optional in CI
    from dateutil.relativedelta import relativedelta
except ModuleNotFoundError:  # pragma: no cover
    from odoo_shims.relativedelta import relativedelta

try:  # pragma: no cover - fallback shim for lightweight runtimes
    import pytz
except ModuleNotFoundError:  # pragma: no cover
    from odoo_shims import pytz

from odoo import _, api, fields, models
from odoo.osv.expression import AND, OR


class SaleOrder(models.Model):
    """Extends sale orders with Gear On Rent contract settings."""

    _inherit = "sale.order"

    x_billing_category = fields.Selection(
        selection=[
            ("rental", "Rental"),
            ("rmc", "RMC"),
            ("plant", "Plant"),
        ],
        string="Billing Category",
        default="rental",
        tracking=True,
    )
    x_workcenter_id = fields.Many2one(
        comodel_name="mrp.workcenter",
        string="Primary Work Center",
        help="Work center that will receive IDS telemetry for this contract.",
        tracking=True,
    )
    x_monthly_mgq = fields.Float(
        string="Monthly MGQ",
        digits=(16, 2),
        tracking=True,
    )
    x_loto_waveoff_hours = fields.Float(
        string="Monthly LOTO Wave-Off Allowance",
        digits=(16, 2),
        default=48.0,
        tracking=True,
    )
    x_contract_start = fields.Date(string="Contract Start", tracking=True)
    x_contract_end = fields.Date(string="Contract End", tracking=True)
    x_cooling_period_months = fields.Integer(
        string="Cooling Period (Months)",
        default=3,
        help="Number of months to keep returned assets on hold before the contract can restart.",
        tracking=True,
    )
    x_cooling_end = fields.Datetime(
        string="Cooling Ends",
        compute="_compute_x_cooling_end",
        store=True,
        help="Last day of the cooling period window.",
    )
    gear_ngt_relief_days = fields.Float(
        string="NGT Relief (Days)",
        digits=(16, 2),
        default=0.0,
        tracking=True,
    )
    gear_loto_relief_days = fields.Float(
        string="LOTO Relief (Days)",
        digits=(16, 2),
        default=0.0,
        tracking=True,
    )
    gear_materials_shortage_note = fields.Text(string="Materials Shortage Notes")
    gear_manpower_note = fields.Text(string="Manpower Notes")
    gear_asset_note = fields.Text(string="Asset Notes")

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._gear_sync_billing_category()
        return orders

    def write(self, vals):
        res = super().write(vals)
        if "order_line" in vals:
            self._gear_sync_billing_category()
        return res

    def action_confirm(self):
        res = super().action_confirm()
        rmc_orders = self.filtered(lambda o: o.x_billing_category == "rmc")
        if rmc_orders:
            rmc_orders._gear_sync_production_defaults()
            rmc_orders.gear_generate_monthly_orders(limit=1)
        return res

    @api.onchange("order_line")
    def _onchange_order_line_update_category(self):
        self._gear_sync_billing_category()

    def _gear_get_primary_product(self):
        self.ensure_one()
        line = self.order_line.filtered(
            lambda l: not l.display_type and l.product_id and l.product_id.gear_is_production
        )[:1]
        if not line:
            line = self.order_line.filtered(lambda l: not l.display_type)[:1]
        return line.product_id

    def _gear_get_timezone(self):
        self.ensure_one()
        tz_name = self.env.context.get("tz") or self.env.user.tz or "UTC"
        try:
            return pytz.timezone(tz_name)
        except Exception:
            return pytz.utc

    def _gear_localize_day(self, day, is_end=False, tz=None):
        tz = tz or self._gear_get_timezone()
        boundary = time(23, 59, 59) if is_end else time.min
        return tz.localize(datetime.combine(day, boundary))

    def _gear_db_to_local(self, dt, tz=None):
        tz = tz or self._gear_get_timezone()
        if not dt:
            return False
        if dt.tzinfo:
            return dt.astimezone(tz)
        return pytz.utc.localize(dt).astimezone(tz)

    @staticmethod
    def _gear_local_to_utc(dt):
        if not dt:
            return False
        return dt.astimezone(pytz.utc).replace(tzinfo=None)

    def gear_generate_monthly_orders(self, date_start=None, date_end=None, limit=None):
        """Ensure monthly orders and daily MOs exist for the contract window."""
        MonthlyOrder = self.env["gear.rmc.monthly.order"]
        try:
            limit = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            limit = None
        if limit is not None and limit <= 0:
            limit = None

        date_start = fields.Date.to_date(date_start) if date_start else None
        date_end = fields.Date.to_date(date_end) if date_end else None

        for order in self.filtered(lambda s: s.x_billing_category == "rmc"):
            if not order.x_contract_start or not order.x_contract_end:
                continue
            product = order._gear_get_primary_product()
            if not product:
                continue
            if not order.x_monthly_mgq or order.x_monthly_mgq <= 0:
                order.message_post(
                    body=_("Monthly MGQ is required to generate daily orders. Please set a positive value."),
                    subtype_xmlid="mail.mt_note",
                )
                continue
            windows = order._gear_iter_monthly_windows(order.x_contract_start, order.x_contract_end)
            if date_start or date_end:
                filtered_windows = [
                    window
                    for window in windows
                    if not (date_end and window["date_start"] > date_end)
                    and not (date_start and window["date_end"] < date_start)
                ]
            else:
                filtered_windows = list(windows)

            if not filtered_windows:
                continue
            if limit is not None:
                filtered_windows = filtered_windows[:limit]
            managed_orders = self.env["gear.rmc.monthly.order"]
            for window in filtered_windows:
                monthly = MonthlyOrder.search(
                    [
                        ("so_id", "=", order.id),
                        ("date_start", "=", window["date_start"]),
                    ],
                    limit=1,
                )
                mgq_total = order.x_monthly_mgq or 0.0
                month_hours = window.get("month_hours") or 0.0
                window_hours = window.get("window_hours") or 0.0
                if month_hours:
                    ratio = window_hours / month_hours
                else:
                    span_days = window["span_days"]
                    month_days = window["month_days"]
                    ratio = span_days / month_days if month_days else 1.0
                snapshot = mgq_total * ratio if mgq_total else 0.0
                vals = {
                    "so_id": order.id,
                    "product_id": product.id,
                    "workcenter_id": order.x_workcenter_id.id or product.gear_workcenter_id.id,
                    "date_start": window["date_start"],
                    "date_end": window["date_end"],
                    "x_window_start": window["window_start"],
                    "x_window_end": window["window_end"],
                    "x_is_cooling_period": window["is_cooling"],
                    "x_monthly_mgq_snapshot": snapshot,
                }
                if monthly:
                    monthly.write(vals)
                else:
                    monthly = MonthlyOrder.create(vals)
                managed_orders |= monthly
            all_orders = MonthlyOrder.search([("so_id", "=", order.id)])
            all_orders._gear_reassign_productions_to_windows()
            today = fields.Date.context_today(order)
            current_orders = managed_orders.filtered(
                lambda m: m.state != "done"
                and m.date_start
                and m.date_end
                and m.date_start <= today <= m.date_end
            )
            if not current_orders:
                past_orders = managed_orders.filtered(
                    lambda m: m.state != "done"
                    and m.date_end
                    and m.date_end < today
                )
                current_orders = past_orders.sorted(key=lambda m: m.date_end or fields.Date.today(), reverse=True)[:1]
            for monthly in current_orders:
                has_locked_mo = monthly.production_ids.filtered(lambda p: p.state in ("done", "cancel"))
                has_locked_wo = monthly.production_ids.mapped("workorder_ids").filtered(lambda wo: wo.state in ("done", "cancel"))
                if monthly.state != "done" and not has_locked_mo and not has_locked_wo:
                    monthly.action_schedule_orders(until_date=today)

    def gear_generate_next_monthly_order(self, horizon_days=1):
        """Create the next missing monthly order when the window is imminent or previous is done."""
        MonthlyOrder = self.env["gear.rmc.monthly.order"]
        today = fields.Date.context_today(self)
        try:
            horizon_days = int(horizon_days)
        except (TypeError, ValueError):
            horizon_days = 0
        if horizon_days < 0:
            horizon_days = 0
        horizon_date = today + timedelta(days=horizon_days)

        for order in self.filtered(lambda s: s.x_billing_category == "rmc"):
            if not order.x_contract_start or not order.x_contract_end:
                continue

            windows = order._gear_iter_monthly_windows(order.x_contract_start, order.x_contract_end)
            if not windows:
                continue

            existing_orders = MonthlyOrder.search([("so_id", "=", order.id)])
            existing_by_start = {monthly.date_start: monthly for monthly in existing_orders}

            for idx, window in enumerate(windows):
                start_date = window["date_start"]
                if start_date in existing_by_start:
                    continue

                prev_window = windows[idx - 1] if idx > 0 else None
                prev_order = existing_by_start.get(prev_window["date_start"]) if prev_window else None

                should_create = False
                if prev_order and prev_order.state == "done":
                    should_create = True
                if start_date and start_date <= today:
                    should_create = True
                if start_date and start_date <= horizon_date:
                    should_create = True

                if should_create:
                    order.gear_generate_monthly_orders(
                        date_start=start_date,
                        date_end=window["date_end"],
                        limit=1,
                    )
                    new_monthly = MonthlyOrder.search(
                        [
                            ("so_id", "=", order.id),
                            ("date_start", "=", start_date),
                        ],
                        limit=1,
                    )
                    if new_monthly:
                        existing_by_start[start_date] = new_monthly

    @api.model
    def _cron_generate_next_monthly_orders(self):
        """Nightly job to ensure the upcoming monthly WMO exists."""
        domain = [
            ("state", "in", ["sale", "done"]),
            ("x_billing_category", "=", "rmc"),
            ("x_contract_start", "!=", False),
            ("x_contract_end", "!=", False),
        ]
        orders = self.search(domain)
        if orders:
            orders.gear_generate_next_monthly_order()

    def _gear_iter_monthly_windows(self, start_date, end_date):
        """Return dictionaries describing each monthly window, splitting on cooling transitions."""
        self.ensure_one()
        if not start_date or not end_date:
            return []

        contract_start_dt = self._gear_get_contract_start_datetime()
        cooling_end_dt = self.x_cooling_end
        tz = self._gear_get_timezone()
        contract_start_local = self._gear_db_to_local(contract_start_dt, tz)
        cooling_end_local = self._gear_db_to_local(cooling_end_dt, tz)
        current = start_date.replace(day=1)
        limit = end_date
        windows = []

        def compute_hours(start_dt, end_dt):
            if not start_dt or not end_dt or end_dt < start_dt:
                return 0.0
            delta_seconds = (end_dt - start_dt).total_seconds() + 1.0
            return max(delta_seconds / 3600.0, 0.0)

        while current <= limit:
            month_days = monthrange(current.year, current.month)[1]
            month_start = current
            month_end = current.replace(day=month_days)
            window_start = month_start if month_start >= start_date else start_date
            window_end = month_end if month_end <= end_date else end_date
            if window_start > window_end:
                current = (current + relativedelta(months=1)).replace(day=1)
                continue

            month_start_local = self._gear_localize_day(month_start, tz=tz)
            month_end_local = self._gear_localize_day(month_end, is_end=True, tz=tz)
            month_hours = compute_hours(month_start_local, month_end_local)

            midnight_start_local = self._gear_localize_day(window_start, tz=tz)
            if contract_start_local and contract_start_local.date() == window_start:
                start_local = min(midnight_start_local, contract_start_local)
            else:
                start_local = midnight_start_local
            end_local = self._gear_localize_day(window_end, is_end=True, tz=tz)
            default_span_days = (window_end - window_start).days + 1

            if cooling_end_local and start_local <= cooling_end_local <= end_local:
                first_end_local = min(cooling_end_local, end_local)
                first_end_date = min(window_end, first_end_local.date())
                if first_end_date >= window_start:
                    first_span_days = (first_end_date - window_start).days + 1
                    windows.append(
                        {
                            "date_start": window_start,
                            "date_end": first_end_date,
                            "window_start": self._gear_local_to_utc(start_local),
                            "window_end": self._gear_local_to_utc(first_end_local),
                            "is_cooling": True,
                            "month_days": month_days,
                            "span_days": first_span_days,
                            "month_hours": month_hours,
                            "window_hours": compute_hours(start_local, first_end_local),
                        }
                    )
                after_cooling_date = first_end_date + timedelta(days=1)
                if after_cooling_date <= window_end:
                    second_start_local = max(
                        cooling_end_local + timedelta(seconds=1),
                        self._gear_localize_day(after_cooling_date, tz=tz),
                    )
                    second_span_days = (window_end - after_cooling_date).days + 1
                    if second_span_days > 0:
                        windows.append(
                            {
                                "date_start": after_cooling_date,
                                "date_end": window_end,
                                "window_start": self._gear_local_to_utc(second_start_local),
                                "window_end": self._gear_local_to_utc(end_local),
                                "is_cooling": False,
                                "month_days": month_days,
                                "span_days": second_span_days,
                                "month_hours": month_hours,
                                "window_hours": compute_hours(second_start_local, end_local),
                            }
                        )
            else:
                is_cooling = bool(cooling_end_local and end_local <= cooling_end_local)
                windows.append(
                    {
                        "date_start": window_start,
                        "date_end": window_end,
                        "window_start": self._gear_local_to_utc(start_local),
                        "window_end": self._gear_local_to_utc(end_local),
                        "is_cooling": is_cooling,
                        "month_days": month_days,
                        "span_days": default_span_days,
                        "month_hours": month_hours,
                        "window_hours": compute_hours(start_local, end_local),
                    }
                )

            current = (current + relativedelta(months=1)).replace(day=1)

        return windows

    def _gear_has_production_products(self):
        self.ensure_one()
        return any(
            line.product_id.gear_is_production
            for line in self.order_line
            if not line.display_type and line.product_id
        )

    def _gear_sync_billing_category(self):
        for order in self:
            has_production = order._gear_has_production_products()
            if has_production:
                if order.x_billing_category != "rmc":
                    order.x_billing_category = "rmc"
                order._gear_sync_production_defaults()
            elif order.x_billing_category == "rmc":
                order.x_billing_category = "rental"

    def _gear_sync_production_defaults(self):
        for order in self.filtered(lambda o: o.x_billing_category == "rmc"):
            production_lines = order.order_line.filtered(
                lambda l: not l.display_type and l.product_id and l.product_id.gear_is_production
            )
            if not production_lines:
                continue

            total_qty = sum(production_lines.mapped("product_uom_qty"))
            if total_qty > 0 and (not order.x_monthly_mgq or order.x_monthly_mgq <= 0):
                order.x_monthly_mgq = total_qty

            if not order.x_workcenter_id:
                workcenters = production_lines.mapped("product_id.gear_workcenter_id")
                if workcenters:
                    order.x_workcenter_id = workcenters[0]

            start_dates = [fields.Date.to_date(dt) for dt in production_lines.mapped("start_date") if dt]
            end_dates = [fields.Date.to_date(dt) for dt in production_lines.mapped("return_date") if dt]

            if start_dates:
                min_start = min(start_dates)
                if not order.x_contract_start or order.x_contract_start > min_start:
                    order.x_contract_start = min_start
            if end_dates:
                max_end = max(end_dates)
                if not order.x_contract_end or order.x_contract_end < max_end:
                    order.x_contract_end = max_end

    @api.depends(
        "date_order",
        "x_cooling_period_months",
        "order_line.is_rental",
        "order_line.start_date",
        "order_line.reservation_begin",
    )
    def _compute_x_cooling_end(self):
        for order in self:
            contract_start = order._gear_get_contract_start_datetime()
            months = order.x_cooling_period_months
            if not contract_start or months is None:
                order.x_cooling_end = False
                continue
            order.x_cooling_end = contract_start + relativedelta(months=months, days=-1)

    def _gear_get_contract_start_datetime(self):
        self.ensure_one()
        contract_start = False
        renting_lines = self.order_line.filtered(lambda line: getattr(line, "is_rental", False))
        if renting_lines:
            line_fields = renting_lines._fields
            for field_name in ("start_date", "reservation_begin"):
                if field_name in line_fields:
                    values = [dt for dt in renting_lines.mapped(field_name) if dt]
                    if values:
                        contract_start = min(values)
                        break
        return contract_start or self.date_order

    def gear_register_ngt(self, request):
        """Distribute NGT relief across the impacted daily manufacturing orders."""
        self.ensure_one()
        productions = self._gear_get_productions_between(request.date_start, request.date_end)
        for production in productions:
            hours = self._gear_overlap_hours(production, request.date_start, request.date_end)
            if hours:
                production.gear_allocate_relief_hours(hours, "ngt")

    def gear_register_loto(self, request):
        """Apply LOTO relief and compute the wave-off utilisation."""
        self.ensure_one()
        productions = self._gear_get_productions_between(request.date_start, request.date_end)
        grouped = defaultdict(list)
        for production in productions:
            grouped[production.x_monthly_order_id].append(production)

        total_waveoff = 0.0
        total_chargeable = 0.0

        for monthly_order, items in grouped.items():
            if not monthly_order:
                continue
            allowance = self.x_loto_waveoff_hours or 0.0
            used = monthly_order.waveoff_hours_applied or 0.0
            remaining_waveoff = max(allowance - used, 0.0)
            for production in sorted(items, key=lambda p: p.date_start or datetime.min):
                hours = self._gear_overlap_hours(production, request.date_start, request.date_end)
                if not hours:
                    continue
                waveoff_applied = min(remaining_waveoff, hours)
                chargeable = hours - waveoff_applied
                production.gear_allocate_relief_hours(hours, "loto")
                production.gear_apply_loto_waveoff(waveoff_applied, chargeable)
                total_waveoff += waveoff_applied
                total_chargeable += chargeable
                remaining_waveoff -= waveoff_applied
        remainder = round(request.hours_total - (total_waveoff + total_chargeable), 2)
        if remainder > 0:
            total_chargeable += remainder
        return total_waveoff, total_chargeable

    def _gear_get_productions_between(self, start_dt, end_dt):
        Production = self.env["mrp.production"]
        range_domain = AND(
            [
                [("x_sale_order_id", "in", self.ids)],
                OR(
                    [
                        [("date_finished", "=", False)],
                        [("date_finished", ">=", start_dt)],
                    ]
                ),
                OR(
                    [
                        [("date_start", "=", False)],
                        [("date_start", "<=", end_dt)],
                    ]
                ),
            ]
        )
        productions = Production.search(range_domain, order="date_start asc, id asc")
        # Filter out any productions that still do not overlap once their window is inferred.
        return productions.filtered(
            lambda production: self._gear_overlap_hours(production, start_dt, end_dt) > 0.0
        )

    @staticmethod
    def _gear_infer_production_window(production):
        """Return a best-effort (start, end) tuple for the production window."""
        tz_name = production.env.context.get("tz") or production.env.user.tz or "UTC"
        try:
            user_tz = pytz.timezone(tz_name)
        except Exception:
            user_tz = pytz.utc

        def to_local(dt):
            if not dt:
                return None
            if dt.tzinfo:
                dt_utc = dt.astimezone(pytz.utc)
            else:
                dt_utc = pytz.utc.localize(dt)
            return dt_utc.astimezone(user_tz)

        def to_utc(local_dt):
            return local_dt.astimezone(pytz.utc).replace(tzinfo=None)

        start = production.date_start or getattr(production, "date_planned_start", False)
        end = production.date_finished or getattr(production, "date_planned_finished", False)

        inferred_date = False
        local_start = to_local(start)
        local_end = to_local(end)
        if local_start:
            inferred_date = local_start.date()
        elif local_end:
            inferred_date = local_end.date()
        elif production.name and "-" in production.name:
            suffix = production.name.rsplit("-", 1)[-1]
            try:
                inferred_date = datetime.strptime(suffix, "%Y%m%d").date()
            except ValueError:
                inferred_date = False

        if inferred_date:
            day_start = user_tz.localize(datetime.combine(inferred_date, time.min))
            day_end = user_tz.localize(datetime.combine(inferred_date, time(23, 59, 59)))
            if not start:
                start = to_utc(day_start)
            else:
                start = min(start, to_utc(day_start))
            if not end:
                end = to_utc(day_end)
            else:
                end = max(end, to_utc(day_end))

        return start, end

    @staticmethod
    def _gear_overlap_hours(production, start_dt, end_dt):
        start, end = SaleOrder._gear_infer_production_window(production)
        start = start or start_dt
        end = end or end_dt
        window_start = max(start_dt, start)
        window_end = min(end_dt, end)
        if window_end <= window_start:
            return 0.0
        delta = window_end - window_start
        return round(delta.total_seconds() / 3600.0, 2)


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        orders = lines.mapped("order_id")
        orders._gear_sync_billing_category()
        orders._gear_sync_production_defaults()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if any(field in vals for field in ["product_id", "product_template_id", "display_type", "product_uom_qty", "start_date", "return_date"]):
            orders = self.mapped("order_id")
            orders._gear_sync_billing_category()
            orders._gear_sync_production_defaults()
        return res

    def unlink(self):
        orders = self.mapped("order_id")
        res = super().unlink()
        orders._gear_sync_billing_category()
        orders._gear_sync_production_defaults()
        return res
