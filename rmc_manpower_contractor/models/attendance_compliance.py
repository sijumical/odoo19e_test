# -*- coding: utf-8 -*-
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.osv import expression

_logger = logging.getLogger(__name__)

class RmcAttendanceCompliance(models.Model):
    _name = 'rmc.attendance.compliance'
    _description = 'RMC Attendance Compliance'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc'
    _ATTENDANCE_SYNC_PARAM = 'rmc.attendance.last_sync_date'
    _SUPERVISOR_KEYWORDS_PARAM = 'rmc.attendance.supervisor_keywords'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    agreement_id = fields.Many2one('rmc.contract.agreement', string='Agreement', required=True, ondelete='restrict', tracking=True)
    contractor_id = fields.Many2one(related='agreement_id.contractor_id', string='Contractor', store=True)
    date = fields.Date(string='Date', required=True, default=fields.Date.context_today, tracking=True)
    headcount_expected = fields.Integer(string='Expected Headcount', compute='_compute_expected', store=True)
    headcount_present = fields.Integer(string='Present Headcount', required=True, default=0)
    documents_ok = fields.Boolean(string='Documents OK', default=True)
    supervisor_ok = fields.Boolean(string='Supervisor Sign-off', default=False)
    compliance_percentage = fields.Float(string='Compliance %', compute='_compute_compliance', store=True, digits=(5, 2))
    state = fields.Selection([('draft', 'Draft'), ('pending_agreement', 'Pending Agreement'), ('validated', 'Validated')], default='draft', required=True, tracking=True)
    first_check_in = fields.Datetime(string='First Check-In')
    last_check_out = fields.Datetime(string='Last Check-Out')
    employee_ids = fields.Many2many(
        'hr.employee',
        'rmc_attendance_employee_rel',
        'attendance_id',
        'employee_id',
        string='Present Employees',
        help='Employees who were present for this agreement on the selected date.'
    )
    attendance_entry_ids = fields.Many2many(
        'hr.attendance',
        string='Attendance Logs',
        compute='_compute_attendance_entries',
        readonly=True,
        help='Detailed attendance entries for the present employees on this day.'
    )
    notes = fields.Text(string='Notes')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    @api.depends('agreement_id.manpower_matrix_ids')
    def _compute_expected(self):
        for record in self:
            record.headcount_expected = sum(record.agreement_id.manpower_matrix_ids.mapped('headcount'))

    @api.depends('headcount_present', 'headcount_expected', 'documents_ok', 'supervisor_ok')
    def _compute_compliance(self):
        for record in self:
            if record.headcount_expected > 0:
                attendance_pct = (record.headcount_present / record.headcount_expected) * 100
                doc_pct = 100 if record.documents_ok else 50
                super_pct = 100 if record.supervisor_ok else 70
                record.compliance_percentage = (attendance_pct * 0.6 + doc_pct * 0.2 + super_pct * 0.2)
            else:
                record.compliance_percentage = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            agreement_id = vals.get('agreement_id')
            if agreement_id:
                self.env['rmc.contract.agreement'].browse(agreement_id)._check_closure_operation_allowed(_('create attendance records'))
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('rmc.attendance.compliance') or _('New')
        records = super(RmcAttendanceCompliance, self).create(vals_list)
        for record in records:
            record._check_agreement_signature()
            record._sync_present_from_employees()
        return records

    def write(self, vals):
        if self:
            agreements = self.mapped('agreement_id')
            if agreements:
                agreements._check_closure_operation_allowed(_('update attendance records'))
        res = super(RmcAttendanceCompliance, self).write(vals)
        if 'state' not in vals:
            self._check_agreement_signature()
        if 'employee_ids' in vals and not self.env.context.get('rmc_attendance_skip_sync'):
            self._sync_present_from_employees()
        if 'agreement_id' in vals and not self.env.context.get('rmc_attendance_skip_sync'):
            self._sync_present_from_employees()
        return res

    def _check_agreement_signature(self):
        for record in self:
            if not record.agreement_id.is_signed():
                record.state = 'pending_agreement'
                record.message_post(body=_('Attendance pending agreement signature.'))
                record.agreement_id.activity_schedule('mail.mail_activity_data_todo', summary=_('Sign agreement'), note=_('Attendance %s waiting.') % record.name)

    @api.constrains('headcount_present')
    def _check_headcount(self):
        for record in self:
            if record.headcount_present < 0:
                raise ValidationError(_('Headcount cannot be negative.'))
            if record.employee_ids and record.headcount_present != len(record.employee_ids):
                raise ValidationError(_('Headcount present must match the number of selected employees.'))

    @api.constrains('employee_ids', 'agreement_id')
    def _check_employee_assignment(self):
        for record in self:
            if record.agreement_id and record.agreement_id.driver_ids and record.employee_ids:
                extra = record.employee_ids - record.agreement_id.driver_ids
                if extra:
                    raise ValidationError(_(
                        'Employees %s are not assigned to agreement %s.'
                    ) % (', '.join(extra.mapped('name')), record.agreement_id.name))

    @api.onchange('employee_ids')
    def _onchange_employee_ids(self):
        for record in self:
            record.headcount_present = len(record.employee_ids)

    def _sync_present_from_employees(self):
        for record in self:
            if record.employee_ids:
                record.with_context(rmc_attendance_skip_sync=True).write({
                    'headcount_present': len(record.employee_ids)
                })

    def action_validate(self):
        for record in self:
            if not record.agreement_id.is_signed():
                raise ValidationError(_('Cannot validate: Agreement not signed.'))
            record.state = 'validated'
            record.message_post(body=_('Attendance validated'))

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})

    # ------------------------------------------------------------------
    # Auto-sync helpers
    # ------------------------------------------------------------------

    def _auto_validate_from_sync(self):
        """Validate record when attendance + compliance signals are ready."""
        for record in self:
            if record.state == 'validated':
                continue
            if not (record.documents_ok and record.supervisor_ok):
                continue
            if record.headcount_present != len(record.employee_ids):
                continue
            try:
                if record.agreement_id and record.agreement_id.is_signed():
                    record.state = 'validated'
                else:
                    record.state = 'pending_agreement'
            except Exception as exc:
                _logger.warning("Auto-validation skipped for %s: %s", record.name, exc)

    @api.model
    def _attendance_sync_window(self):
        """
        Return (start_date, end_date) using a rolling window.
        If rmc.attendance.last_sync_date is set beyond today, respect it so
        operators can backfill future-dated demo data.
        """
        today = fields.Date.context_today(self)
        end_date = today - timedelta(days=1)
        ICP = self.env['ir.config_parameter'].sudo()
        last_sync_str = ICP.get_param(self._ATTENDANCE_SYNC_PARAM)
        if last_sync_str:
            last_sync = fields.Date.from_string(last_sync_str)
            if last_sync > end_date:
                end_date = last_sync
        window_days = int(ICP.get_param('rmc.attendance.sync_days', 30) or 30)
        window_days = max(window_days, 7)
        start_date = end_date - timedelta(days=window_days - 1)
        if start_date > end_date:
            start_date = end_date
        return start_date, end_date

    @api.model
    def _build_employee_agreement_map(self, employee_ids):
        """Return a {employee_id: agreement} mapping."""
        employee_ids = [eid for eid in set(employee_ids) if eid]
        mapping = {}
        if not employee_ids:
            return mapping
        Agreement = self.env['rmc.contract.agreement'].sudo()
        agreements = Agreement.search([
            '|',
            ('manpower_matrix_ids.employee_id', 'in', employee_ids),
            ('driver_ids', 'in', employee_ids)
        ])
        for agreement in agreements:
            employees = (agreement.driver_ids | agreement.manpower_matrix_ids.mapped('employee_id')).filtered(lambda e: e and e.id in employee_ids)
            for employee in employees:
                mapping.setdefault(employee.id, agreement)
        return mapping

    @api.model
    def _derive_documents_flag(self, employees):
        """Set documents_ok if each employee has an attachment on their profile."""
        employees = employees.filtered(lambda e: e)
        if not employees:
            return False
        Attachment = self.env['ir.attachment'].sudo()
        attachments = Attachment.search([
            ('res_model', '=', 'hr.employee'),
            ('res_id', 'in', employees.ids)
        ])
        employees_with_docs = set(attachments.mapped('res_id'))
        missing = employees.filtered(lambda e: e.id not in employees_with_docs)
        return not missing

    @api.model
    def _get_supervisor_keywords(self):
        ICP = self.env['ir.config_parameter'].sudo()
        raw = ICP.get_param(self._SUPERVISOR_KEYWORDS_PARAM, 'supervisor,shift incharge,shift lead')
        return [kw.strip().lower() for kw in raw.split(',') if kw.strip()]

    @api.model
    def _derive_supervisor_flag(self, employees):
        """Heuristic: supervisor present if any employee title/category matches keywords."""
        employees = employees.filtered(lambda e: e)
        if not employees:
            return False
        keywords = self._get_supervisor_keywords()
        if not keywords:
            return True
        for employee in employees:
            parts = [
                employee.job_title or '',
                employee.department_id.name if employee.department_id else '',
            ]
            parts.extend(employee.category_ids.mapped('name'))
            haystack = ' '.join(parts).lower()
            if any(keyword in haystack for keyword in keywords):
                return True
        return False

    def _localize_attendance_date(self, dt_value):
        if not dt_value:
            return False
        dt_obj = fields.Datetime.to_datetime(dt_value)
        local_dt = fields.Datetime.context_timestamp(self, dt_obj)
        return local_dt.date()

    @api.model
    def _attendance_domain(self, start_date, end_date):
        """Build a domain matching attendance entries that overlap the window."""
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        start_str = fields.Datetime.to_string(start_dt)
        end_str = fields.Datetime.to_string(end_dt)
        check_in_domain = expression.AND([
            [('check_in', '!=', False)],
            [('check_in', '>=', start_str)],
            [('check_in', '<', end_str)],
        ])
        check_out_domain = expression.AND([
            [('check_out', '!=', False)],
            [('check_out', '>=', start_str)],
            [('check_out', '<', end_str)],
        ])
        open_range = expression.AND([
            [('check_in', '<', end_str)],
            [('check_out', '>', start_str)],
        ])
        return expression.OR([
            expression.AND([check_in_domain, [('check_out', '=', False)]]),
            expression.AND([check_out_domain, [('check_in', '=', False)]]),
            open_range,
        ])

    @api.model
    def cron_sync_from_hr_attendance(self):
        """Daily cron that auto-creates attendance compliance entries."""
        self = self.sudo()
        window = self._attendance_sync_window()
        if not window or not all(window):
            _logger.debug("Attendance auto-sync skipped: empty window %s", window)
            return
        start_date, end_date = window
        Attendance = self.env['hr.attendance'].sudo()
        attendances = Attendance.search(self._attendance_domain(start_date, end_date))
        if not attendances:
            ICP = self.env['ir.config_parameter'].sudo()
            ICP.set_param(self._ATTENDANCE_SYNC_PARAM, fields.Date.to_string(end_date))
            _logger.info("Attendance auto-sync: no records between %s and %s", start_date, end_date)
            return
        employee_ids = attendances.filtered(lambda a: not a.agreement_id).mapped('employee_id').ids
        agreement_map = self._build_employee_agreement_map(employee_ids)
        bucket = defaultdict(lambda: {
            'agreement': False,
            'employees': self.env['hr.employee'],
            'first_check_in': False,
            'last_check_out': False,
        })
        for attendance in attendances:
            employee = attendance.employee_id
            if not employee:
                continue
            agreement = attendance.agreement_id or agreement_map.get(employee.id)
            if not agreement:
                continue
            date_value = self._localize_attendance_date(attendance.check_in or attendance.check_out)
            if not date_value:
                continue
            key = (agreement.id, date_value)
            bucket[key]['agreement'] = agreement
            bucket[key]['employees'] |= employee
            if attendance.check_in:
                first_ci = bucket[key]['first_check_in']
                if not first_ci or attendance.check_in < first_ci:
                    bucket[key]['first_check_in'] = attendance.check_in
            if attendance.check_out:
                last_co = bucket[key]['last_check_out']
                if not last_co or attendance.check_out > last_co:
                    bucket[key]['last_check_out'] = attendance.check_out
            # record employees for the target day/agreement
        ctx = dict(self.env.context, rmc_attendance_skip_sync=True)
        created = updated = 0
        for (agreement_id, day), payload in bucket.items():
            employees = payload['employees']
            if not employees:
                continue
            documents_ok = self._derive_documents_flag(employees)
            supervisor_ok = self._derive_supervisor_flag(employees)
            vals = {
                'agreement_id': agreement_id,
                'date': day,
                'employee_ids': [(6, 0, employees.ids)],
                'headcount_present': len(employees),
                'documents_ok': documents_ok,
                'supervisor_ok': supervisor_ok,
                'first_check_in': payload['first_check_in'],
                'last_check_out': payload['last_check_out'],
            }
            record = self.search([
                ('agreement_id', '=', agreement_id),
                ('date', '=', day),
            ], limit=1)
            if record:
                record.with_context(ctx).write(vals)
                updated += 1
            else:
                record = self.with_context(ctx).create(vals)
                created += 1
            record._auto_validate_from_sync()
        self.env['ir.config_parameter'].sudo().set_param(self._ATTENDANCE_SYNC_PARAM, fields.Date.to_string(end_date))
        _logger.info(
            "Attendance auto-sync done for %s-%s (created=%s, updated=%s)",
            start_date, end_date, created, updated
        )

    _sql_constraints = [
        (
            'rmc_attendance_headcount_positive',
            'CHECK(headcount_present >= 0)',
            'Present headcount must be non-negative.'
        ),
    ]

    @api.depends('employee_ids', 'date')
    def _compute_attendance_entries(self):
        Attendance = self.env['hr.attendance']
        for record in self:
            record.attendance_entry_ids = Attendance.browse()
            if not record.date or not record.employee_ids:
                continue
            domain = expression.AND([
                [('employee_id', 'in', record.employee_ids.ids)],
                self._attendance_domain(record.date, record.date),
            ])
            attendances = Attendance.search(domain, order='employee_id,check_in')
            target_date = record.date
            if attendances and target_date:
                attendances = attendances.filtered(
                    lambda att: record._localize_attendance_date(att.check_in or att.check_out) == target_date
                )
            record.attendance_entry_ids = attendances
