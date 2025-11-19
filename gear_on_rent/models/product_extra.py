from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    gear_workcenter_id = fields.Many2one(
        comodel_name="mrp.workcenter",
        string="Default Work Center",
        help="Work center used when this product drives the RMC production workflow.",
        check_company=False,
    )

    @api.onchange("gear_workcenter_id")
    def _onchange_gear_workcenter_id(self):
        for template in self:
            workcenter = template.gear_workcenter_id
            company = workcenter.company_id if workcenter else False
            if company and template.company_id != company:
                template.company_id = company

    @api.model_create_multi
    def create(self, vals_list):
        Workcenter = self.env["mrp.workcenter"]
        for vals in vals_list:
            workcenter_id = vals.get("gear_workcenter_id")
            if workcenter_id:
                workcenter = Workcenter.browse(workcenter_id)
                company = workcenter.company_id
                if company:
                    vals["company_id"] = company.id
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)  # copy to avoid mutating caller dict when we add company_id
        if "gear_workcenter_id" in vals:
            workcenter_id = vals.get("gear_workcenter_id")
            if workcenter_id:
                workcenter = self.env["mrp.workcenter"].browse(workcenter_id)
                company = workcenter.company_id
                if company:
                    vals["company_id"] = company.id
        return super().write(vals)


class ProductProduct(models.Model):
    _inherit = "product.product"

    gear_workcenter_id = fields.Many2one(
        comodel_name="mrp.workcenter",
        string="Default Work Center",
        related="product_tmpl_id.gear_workcenter_id",
        store=True,
        readonly=False,
    )
