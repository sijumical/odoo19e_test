from odoo import fields, models


class ProductTemplate(models.Model):
    """Flag products that should launch the RMC production flow."""

    _inherit = "product.template"

    gear_is_production = fields.Boolean(
        string="RMC Production Item",
        help="Enable to run the Gear On Rent production workflow whenever this product appears on a sale order.",
        tracking=True,
    )


class ProductProduct(models.Model):
    _inherit = "product.product"

    gear_is_production = fields.Boolean(
        related="product_tmpl_id.gear_is_production",
        store=True,
        readonly=False,
    )
