from odoo import api, fields, models
from odoo.osv import expression
from odoo.exceptions import ValidationError


METRIC_TYPE_SELECTION = [
    ("int", "Integer"),
    ("float", "Float"),
    ("text", "Text"),
    ("selection", "Selection"),
]


class DailyManagerReport(models.Model):
    _name = "daily.manager.report"
    _description = "Daily Manager Report"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    company_id = fields.Many2one("res.company", string="Company", required=True, default=lambda self: self.env.company, index=True)
    branch_id = fields.Many2one(
        "res.company",
        string="Branch",
        domain="[('id', 'in', allowed_company_ids)]",
        index=True,
        default=lambda self: self.env.company,
    )
    def _default_department(self):
        employee = self.env.user.employee_id
        return employee.department_id if employee and employee.department_id else False

    department_id = fields.Many2one(
        "hr.department",
        string="Department",
        default=_default_department,
        domain="[('company_id', '=', company_id)]",
    )
    manager_id = fields.Many2one("res.users", string="Manager", required=True, default=lambda self: self.env.user, tracking=True)
    activities = fields.Html(string="Activities")
    complaint_ids = fields.One2many("daily.manager.report.complaint", "report_id", string="Complaints")
    staff_log_ids = fields.One2many("daily.manager.report.staff.log", "report_id", string="Staff Logs")
    contractor_performance_ids = fields.One2many("daily.manager.report.contractor.performance", "report_id", string="Contractor Performance")
    metric_line_ids = fields.One2many("daily.manager.report.metric.line", "report_id", string="Metric Lines")
    dynamic_section_ids = fields.One2many(
        "daily.manager.report.section",
        "report_id",
        string="Dynamic Sections",
        help="Part of SP Nexgen Automind's Universal Reporting System.",
    )
    state = fields.Selection([("draft", "Draft"), ("submitted", "Submitted")], default="draft", required=True, tracking=True)
    submitted_on = fields.Datetime(readonly=True)
    submitted_by = fields.Many2one("res.users", string="Submitted By", readonly=True)
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.onchange("company_id")
    def _onchange_company_id(self):
        if self.company_id and (not self.branch_id or self.branch_id != self.company_id):
            self.branch_id = self.company_id
        if self.department_id and self.department_id.company_id and self.department_id.company_id != self.company_id:
            self.department_id = False

    @api.onchange("manager_id")
    def _onchange_manager_id(self):
        employee = self.manager_id.employee_id
        if employee and employee.department_id and employee.department_id.company_id == self.company_id:
            self.department_id = employee.department_id

    @api.constrains("branch_id", "company_id")
    def _check_branch_company(self):
        for record in self:
            if record.branch_id and record.branch_id not in record.manager_id.company_ids:
                raise ValidationError("Branch must belong to one of the manager's allowed companies.")
            if record.branch_id and record.company_id and record.branch_id != record.company_id:
                raise ValidationError("Branch must match the selected company for the report.")
            if record.department_id and record.department_id.company_id and record.department_id.company_id != record.company_id:
                raise ValidationError("Department must belong to the report company.")

    @api.model
    def create(self, vals_list):
        vals_list = [vals_list] if isinstance(vals_list, dict) else vals_list
        for vals in vals_list:
            if vals.get("company_id") and not vals.get("branch_id"):
                vals["branch_id"] = vals.get("company_id")
        records = super().create(vals_list)
        for report in records:
            report._create_metric_lines_from_templates()
            report._create_dynamic_sections_from_templates()
        return records

    def _create_metric_lines_from_templates(self):
        MetricTemplate = self.env["daily.manager.metric.template"]
        for report in self:
            domain = expression.AND(
                [
                    [("active", "=", True)],
                    ["|", ("company_id", "=", False), ("company_id", "=", report.company_id.id)],
                    ["|", ("branch_id", "=", False), ("branch_id", "=", report.branch_id.id)],
                    ["|", ("department_id", "=", False), ("department_id", "=", report.department_id.id)],
                ]
            )
            templates = MetricTemplate.search(domain, order="sequence, id")
            values = []
            existing_templates = set(report.metric_line_ids.mapped("template_id").ids)
            for template in templates:
                if template.id in existing_templates:
                    continue
                values.append(
                    {
                        "report_id": report.id,
                        "template_id": template.id,
                        "name": template.name,
                        "metric_type": template.metric_type,
                        "sequence": template.sequence,
                        "int_value": template.default_int_value if template.metric_type == "int" else False,
                        "float_value": template.default_float_value if template.metric_type == "float" else False,
                        "text_value": template.default_text_value if template.metric_type == "text" else False,
                        "selection_value": template.default_selection_value if template.metric_type == "selection" else False,
                    }
                )
            if values:
                self.env["daily.manager.report.metric.line"].create(values)

    def _create_dynamic_sections_from_templates(self):
        SectionTemplate = self.env["daily.manager.section.template"]
        for report in self:
            domain = expression.AND(
                [
                    [("active", "=", True)],
                    ["|", ("company_id", "=", False), ("company_id", "=", report.company_id.id)],
                    ["|", ("branch_id", "=", False), ("branch_id", "=", report.branch_id.id)],
                    ["|", ("department_id", "=", False), ("department_id", "=", report.department_id.id)],
                ]
            )
            templates = SectionTemplate.search(domain, order="sequence, id")
            values = []
            existing_templates = set(report.dynamic_section_ids.mapped("template_id").ids)
            for template in templates:
                if template.id in existing_templates:
                    continue
                values.append(
                    {
                        "report_id": report.id,
                        "template_id": template.id,
                        "title": template.name,
                        "sequence": template.sequence,
                    }
                )
            if values:
                self.env["daily.manager.report.section"].create(values)

    def action_submit(self):
        for record in self:
            if record.state != "draft":
                continue
            record.write(
                {
                    "state": "submitted",
                    "submitted_on": fields.Datetime.now(),
                    "submitted_by": self.env.user.id,
                }
            )
        return True

    def action_reopen(self):
        for record in self:
            if record.state != "submitted":
                continue
            record.write({"state": "draft"})
        return True

    def _get_allowed_notification_partners(self):
        self.ensure_one()
        allowed_companies = {self.company_id.id}
        if self.branch_id:
            allowed_companies.add(self.branch_id.id)
        partners = self.message_partner_ids.filtered(
            lambda partner: not partner.company_id or partner.company_id.id in allowed_companies
        )
        manager_partner = self.manager_id.partner_id
        if manager_partner and manager_partner not in partners:
            partners |= manager_partner
        return partners

    def _get_summary_message_body(self):
        self.ensure_one()
        parts = [
            f"Daily Report {self.date or ''}",
            f"Company: {self.company_id.name or ''}",
        ]
        if self.branch_id:
            parts.append(f"Branch: {self.branch_id.name}")
        if self.department_id:
            parts.append(f"Department: {self.department_id.name}")
        parts.append(f"Manager: {self.manager_id.name or ''}")
        if self.activities:
            parts.append(f"Activities: {self.activities}")
        if self.complaint_ids:
            parts.append(
                "Complaints: "
                + "; ".join(f"{comp.description or ''} ({comp.severity or ''})" for comp in self.complaint_ids)
            )
        if self.contractor_performance_ids:
            parts.append(
                "Contractors: "
                + "; ".join(
                    f"{perf.contractor_id.name or ''}: {perf.rating}" for perf in self.contractor_performance_ids
                )
            )
        if self.metric_line_ids:
            parts.append(
                "Metrics: "
                + "; ".join(
                    f"{line.name}: {line.int_value or line.float_value or line.text_value or line.selection_value or ''}"
                    for line in self.metric_line_ids
                )
        )
        if self.dynamic_section_ids:
            parts.append(
                "Dynamic Sections: "
                + "; ".join(
                    f"{section.title}: {section.subject or ''} {section.description or ''}"
                    for section in self.dynamic_section_ids
                )
            )
        if self.notes:
            parts.append(f"Notes: {self.notes}")
        return "\n".join(parts)

    def action_send_email(self):
        self.ensure_one()
        if self.state != "submitted":
            raise ValidationError("Reports must be submitted before sending notifications.")
        template = self.env.ref("universal_daily_reporting.mail_template_daily_report_email", raise_if_not_found=False)
        partners = self._get_allowed_notification_partners()
        compose_form = self.env.ref("mail.email_compose_message_wizard_form", raise_if_not_found=False)
        ctx = {
            "default_model": self._name,
            "default_res_id": self.id,
            "default_use_template": bool(template),
            "default_template_id": template.id if template else False,
            "default_composition_mode": "comment",
            "mark_so_as_sent": True,
            "force_email": True,
        }
        if partners:
            ctx["default_partner_ids"] = partners.ids
        if compose_form:
            return {
                "type": "ir.actions.act_window",
                "res_model": "mail.compose.message",
                "view_mode": "form",
                "views": [(compose_form.id, "form")],
                "target": "new",
                "context": ctx,
            }
        # Fallback to posting a summary when compose form is unavailable
        self.message_post(
            body=self._get_summary_message_body(),
            partner_ids=partners.ids,
            subtype_xmlid="mail.mt_comment",
        )
        return True

    def action_send_whatsapp(self):
        self.ensure_one()
        if self.state != "submitted":
            raise ValidationError("Reports must be submitted before sending notifications.")
        template = self.env.ref("universal_daily_reporting.mail_template_daily_report_whatsapp", raise_if_not_found=False)
        partners = self._get_allowed_notification_partners()
        compose_form = self.env.ref("mail.email_compose_message_wizard_form", raise_if_not_found=False)
        ctx = {
            "default_model": self._name,
            "default_res_id": self.id,
            "default_use_template": bool(template),
            "default_template_id": template.id if template else False,
            "default_composition_mode": "comment",
            "force_email": True,
        }
        if partners:
            ctx["default_partner_ids"] = partners.ids
        if compose_form:
            return {
                "type": "ir.actions.act_window",
                "res_model": "mail.compose.message",
                "view_mode": "form",
                "views": [(compose_form.id, "form")],
                "target": "new",
                "context": ctx,
            }
        self.message_post(
            body=self._get_summary_message_body(),
            partner_ids=partners.ids,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        return True


class DailyManagerReportComplaint(models.Model):
    _name = "daily.manager.report.complaint"
    _description = "Daily Manager Report Complaint"

    report_id = fields.Many2one("daily.manager.report", string="Report", required=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", string="Company", related="report_id.company_id", store=True, readonly=True)
    branch_id = fields.Many2one("res.company", string="Branch", related="report_id.branch_id", store=True, readonly=True)
    department_id = fields.Many2one("hr.department", string="Department", related="report_id.department_id", store=True, readonly=True)
    description = fields.Text(required=True)
    customer_id = fields.Many2one("res.partner", string="Customer")
    severity = fields.Selection([("low", "Low"), ("medium", "Medium"), ("high", "High")], default="medium")
    action_taken = fields.Text()
    resolved = fields.Boolean(default=False)
    responsible_id = fields.Many2one("res.users", string="Responsible")
    date = fields.Date(default=fields.Date.context_today)
    reference = fields.Char(string="Reference")


class DailyManagerReportStaffLog(models.Model):
    _name = "daily.manager.report.staff.log"
    _description = "Daily Manager Report Staff Log"

    report_id = fields.Many2one("daily.manager.report", string="Report", required=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", string="Company", related="report_id.company_id", store=True, readonly=True)
    branch_id = fields.Many2one("res.company", string="Branch", related="report_id.branch_id", store=True, readonly=True)
    department_id = fields.Many2one("hr.department", string="Department", related="report_id.department_id", store=True, readonly=True)
    staff_id = fields.Many2one("hr.employee", string="Staff Member")
    role = fields.Char(string="Role")
    shift = fields.Char(string="Shift")
    issue = fields.Text(string="Issue/Observation")
    action = fields.Text(string="Action Taken")
    attendance_flag = fields.Selection([("present", "Present"), ("absent", "Absent"), ("late", "Late")], default="present")
    note = fields.Text(string="Note")


class DailyManagerReportContractorPerformance(models.Model):
    _name = "daily.manager.report.contractor.performance"
    _description = "Daily Manager Report Contractor Performance"

    report_id = fields.Many2one("daily.manager.report", string="Report", required=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", string="Company", related="report_id.company_id", store=True, readonly=True)
    branch_id = fields.Many2one("res.company", string="Branch", related="report_id.branch_id", store=True, readonly=True)
    department_id = fields.Many2one("hr.department", string="Department", related="report_id.department_id", store=True, readonly=True)
    contractor_id = fields.Many2one("res.partner", string="Contractor", required=True)
    rating = fields.Float(default=0.0)
    comment = fields.Text()
    follow_up_action = fields.Text()
    attachment_ids = fields.Many2many("ir.attachment", "contractor_performance_attachment_rel", "performance_id", "attachment_id", string="Attachments")
    reference_period = fields.Char(string="Reference Period")
    active = fields.Boolean(default=True)


class DailyManagerSectionTemplate(models.Model):
    _name = "daily.manager.section.template"
    _description = "Daily Manager Report Section Template"
    _order = "sequence, name, id"

    name = fields.Char(required=True)
    code = fields.Char()
    company_id = fields.Many2one("res.company", string="Company")
    branch_id = fields.Many2one("res.company", string="Branch", domain="[('id', 'in', allowed_company_ids)]")
    department_id = fields.Many2one(
        "hr.department",
        string="Department",
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    description = fields.Text()

    @api.constrains("branch_id", "company_id")
    def _check_branch_company(self):
        for template in self:
            if template.branch_id and template.company_id and template.branch_id != template.company_id:
                raise ValidationError("Branch must match the template company when both are set.")
            if template.department_id and template.department_id.company_id and template.company_id:
                if template.department_id.company_id != template.company_id:
                    raise ValidationError("Department must belong to the template company.")


class DailyManagerReportSection(models.Model):
    _name = "daily.manager.report.section"
    _description = "Daily Manager Report Dynamic Section"
    _order = "sequence, id"

    report_id = fields.Many2one("daily.manager.report", string="Report", required=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", string="Company", related="report_id.company_id", store=True, readonly=True)
    branch_id = fields.Many2one("res.company", string="Branch", related="report_id.branch_id", store=True, readonly=True)
    department_id = fields.Many2one("hr.department", string="Department", related="report_id.department_id", store=True, readonly=True)
    template_id = fields.Many2one("daily.manager.section.template", string="Template", ondelete="restrict")
    title = fields.Char()
    subject = fields.Char()
    description = fields.Html()
    employee_id = fields.Many2one("hr.employee", string="Employee")
    partner_id = fields.Many2one("res.partner", string="Partner")
    sequence = fields.Integer(default=10)

    @api.model
    def create(self, vals):
        template = False
        if vals.get("template_id"):
            template = self.env["daily.manager.section.template"].browse(vals["template_id"])
        if template and not vals.get("title"):
            vals["title"] = template.name
        return super().create(vals)

    @api.constrains("template_id", "report_id")
    def _check_template_scope(self):
        for line in self:
            if not line.template_id or not line.report_id:
                continue
            template = line.template_id
            report = line.report_id
            if template.company_id and template.company_id != report.company_id:
                raise ValidationError("Template company must match the report company.")
            if template.branch_id and template.branch_id != report.branch_id:
                raise ValidationError("Template branch must match the report branch.")
            if template.department_id and template.department_id != report.department_id:
                raise ValidationError("Template department must match the report department.")


class DailyManagerMetricTemplate(models.Model):
    _name = "daily.manager.metric.template"
    _description = "Daily Manager Metric Template"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char(string="Code")
    metric_type = fields.Selection(METRIC_TYPE_SELECTION, required=True)
    selection_options = fields.Text(help="Selection options, one value per line or JSON mapping for labels.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", string="Company")
    branch_id = fields.Many2one("res.company", string="Branch", domain="[('id', 'in', allowed_company_ids)]")
    department_id = fields.Many2one(
        "hr.department",
        string="Department",
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    description = fields.Text()
    default_int_value = fields.Integer(string="Default Integer")
    default_float_value = fields.Float(string="Default Float")
    default_text_value = fields.Text(string="Default Text")
    default_selection_value = fields.Char(string="Default Selection")
    required = fields.Boolean(string="Required", default=False)
    sequence = fields.Integer(default=10)

    @api.constrains("branch_id", "company_id")
    def _check_branch_alignment(self):
        for template in self:
            if template.branch_id and template.company_id and template.branch_id != template.company_id:
                raise ValidationError("Branch must belong to the selected company for the metric template.")
            if template.department_id and template.department_id.company_id and template.company_id:
                if template.department_id.company_id != template.company_id:
                    raise ValidationError("Department must belong to the metric template company.")


class DailyManagerReportMetricLine(models.Model):
    _name = "daily.manager.report.metric.line"
    _description = "Daily Manager Report Metric Line"
    _order = "sequence, id"

    report_id = fields.Many2one("daily.manager.report", string="Report", required=True, ondelete="cascade")
    template_id = fields.Many2one("daily.manager.metric.template", string="Metric Template", ondelete="set null")
    name = fields.Char(required=True)
    metric_type = fields.Selection(METRIC_TYPE_SELECTION, required=True)
    int_value = fields.Integer(string="Integer Value")
    float_value = fields.Float(string="Float Value")
    text_value = fields.Text(string="Text Value")
    selection_value = fields.Char(string="Selection Value")
    branch_id = fields.Many2one("res.company", string="Branch", related="report_id.branch_id", store=True, readonly=True)
    company_id = fields.Many2one("res.company", string="Company", related="report_id.company_id", store=True, readonly=True)
    department_id = fields.Many2one("hr.department", string="Department", related="report_id.department_id", store=True, readonly=True)
    sequence = fields.Integer(default=10)

    @api.constrains("metric_type", "template_id")
    def _check_metric_type_alignment(self):
        for line in self:
            if line.template_id and line.metric_type != line.template_id.metric_type:
                raise ValidationError("Metric line type must match the template metric type.")
