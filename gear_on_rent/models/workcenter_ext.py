from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class MrpWorkcenter(models.Model):
    """Extend work centers with equipment linkage and IDS metadata."""

    _inherit = "mrp.workcenter"

    x_equipment_id = fields.Many2one(
        comodel_name="maintenance.equipment",
        string="Linked Equipment",
        help="Equipment operated at this work center; used for downtime coordination.",
        check_company=True,
    )
    x_ids_external_id = fields.Char(
        string="IDS External Identifier",
        help="Identifier provided by IDS to push telemetry for this work center.",
        copy=False,
    )

    _sql_constraints = [
        (
            "unique_ids_external_id",
            "unique(x_ids_external_id, company_id)",
            "Each IDS external identifier must be unique per company.",
        )
    ]

    @api.constrains("x_equipment_id")
    def _check_equipment_company(self):
        for workcenter in self.filtered(lambda w: w.x_equipment_id):
            if (
                workcenter.company_id
                and workcenter.x_equipment_id.company_id
                and workcenter.company_id != workcenter.x_equipment_id.company_id
            ):
                raise ValidationError(
                    _("The linked equipment must belong to the same company as the work center.")
                )

    @api.model
    def gear_get_by_external_id(self, external_id):
        """Locate a work center using the IDS external identifier."""
        if not external_id:
            return self.browse()
        domain = [("x_ids_external_id", "=", external_id)]
        return self.search(domain, limit=1)
