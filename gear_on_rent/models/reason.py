from odoo import fields, models


class GearReason(models.Model):
    """Master list of client vs. maintenance reasons."""

    _name = "gear.reason"
    _description = "Gear Reason"
    _order = "name"

    name = fields.Char(required=True)
    reason_type = fields.Selection(
        selection=[("client", "Client"), ("maintenance", "Maintenance")],
        string="Reason Type",
        required=True,
        default="client",
    )
    active = fields.Boolean(default=True)
