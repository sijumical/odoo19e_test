from odoo import models


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    def _get_invoice_extra_attachments(self, move):
        attachments = super()._get_invoice_extra_attachments(move)
        extra = self.env["ir.attachment"]
        if move.x_billing_category == "rmc":
            month_end = move.gear_month_end_attachment_id
            if not month_end:
                month_end = self.env["ir.attachment"].search(
                    [
                        ("res_model", "=", move._name),
                        ("res_id", "=", move.id),
                        ("name", "ilike", "Month-End Report.pdf"),
                    ],
                    limit=1,
                )
            log_summary = move.gear_log_summary_attachment_id
            if not log_summary:
                log_summary = self.env["ir.attachment"].search(
                    [
                        ("res_model", "=", move._name),
                        ("res_id", "=", move.id),
                        ("name", "ilike", "Log Summary.pdf"),
                    ],
                    limit=1,
                )
            extra |= month_end | log_summary
        return attachments | extra
