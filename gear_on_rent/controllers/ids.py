import logging

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class GearIdsController(http.Controller):
    """Receive IDS telemetry and fan it into the matching work orders."""

    IDS_TOKEN_PARAM = "gear_on_rent.ids_token"

    @http.route(
        "/ids/workcenter/update",
        type="json",
        auth="none",
        csrf=False,
        methods=["POST"],
    )
    def ids_workcenter_update(self, **payload):
        env = request.env
        try:
            token_check = self._check_token()
            if token_check:
                return token_check
            workcenter = self._resolve_workcenter(payload)
            if not workcenter:
                return {"status": "error", "message": "Unknown work center."}

            timestamp = self._parse_timestamp(payload.get("timestamp"))
            Workorder = env["mrp.workorder"].sudo()
            workorder = Workorder.gear_find_workorder(workcenter, timestamp)

            if not workorder:
                production = env["mrp.production"].sudo().gear_find_mo_for_datetime(workcenter, timestamp)
                if production:
                    workorder = production.workorder_ids[:1]
                    if not workorder:
                        workorder = Workorder.create(
                            {
                                "name": f"{production.name} / {workcenter.display_name}",
                                "production_id": production.id,
                                "workcenter_id": workcenter.id,
                                "qty_production": production.product_qty,
                                "date_start": production.date_start or timestamp,
                                "date_finished": production.date_finished or timestamp,
                            }
                        )

            if not workorder:
                monthly = env["gear.rmc.monthly.order"].sudo().search(
                    [
                        ("workcenter_id", "=", workcenter.id),
                        ("date_start", "<=", fields.Date.to_date(timestamp)),
                        ("date_end", ">=", fields.Date.to_date(timestamp)),
                    ],
                    limit=1,
                )
                if not monthly:
                    return {
                        "status": "error",
                        "message": "No active work order found for the provided timestamp.",
                    }
                production = monthly.production_ids[:1]
                if production:
                    workorder = production.workorder_ids[:1]

            if not workorder:
                return {
                    "status": "error",
                    "message": "Unable to locate or create a work order for telemetry.",
                }

            docket = workorder.gear_register_ids_payload(payload)
            production = workorder.production_id
            monthly = production.x_monthly_order_id if production else False

            return {
                "status": "ok",
                "workorder_id": workorder.id,
                "production_id": production.id if production else False,
                "monthly_order_id": monthly.id if monthly else False,
                "docket_id": docket.id if docket else False,
            }
        except Exception as exc:
            env.cr.rollback()
            _logger.exception("IDS payload processing failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    def _check_token(self):
        env = request.env
        token_param = env["ir.config_parameter"].sudo().get_param(self.IDS_TOKEN_PARAM)
        if not token_param:
            return None

        header_token = request.httprequest.headers.get("X-IDS-Token")
        if not header_token:
            auth_header = request.httprequest.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                header_token = auth_header[7:]
            elif auth_header.lower().startswith("token "):
                header_token = auth_header[6:]

        if token_param and header_token and header_token == token_param:
            return None
        return http.Response(
            status=401,
            content_type="application/json",
            response=b'{"status": "error", "message": "Unauthorized"}',
        )

    def _resolve_workcenter(self, payload):
        env = request.env
        external_id = payload.get("workcenter_external_id")
        if not external_id:
            raise ValueError("Missing workcenter_external_id in payload.")
        workcenter = env["mrp.workcenter"].sudo().gear_get_by_external_id(external_id)
        if not workcenter:
            _logger.warning("IDS payload received for unknown work center '%s'", external_id)
        return workcenter

    @staticmethod
    def _parse_timestamp(ts_value):
        if not ts_value:
            return fields.Datetime.now()
        return fields.Datetime.to_datetime(ts_value)
