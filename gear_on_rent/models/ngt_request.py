from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round


class GearNgTLedger(models.Model):
    """Monthly ledger that stores NGT relief that has been approved."""

    _name = "gear.ngt.ledger"
    _description = "NGT Monthly Ledger"
    _order = "month desc, so_id"

    so_id = fields.Many2one(
        comodel_name="sale.order",
        string="Contract",
        required=True,
        index=True,
        ondelete="cascade",
    )
    request_id = fields.Many2one(
        comodel_name="gear.ngt.request",
        string="NGT Request",
        required=True,
        ondelete="cascade",
    )
    month = fields.Date(string="Month", required=True, index=True)
    hours_relief = fields.Float(string="Approved Hours", digits=(16, 2))
    note = fields.Char(string="Notes")


class GearNgTRequest(models.Model):
    """Handles Non-Generation Time (NGT) requests and MGQ relief."""

    _name = "gear.ngt.request"
    _description = "Gear On Rent NGT Request"
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
    month = fields.Date(
        string="Relief Month",
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
            if request.state not in ("rejected", "submitted"):
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
                raise UserError(_("Cannot approve an NGT request without duration."))
            request.so_id.gear_generate_monthly_orders(
                date_start=fields.Date.to_date(request.date_start),
                date_end=fields.Date.to_date(request.date_end),
            )
            request.so_id.gear_register_ngt(request)
            month = request.month
            request._create_ledger_entry(month)
            request.write(
                {
                    "state": "approved",
                    "approved_by": self.env.user.id,
                    "approved_on": fields.Datetime.now(),
                }
            )
        return True

    def _create_ledger_entry(self, month):
        self.ensure_one()
        ledger_env = self.env["gear.ngt.ledger"]
        existing = ledger_env.search(
            [
                ("request_id", "=", self.id),
            ],
            limit=1,
        )
        if existing:
            existing.write(
                {
                    "so_id": self.so_id.id,
                    "month": month,
                    "hours_relief": self.hours_total,
                }
            )
        else:
            ledger_env.create(
                {
                    "so_id": self.so_id.id,
                    "request_id": self.id,
                    "month": month,
                    "hours_relief": self.hours_total,
                }
            )

    def _ensure_can_approve(self):
        if not self.env.user.has_group("gear_on_rent.group_gear_on_rent_manager"):
            raise UserError(_("Only Gear On Rent managers can approve requests."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.name == _("New"):
                record.name = self.env["ir.sequence"].next_by_code("gear.ngt.request") or _("New")
        return records
