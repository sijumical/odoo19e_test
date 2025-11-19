from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round


class GearLotoLedger(models.Model):
    """Ledger storing LOTO approvals and allowance usage per month."""

    _name = "gear.loto.ledger"
    _description = "LOTO Monthly Ledger"
    _order = "month desc, so_id"

    so_id = fields.Many2one(
        comodel_name="sale.order",
        string="Contract",
        required=True,
        index=True,
        ondelete="cascade",
    )
    request_id = fields.Many2one(
        comodel_name="gear.loto.request",
        string="LOTO Request",
        required=True,
        ondelete="cascade",
    )
    month = fields.Date(string="Month", required=True, index=True)
    hours_total = fields.Float(string="Approved Hours", digits=(16, 2))
    hours_waveoff = fields.Float(string="Wave-Off Hours", digits=(16, 2))
    hours_chargeable = fields.Float(string="Chargeable Hours", digits=(16, 2))
    note = fields.Char(string="Notes")


class GearLotoRequest(models.Model):
    """Handles Lock-Out Tag-Out (LOTO) requests with wave-off allowance logic."""

    _name = "gear.loto.request"
    _description = "Gear On Rent LOTO Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Reference",
        default=lambda self: _("New"),
        copy=False,
        tracking=True,
    )
    so_id = fields.Many2one(
        comodel_name="sale.order",
        string="Contract / SO",
        required=True,
        tracking=True,
        domain=[("state", "in", ["sale", "done"])],
    )
    date_start = fields.Datetime(string="Start", required=True, tracking=True)
    date_end = fields.Datetime(string="End", required=True, tracking=True)
    reason = fields.Text(string="Reason", tracking=True)
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        string="Status",
        default="draft",
        tracking=True,
    )
    approved_by = fields.Many2one(
        comodel_name="res.users",
        string="Approved By",
        tracking=True,
        readonly=True,
    )
    approved_on = fields.Datetime(string="Approved On", tracking=True, readonly=True)
    hours_total = fields.Float(
        string="Total Hours",
        compute="_compute_hours_total",
        store=True,
        digits=(16, 2),
    )
    hours_waveoff_applied = fields.Float(
        string="Wave-Off Applied",
        digits=(16, 2),
        tracking=True,
    )
    hours_chargeable = fields.Float(
        string="Chargeable Hours",
        digits=(16, 2),
        tracking=True,
    )
    month = fields.Date(
        string="Allowance Month",
        compute="_compute_month",
        store=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        related="so_id.company_id",
        store=True,
        readonly=True,
    )

    @api.depends("date_start", "date_end")
    def _compute_hours_total(self):
        for request in self:
            hours = 0.0
            if request.date_start and request.date_end:
                if request.date_end < request.date_start:
                    raise UserError(_("End date must be after start date."))
                delta = request.date_end - request.date_start
                hours = float_round(delta.total_seconds() / 3600.0, precision_digits=2)
            request.hours_total = hours

    @api.depends("date_start")
    def _compute_month(self):
        for request in self:
            date_ref = request.date_start or fields.Datetime.now()
            request.month = fields.Date.to_date(date_ref).replace(day=1)

    def action_submit(self):
        for request in self:
            if request.state != "draft":
                raise UserError(_("Only draft requests can be submitted."))
            request.state = "submitted"
        return True

    def action_reset_to_draft(self):
        for request in self:
            if request.state not in ("submitted", "rejected"):
                raise UserError(_("Only submitted or rejected requests can be reset."))
            request.state = "draft"
        return True

    def action_reject(self):
        self._ensure_can_approve()
        for request in self:
            if request.state != "submitted":
                raise UserError(_("Only submitted requests can be rejected."))
            request.state = "rejected"
        return True

    def action_approve(self):
        self._ensure_can_approve()
        for request in self:
            if request.state != "submitted":
                raise UserError(_("Only submitted requests can be approved."))
            if not request.hours_total:
                raise UserError(_("Cannot approve a LOTO request without duration."))
            request.so_id.gear_generate_monthly_orders(
                date_start=fields.Date.to_date(request.date_start),
                date_end=fields.Date.to_date(request.date_end),
            )
            waveoff_applied, chargeable = request.so_id.gear_register_loto(request)
            waveoff_applied = round(waveoff_applied, 2)
            chargeable = round(chargeable, 2)
            request._create_ledger_entry(
                request.month,
                request.hours_total,
                waveoff_applied,
                chargeable,
            )
            request.write(
                {
                    "state": "approved",
                    "approved_by": self.env.user.id,
                    "approved_on": fields.Datetime.now(),
                    "hours_waveoff_applied": waveoff_applied,
                    "hours_chargeable": chargeable,
                }
            )
        return True

    def _create_ledger_entry(self, month, total, waveoff, chargeable):
        self.ensure_one()
        ledger_env = self.env["gear.loto.ledger"]
        existing = ledger_env.search(
            [
                ("request_id", "=", self.id),
            ],
            limit=1,
        )
        vals = {
            "so_id": self.so_id.id,
            "month": month,
            "hours_total": total,
            "hours_waveoff": waveoff,
            "hours_chargeable": chargeable,
        }
        if existing:
            existing.write(vals)
        else:
            vals["request_id"] = self.id
            ledger_env.create(vals)

    def _ensure_can_approve(self):
        if not self.env.user.has_group("gear_on_rent.group_gear_on_rent_manager"):
            raise UserError(_("Only Gear On Rent managers can approve requests."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.name == _("New"):
                record.name = self.env["ir.sequence"].next_by_code("gear.loto.request") or _("New")
        return records
