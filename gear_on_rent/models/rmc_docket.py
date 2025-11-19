from datetime import datetime, time
from math import ceil
import random
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class GearRmcDocket(models.Model):
    """RMC production docket captured per work order; expanded for operator entry."""

    _name = "gear.rmc.docket"
    _description = "RMC Production Docket"
    _order = "date desc, docket_no desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "docket_no"

    so_id = fields.Many2one(
        comodel_name="sale.order",
        string="Contract / SO",
        required=True,
        index=True,
        ondelete="cascade",
        tracking=True,
    )
    production_id = fields.Many2one(
        comodel_name="mrp.production",
        string="Manufacturing Order",
        index=True,
        ondelete="set null",
        tracking=True,
    )
    workorder_id = fields.Many2one(
        comodel_name="mrp.workorder",
        string="Work Order",
        index=True,
        ondelete="set null",
        tracking=True,
    )
    workcenter_id = fields.Many2one(
        comodel_name="mrp.workcenter",
        string="Work Center",
        compute="_compute_workcenter",
        store=True,
        readonly=False,
        tracking=True,
    )
    monthly_order_id = fields.Many2one(
        comodel_name="gear.rmc.monthly.order",
        string="Monthly Work Order",
        compute="_compute_monthly_order",
        store=True,
        readonly=False,
        index=True,
        tracking=True,
    )

    docket_no = fields.Char(string="Docket Number", required=True, tracking=True)
    name = fields.Char(string="Reference", default="New", copy=False)
    date = fields.Date(string="Production Date", required=True, default=fields.Date.context_today, tracking=True)
    payload_timestamp = fields.Datetime(string="Telemetry Timestamp", help="When telemetry was received.")

    qty_m3 = fields.Float(string="Quantity (m³)", digits=(16, 2), tracking=True)
    slump = fields.Char(string="Slump")
    runtime_minutes = fields.Float(string="Runtime (min)", digits=(16, 2))
    idle_minutes = fields.Float(string="Idle (min)", digits=(16, 2))
    alarm_codes = fields.Json(string="IDS Alarms", help="Telemetry alarms raised for this docket.", default=list)

    helpdesk_ticket_id = fields.Many2one("helpdesk.ticket", string="Ticket")
    subcontractor_name = fields.Char(string="Subcontractor")
    transport_reference = fields.Char(string="Transport Reference")
    plant_reference = fields.Char(string="Plant Reference")
    notes = fields.Text(string="Notes")
    attachment_ids = fields.Many2many(
        comodel_name="ir.attachment",
        relation="gear_rmc_docket_attachment_rel",
        column1="docket_id",
        column2="attachment_id",
        string="Attachments",
    )

    company_id = fields.Many2one("res.company", related="so_id.company_id", store=True, readonly=True)

    source = fields.Selection(
        selection=[
            ("ids", "IDS"),
            ("manual", "Manual"),
            ("cron", "Cron Adjustment"),
        ],
        string="Captured By",
        default="manual",
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("in_production", "In Production"),
            ("ready", "Ready"),
            ("dispatched", "Dispatched"),
            ("delivered", "Delivered"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    recipe_id = fields.Many2one("mrp.bom", string="Recipe")
    concrete_grade = fields.Char(string="Concrete Grade", compute="_compute_concrete_grade", store=True)
    product_id = fields.Many2one("product.product", string="Product", compute="_compute_product", store=True)
    is_rmc_product = fields.Boolean(string="Is RMC Product", compute="_compute_is_rmc_product", store=True)

    quantity_ordered = fields.Float(string="Quantity Ordered (m³)")
    quantity_produced = fields.Float(string="Quantity Produced (m³)")
    cumulative_quantity = fields.Float(string="Cumulative Quantity (m³)")
    quantity_ticket = fields.Float(string="Ticket Quantity (m³)", compute="_compute_quantity_ticket", store=True)

    pour_structure = fields.Selection(
        [
            ("rcc", "RCC"),
            ("pcc", "PCC"),
            ("foundation", "Foundation"),
            ("slab", "Slab"),
            ("beam", "Beam"),
            ("column", "Column"),
        ],
        string="Pour Structure",
        default="rcc",
    )
    batching_time = fields.Datetime(string="Batching Time")
    water_ratio_actual = fields.Float(string="Actual Water Ratio")
    slump_flow_actual = fields.Float(string="Actual Slump/Flow (mm)")
    current_capacity = fields.Float(string="Current Capacity (m³/batch)")
    tm_number = fields.Char(string="TM Number")
    driver_name = fields.Char(string="Driver Name")

    docket_batch_count = fields.Integer(compute="_compute_counts", string="Batches")
    vendor_bill_count = fields.Integer(compute="_compute_counts", string="Vendor Bills")
    invoice_id = fields.Many2one("account.move", string="Customer Invoice", readonly=True)
    invoice_count = fields.Integer(compute="_compute_counts", string="Customer Invoices")

    docket_line_ids = fields.One2many("gear.rmc.docket.line", "docket_id", string="Docket Lines")
    docket_batch_ids = fields.One2many("gear.rmc.docket.batch", "docket_id", string="Batches")
    batch_variance_tolerance = fields.Float(string="Batch Variance Tolerance (%)", default=2.0)

    operator_user_id = fields.Many2one("res.users", string="Operator User")
    operator_portal_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
        ],
        default="pending",
        tracking=True,
    )
    operator_completion_time = fields.Datetime(string="Operator Completion Time")
    operator_notes = fields.Text(string="Operator Notes")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("unique_docket_per_contract", "unique(so_id, docket_no)", "The docket number must be unique per contract."),
    ]

    @api.depends("workorder_id.workcenter_id")
    def _compute_workcenter(self):
        for docket in self:
            docket.workcenter_id = docket.workorder_id.workcenter_id

    @api.depends("production_id", "production_id.x_monthly_order_id")
    def _compute_monthly_order(self):
        for docket in self:
            docket.monthly_order_id = docket.production_id.x_monthly_order_id or False

    @api.depends("helpdesk_ticket_id")
    def _compute_quantity_ticket(self):
        for docket in self:
            docket.quantity_ticket = float(getattr(docket.helpdesk_ticket_id, "rmc_quantity", 0.0) or 0.0)

    @api.depends("so_id.order_line.product_id")
    def _compute_product(self):
        for docket in self:
            product = False
            if docket.so_id and docket.so_id.order_line:
                product = docket.so_id.order_line.filtered(lambda l: not l.display_type)[:1].product_id
            docket.product_id = product

    @api.depends("so_id.order_line.product_id")
    def _compute_is_rmc_product(self):
        for docket in self:
            if docket.so_id:
                templates = docket.so_id.order_line.filtered(lambda l: not l.display_type).mapped("product_id.product_tmpl_id")
                docket.is_rmc_product = any(getattr(tmpl, "gear_is_production", False) for tmpl in templates)
            else:
                docket.is_rmc_product = False

    @api.depends("recipe_id", "product_id", "so_id.order_line")
    def _compute_concrete_grade(self):
        pattern = re.compile(r"M\s*\d+", re.IGNORECASE)
        for docket in self:
            grade = False
            if docket.recipe_id:
                grade = getattr(docket.recipe_id, "concrete_grade", False)
                if not grade:
                    match = pattern.search(docket.recipe_id.display_name or "")
                    if match:
                        grade = match.group(0).replace(" ", "").upper()
            if not grade and docket.product_id:
                tmpl = docket.product_id.product_tmpl_id
                grade = getattr(tmpl, "concrete_grade", False)
            if not grade and docket.product_id:
                match = pattern.search((docket.product_id.display_name or "") + " " + (docket.product_id.name or ""))
                if match:
                    grade = match.group(0).replace(" ", "").upper()
            if not grade and docket.so_id:
                for line in docket.so_id.order_line:
                    src = ((line.product_id and line.product_id.display_name) or "") + " " + (line.name or "")
                    match = pattern.search(src)
                    if match:
                        grade = match.group(0).replace(" ", "").upper()
                        break
            docket.concrete_grade = grade or ""

    def _compute_counts(self):
        AccountMove = self.env["account.move"]
        for docket in self:
            docket.docket_batch_count = len(docket.docket_batch_ids)
            docket.invoice_count = 1 if docket.invoice_id else 0
            if docket.workorder_id:
                docket.vendor_bill_count = AccountMove.search_count(
                    [
                        ("move_type", "=", "in_invoice"),
                        ("invoice_origin", "ilike", docket.workorder_id.name or ""),
                    ]
                )
            else:
                docket.vendor_bill_count = 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("gear.rmc.docket") or "New"
            if vals.get("docket_no") and not vals.get("state"):
                vals["state"] = "in_production"
        records = super().create(vals_list)
        for record, vals in zip(records, vals_list):
            record._gear_backfill_links(vals)
            if record.recipe_id and not record.docket_line_ids:
                record._apply_recipe_lines()
            if record.current_capacity:
                record._generate_batches_safe()
        return records

    def write(self, vals):
        res = super().write(vals)
        relevant_keys = {"production_id", "workorder_id", "so_id", "date", "payload_timestamp", "qty_m3"}
        if relevant_keys.intersection(vals):
            for docket in self:
                docket._gear_backfill_links(vals)
                docket._gear_sync_workorder_quantities()
        if "recipe_id" in vals:
            self._apply_recipe_lines()
        if "current_capacity" in vals or "quantity_ordered" in vals:
            self._generate_batches_safe()
        if "docket_no" in vals and vals.get("docket_no"):
            for docket in self.filtered(lambda d: d.state == "draft"):
                docket.state = "in_production"
        return res

    def _gear_backfill_links(self, initial_vals=None):
        self.ensure_one()
        initial_vals = initial_vals or {}
        if not self.so_id and self.production_id:
            self.so_id = self.production_id.x_sale_order_id
        if not self.monthly_order_id and self.production_id:
            self.monthly_order_id = self.production_id.x_monthly_order_id
        if not self.production_id and self.workorder_id:
            self.production_id = self.workorder_id.production_id
        if not self.workcenter_id and self.workorder_id:
            self.workcenter_id = self.workorder_id.workcenter_id
        if self.workorder_id and self.workorder_id.date_start:
            wo_start = fields.Datetime.to_datetime(self.workorder_id.date_start)
            current_ts = fields.Datetime.to_datetime(self.payload_timestamp) if self.payload_timestamp else False
            if current_ts != wo_start:
                self.payload_timestamp = wo_start
            target_date = False
            monthly_order = self.monthly_order_id or (self.production_id and self.production_id.x_monthly_order_id)
            if monthly_order:
                try:
                    user_tz = monthly_order._gear_get_user_tz()
                    target_date = monthly_order._gear_datetime_to_local_date(wo_start, user_tz)
                except Exception:
                    target_date = False
            if not target_date:
                local_dt = fields.Datetime.context_timestamp(self.workorder_id, wo_start)
                target_date = local_dt.date() if local_dt else fields.Date.context_today(self)
            if target_date and self.date != target_date:
                self.date = target_date
        if not self.payload_timestamp:
            timestamp = False
            wo_start = self.workorder_id.date_start if self.workorder_id else False
            if wo_start:
                timestamp = wo_start
            elif initial_vals.get("payload_timestamp"):
                timestamp = initial_vals["payload_timestamp"]
            elif initial_vals.get("date"):
                date_value = fields.Date.to_date(initial_vals["date"])
                if date_value:
                    timestamp = datetime.combine(date_value, time.min)
            if timestamp:
                self.payload_timestamp = timestamp
        if not self.quantity_ordered and self.so_id:
            qty = sum(self.so_id.order_line.filtered(lambda l: not l.display_type).mapped("product_uom_qty"))
            self.quantity_ordered = qty

    def _gear_sync_workorder_quantities(self):
        self.ensure_one()
        if not self.workorder_id:
            return
        produced = sum(
            self.workorder_id.gear_docket_ids.filtered(lambda d: d.state != "cancel").mapped("qty_m3")
        )
        candidate_qty = produced or sum(
            self.workorder_id.gear_docket_ids.filtered(lambda d: d.state != "cancel").mapped("quantity_produced")
        ) or 0.0
        if candidate_qty and self.workorder_id.qty_produced != candidate_qty:
            self.workorder_id.write({"qty_produced": candidate_qty})

    def _apply_recipe_lines(self):
        for docket in self:
            if not docket.recipe_id:
                continue
            commands = [(5, 0, 0)]
            for bom_line in docket.recipe_id.bom_line_ids:
                commands.append(
                    (
                        0,
                        0,
                        {
                            "material_name": bom_line.product_id.name,
                            "material_code": getattr(bom_line, "product_code", False) or bom_line.product_id.default_code,
                            "design_qty": bom_line.product_qty,
                        },
                    )
                )
            docket.docket_line_ids = commands

    def _generate_batches_safe(self):
        try:
            self._generate_batches()
        except Exception:
            pass

    def _generate_batches(self):
        Batch = self.env["gear.rmc.docket.batch"]
        for docket in self:
            total_qty = float(docket.quantity_ordered or 0.0)
            capacity = float(docket.current_capacity or 0.0)
            tol_pct = float(docket.batch_variance_tolerance or 2.0) / 100.0
            if total_qty <= 0 or capacity <= 0:
                continue

            recipe_lines = []
            if docket.docket_line_ids:
                for line in docket.docket_line_ids:
                    recipe_lines.append({"name": line.material_name or line.material_code or "", "per_cum_qty": line.design_qty or 0.0})
            elif docket.recipe_id:
                for bl in docket.recipe_id.bom_line_ids:
                    recipe_lines.append({"name": bl.product_id.name, "per_cum_qty": bl.product_qty or 0.0})
            else:
                raise UserError(_("No recipe found on the docket. Please set a recipe before generating batches."))

            docket.docket_batch_ids.unlink()

            num_batches = int(ceil(total_qty / capacity))
            volumes = []
            for idx in range(1, num_batches + 1):
                if idx < num_batches:
                    factor = 1.0 + random.uniform(-tol_pct, tol_pct) if tol_pct > 0 else 1.0
                    volumes.append(max(capacity * factor, 0.0))
                else:
                    volumes.append(0.0)
            if len(volumes) > 1:
                volumes[-1] = max(0.0, total_qty - sum(volumes[:-1]))
            else:
                volumes[-1] = total_qty

            total = sum(volumes)
            if total and total != total_qty:
                scale = total_qty / total
                volumes = [v * scale for v in volumes]

            material_total = {rl["name"]: rl["per_cum_qty"] * total_qty for rl in recipe_lines}
            material_running = {name: 0.0 for name in material_total}

            for idx, volume in enumerate(volumes, start=1):
                mat_vals = {
                    "ten_mm": 0.0,
                    "twenty_mm": 0.0,
                    "facs": 0.0,
                    "water_batch": 0.0,
                    "flyash": 0.0,
                    "adm_plast": 0.0,
                    "waterr": 0.0,
                }
                for rl in recipe_lines:
                    name = rl["name"]
                    per_cum = rl.get("per_cum_qty", 0.0)
                    qty = per_cum * volume
                    if idx == len(volumes):
                        qty = material_total[name] - material_running[name]
                    material_running[name] += qty
                    lname = (name or "").lower()
                    if "10" in lname and "20" not in lname:
                        mat_vals["ten_mm"] += qty
                    elif "20" in lname:
                        mat_vals["twenty_mm"] += qty
                    elif "fly" in lname:
                        mat_vals["flyash"] += qty
                    elif "water" in lname or "h2o" in lname:
                        mat_vals["water_batch"] += qty
                        mat_vals["waterr"] += qty
                    elif "adm" in lname:
                        mat_vals["adm_plast"] += qty
                    else:
                        mat_vals["facs"] += qty

                Batch.create(
                    {
                        "docket_id": docket.id,
                        "batch_code": f"Batch-{idx:03d}",
                        "batch_sequence": idx,
                        "quantity_ordered": volume,
                        "ten_mm": mat_vals["ten_mm"],
                        "twenty_mm": mat_vals["twenty_mm"],
                        "facs": mat_vals["facs"],
                        "water_batch": mat_vals["water_batch"],
                        "flyash": mat_vals["flyash"],
                        "adm_plast": mat_vals["adm_plast"],
                        "waterr": mat_vals["waterr"],
                    }
                )

    def action_open_customer_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": "Customer Invoice",
            "res_model": "account.move",
            "res_id": self.invoice_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_vendor_bills(self):
        self.ensure_one()
        domain = [("move_type", "=", "in_invoice")]
        if self.workorder_id and self.workorder_id.name:
            domain.append(("invoice_origin", "ilike", self.workorder_id.name))
        else:
            domain.append(("id", "=", 0))
        return {
            "type": "ir.actions.act_window",
            "name": "Vendor Bills",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": domain,
            "context": {"search_default_posted": 1},
        }

    def action_open_docket_batches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Docket Batches",
            "res_model": "gear.rmc.docket.batch",
            "view_mode": "list,form",
            "domain": [("docket_id", "=", self.id)],
        }

    def action_generate_batches(self):
        self._generate_batches_safe()
        return True

    def action_operator_set_status(self, status, notes=None):
        allowed = {"pending", "in_progress", "completed"}
        if status not in allowed:
            raise ValidationError(_("Invalid operator status."))
        vals = {"operator_portal_status": status}
        if status == "completed":
            vals["operator_completion_time"] = fields.Datetime.now()
        if notes is not None:
            vals["operator_notes"] = notes
        self.write(vals)

    @api.model
    def gear_create_from_workorder(self, workorder, payload):
        if not workorder:
            return self.browse()
        date_value = payload.get("date") or fields.Date.to_string(fields.Date.context_today(self))
        docket_no = payload.get("docket_no") or f"{workorder.id}-{date_value}"

        docket = self.search([("workorder_id", "=", workorder.id), ("docket_no", "=", docket_no)], limit=1)
        monthly_order = workorder.production_id.x_monthly_order_id if workorder.production_id else False
        if monthly_order and monthly_order.date_start and monthly_order.date_end:
            try:
                parsed_date = fields.Date.from_string(date_value)
            except Exception:
                parsed_date = monthly_order.date_start
            if parsed_date < monthly_order.date_start:
                parsed_date = monthly_order.date_start
            elif parsed_date > monthly_order.date_end:
                parsed_date = monthly_order.date_end
            date_value = fields.Date.to_string(parsed_date)

        vals = {
            "so_id": payload.get("so_id") or workorder.production_id.x_sale_order_id.id,
            "production_id": payload.get("production_id") or workorder.production_id.id,
            "workorder_id": workorder.id,
            "workcenter_id": workorder.workcenter_id.id,
            "docket_no": docket_no,
            "date": date_value,
            "payload_timestamp": payload.get("payload_timestamp"),
            "qty_m3": payload.get("qty_m3", 0.0),
            "slump": payload.get("slump"),
            "runtime_minutes": payload.get("runtime_min", payload.get("runtime_minutes", 0.0)),
            "idle_minutes": payload.get("idle_min", payload.get("idle_minutes", 0.0)),
            "alarm_codes": payload.get("alarms", []),
            "notes": payload.get("notes"),
            "source": payload.get("source") or "ids",
            "state": "in_production",
        }
        if docket:
            docket.write(vals)
        else:
            docket = self.create(vals)
        return docket


class GearRmcDocketLine(models.Model):
    _name = "gear.rmc.docket.line"
    _description = "RMC Docket Line"

    docket_id = fields.Many2one("gear.rmc.docket", required=True, ondelete="cascade")
    material_name = fields.Char(string="Material Name")
    material_code = fields.Char(string="Material Code")
    design_qty = fields.Float(string="Design Qty (kg)", required=True)
    correction = fields.Float(string="%Mois/%Abs/Corr (kg)")
    corrected = fields.Float(string="Corrected (kg)")
    actual_qty = fields.Float(string="Actual Qty (kg)")

    required = fields.Float(string="Required (kg)", compute="_compute_variance", store=True)
    batched = fields.Float(string="Batched (kg)", compute="_compute_variance", store=True)
    variance = fields.Float(string="Variance (kg)", compute="_compute_variance", store=True)
    variance_percentage = fields.Float(string="Variance %", compute="_compute_variance", store=True)

    @api.depends(
        "design_qty",
        "actual_qty",
        "material_name",
        "material_code",
        "docket_id.docket_batch_ids.ten_mm",
        "docket_id.docket_batch_ids.twenty_mm",
        "docket_id.docket_batch_ids.facs",
        "docket_id.docket_batch_ids.water_batch",
        "docket_id.docket_batch_ids.flyash",
        "docket_id.docket_batch_ids.adm_plast",
        "docket_id.docket_batch_ids.waterr",
    )
    def _compute_variance(self):
        for line in self:
            line.required = line.design_qty
            batches = line.docket_id.docket_batch_ids if line.docket_id else self.env["gear.rmc.docket.batch"]
            totals = {
                "ten_mm": sum(b.ten_mm for b in batches),
                "twenty_mm": sum(b.twenty_mm for b in batches),
                "facs": sum(b.facs for b in batches),
                "water_batch": sum(b.water_batch for b in batches),
                "flyash": sum(b.flyash for b in batches),
                "adm_plast": sum(b.adm_plast for b in batches),
                "waterr": sum(b.waterr for b in batches),
            }
            key = (line.material_code or line.material_name or "").lower()
            norm = re.sub(r"[^a-z0-9]", "", key)
            if "10" in norm and "20" not in norm:
                batched = totals["ten_mm"]
            elif "20" in norm:
                batched = totals["twenty_mm"]
            elif "fly" in norm:
                batched = totals["flyash"]
            elif "water" in norm or "h2o" in norm:
                batched = totals["waterr"]
            elif "adm" in norm:
                batched = totals["adm_plast"]
            else:
                batched = totals["facs"]
            line.batched = batched
            line.variance = line.actual_qty - line.design_qty
            line.variance_percentage = (line.variance / line.design_qty * 100.0) if line.design_qty else 0.0


class GearRmcDocketBatch(models.Model):
    _name = "gear.rmc.docket.batch"
    _description = "RMC Docket Batch"

    docket_id = fields.Many2one("gear.rmc.docket", required=True, ondelete="cascade")
    batch_code = fields.Char(string="Batch Code")
    batch_sequence = fields.Integer(string="Sequence")
    quantity_ordered = fields.Float(string="Batch Quantity (m³)")

    ten_mm = fields.Float(string="CA10MM")
    twenty_mm = fields.Float(string="CA20MM")
    facs = fields.Float(string="FACS")
    water_batch = fields.Float(string="CEMOPC")
    flyash = fields.Float(string="FLYASH")
    adm_plast = fields.Float(string="ADMPLAST")
    waterr = fields.Float(string="WATERR")
