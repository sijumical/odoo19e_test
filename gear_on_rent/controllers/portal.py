try:  # pragma: no cover - allows running tests without optional deps
    from dateutil.relativedelta import relativedelta
except ModuleNotFoundError:  # pragma: no cover
    from odoo_shims.relativedelta import relativedelta

from odoo import fields, http
from odoo.http import request


class GearOnRentPortal(http.Controller):
    """Portal routes for Gear On Rent clients."""

    @http.route("/my/gear-on-rent", type="http", auth="user", website=True)
    def my_gear_on_rent(self, **kwargs):
        partner = request.env.user.partner_id.commercial_partner_id
        SaleOrder = request.env["sale.order"].sudo()
        orders = SaleOrder.search(
            [
                ("partner_id", "child_of", partner.id),
                ("x_billing_category", "in", ["rental", "rmc"]),
            ]
        )
        values = {
            "page_name": "gear_on_rent_dashboard",
            "orders": orders,
        }
        return request.render("gear_on_rent.portal_gear_on_rent_dashboard", values)

    @http.route(
        "/gear_on_rent/quote_request",
        type="http",
        auth="user",
        website=True,
    )
    def gear_on_rent_quote_request(
        self,
        product_id=None,
        amount=None,
        equipment=None,
        details=None,
        **kwargs,
    ):
        if not product_id:
            return request.redirect("/gear-on-rent")

        try:
            product = request.env["product.product"].sudo().browse(int(product_id))
        except (TypeError, ValueError):
            product = request.env["product.product"].sudo()

        if not product or not product.exists():
            return request.redirect("/gear-on-rent")

        partner = request.env.user.partner_id.commercial_partner_id
        if not partner:
            return request.redirect("/gear-on-rent")

        amount_float = 0.0
        try:
            amount_float = float(amount)
        except (TypeError, ValueError):
            amount_float = 0.0
        if amount_float <= 0.0:
            amount_float = product.lst_price

        pricelist = partner.property_product_pricelist
        addresses = partner.address_get(["delivery", "invoice"])

        rental_type = kwargs.get("rental_type")
        duration_type = kwargs.get("duration_type")
        duration_value = kwargs.get("duration")
        project_duration = kwargs.get("project_duration")
        production_volume = kwargs.get("production_volume")

        start_date = fields.Datetime.now()
        delta = relativedelta(days=1)

        try:
            duration_float = float(duration_value or 0)
        except (TypeError, ValueError):
            duration_float = 0.0

        try:
            project_duration_float = float(project_duration or 0)
        except (TypeError, ValueError):
            project_duration_float = 0.0

        try:
            production_volume_float = float(production_volume or 0)
        except (TypeError, ValueError):
            production_volume_float = 0.0

        if rental_type == "hourly":
            if (duration_type or "hourly") == "daily" and duration_float > 0:
                delta = relativedelta(days=duration_float)
            elif duration_float > 0:
                delta = relativedelta(hours=duration_float)
        elif rental_type == "production" and project_duration_float > 0:
            delta = relativedelta(days=project_duration_float)

        return_date = start_date + delta

        notes = []
        if details:
            notes.append(details)
        if kwargs.get("include_operator"):
            notes.append("Includes operator")
        if kwargs.get("include_maintenance"):
            notes.append("Includes premium maintenance package")
        if rental_type == "hourly" and duration_float:
            unit = 'days' if (duration_type or 'hourly') == 'daily' else 'hours'
            duration_label = int(duration_float) if float(duration_float).is_integer() else duration_float
            notes.append(f"Duration: {duration_label} {unit}")
        if rental_type == "production" and project_duration_float:
            proj_label = int(project_duration_float) if float(project_duration_float).is_integer() else project_duration_float
            notes.append(f"Project duration: {proj_label} days")
        if rental_type == "production" and production_volume_float:
            volume_label = int(production_volume_float) if float(production_volume_float).is_integer() else production_volume_float
            notes.append(f"Production volume: {volume_label} m3")

        user = request.env.user
        tz_start = fields.Datetime.context_timestamp(user, start_date) if start_date else False
        tz_return = fields.Datetime.context_timestamp(user, return_date) if return_date else False
        start_display = tz_start.strftime('%Y-%m-%d %H:%M') if tz_start else ''
        return_display = tz_return.strftime('%Y-%m-%d %H:%M') if tz_return else ''
        if start_display and return_display:
            notes.append(f"Rental window: {start_display} -> {return_display}")

        note_text = "\n".join(filter(None, notes))

        amount_display = f"₹{int(round(amount_float)):,}"

        order_vals = {
            "partner_id": partner.id,
            "partner_invoice_id": addresses.get("invoice", partner.id),
            "partner_shipping_id": addresses.get("delivery", partner.id),
            "pricelist_id": pricelist.id if pricelist else False,
            "origin": "Gear On Rent Website Estimate",
            "x_billing_category": "rental",
            "note": note_text,
            "rental_start_date": start_date,
            "rental_return_date": return_date,
            "is_rental_order": True,
        }
        order = request.env["sale.order"].with_context(in_rental_app=True).sudo().create(order_vals)

        request.env["sale.order.line"].with_context(in_rental_app=True).sudo().create({
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": 1.0,
            "price_unit": amount_float,
            "name": equipment or product.display_name,
            "is_rental": True,
            "start_date": start_date,
            "return_date": return_date,
        })

        order.message_post(body="Generated from Gear On Rent estimator on website.")

        values = {
            "order": order,
            "product": product,
            "amount": amount_float,
            "details": details,
            "equipment": equipment or product.display_name,
            "amount_display": amount_display,
            "start_date": start_display,
            "return_date": return_display,
        }
        return request.render("gear_on_rent.quote_request_success", values)
