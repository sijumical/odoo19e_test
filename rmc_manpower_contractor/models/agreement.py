# -*- coding: utf-8 -*-
"""
RMC Contract Agreement Model
Main model for contractor lifecycle management
"""

import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta, time, date

try:  # pragma: no cover - optional deps not always present in CI
    import pytz
except ModuleNotFoundError:  # pragma: no cover
    from odoo_shims import pytz

try:  # pragma: no cover
    from dateutil.relativedelta import relativedelta
except ModuleNotFoundError:  # pragma: no cover
    from odoo_shims.relativedelta import relativedelta

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools.safe_eval import safe_eval
from odoo.tools.float_utils import float_compare, float_is_zero

from . import retention_common

_logger = logging.getLogger(__name__)

class RmcContractAgreement(models.Model):
    _name = 'rmc.contract.agreement'
    _description = 'RMC Contract Agreement'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _LOCKED_STATES = ('active', 'expired', 'closure_review', 'settled')
    _LOCK_BYPASS_CONTEXT_KEY = 'allow_agreement_locked_write'
    _LOCK_ALLOWED_FIELD_PREFIXES = ('message_', 'activity_', 'rating_')
    _LOCK_ALWAYS_ALLOWED_FIELDS = {
        'state',
        'next_agreement_id',
        'previous_agreement_id',
        'sign_request_id',
        'sign_state',
        'is_agreement_signed',
        'preview_pdf',
        'preview_pdf_filename',
        'preview_cache_key',
        'message_main_attachment_id',
    }
    _LOCK_ALLOWED_FIELDS_CACHE = None

    # Basic Information
    name = fields.Char(
        string='Agreement Reference',
        required=True,
        copy=False,
        readonly=True,
        index=True,
        default=lambda self: _('New'),
        tracking=True
    )
    contractor_id = fields.Many2one(
        'res.partner',
        string='Contractor',
        required=True,
        # domain="[('supplier_rank', '>', 0)]",
        tracking=True
    )
    vendor_id = fields.Many2one(
        'res.partner',
        string='Vendor',
        related='contractor_id',
        store=True,
        readonly=False,
        help='Alias to contractor for integrations expecting a vendor_id field.'
    )
    contract_type = fields.Selection([
        ('driver_transport', 'Transport/Driver Contract'),
        ('pump_ops', 'Workforce Supply & Operations Agreement'),
        ('accounts_audit', 'Accounts & Auditor Manpower')
    ], string='Contract Type', required=True, tracking=True)
    
    # State Management
    state = fields.Selection([
        ('draft', 'Draft'),
        ('offer', 'Offer Sent'),
        ('negotiation', 'Under Negotiation'),
        ('registration', 'Registration'),
        ('verification', 'Verification'),
        ('sign_pending', 'Pending Signature'),
        ('active', 'Active'),
        ('suspended', 'Suspended'),
        ('expired', 'Expired'),
        ('closure_review', 'Closure Review'),
        ('settled', 'Settled')
    ], string='Status', default='draft', required=True, tracking=True)

    previous_agreement_id = fields.Many2one(
        'rmc.contract.agreement',
        string='Previous Agreement',
        copy=False,
        index=True,
        tracking=True,
        help='Agreement that this record supersedes.'
    )
    next_agreement_id = fields.Many2one(
        'rmc.contract.agreement',
        string='Next Agreement',
        copy=False,
        help='Future renewal created from this agreement.'
    )
    revision_no = fields.Integer(
        string='Revision Number',
        default=1,
        copy=False,
        tracking=True,
        help='Sequential revision counter for renewal chains.'
    )
    has_previous_agreement = fields.Boolean(
        string='Has Previous',
        compute='_compute_chain_meta'
    )
    has_next_agreement = fields.Boolean(
        string='Has Next',
        compute='_compute_chain_meta'
    )
    previous_agreement_count = fields.Integer(
        string='Previous Versions',
        compute='_compute_chain_meta'
    )
    next_agreement_count = fields.Integer(
        string='Next Versions',
        compute='_compute_chain_meta'
    )

    # Sign Integration
    sign_template_id = fields.Many2one(
        'sign.template',
        string='Sign Template',
        help='Odoo Sign template to use for this agreement'
    )
    sign_request_id = fields.Many2one(
        'sign.request',
        string='Sign Request',
        readonly=True,
        copy=False
    )
    sign_state = fields.Selection(
        related='sign_request_id.state',
        string='Signature Status',
        store=True
    )
    is_agreement_signed = fields.Boolean(
        string='Is Signed',
        compute='_compute_is_signed',
        store=True
    )
    preview_pdf = fields.Binary(
        string='Cached Preview PDF',
        attachment=True,
        copy=False
    )
    preview_pdf_filename = fields.Char(
        string='Preview Filename',
        copy=False
    )
    preview_cache_key = fields.Char(
        string='Preview Cache Key',
        copy=False
    )

    # Website/Portal
    dynamic_web_path = fields.Char(
        string='Web Path',
        compute='_compute_web_path',
        store=True
    )

    # Validity Period
    validity_start = fields.Date(string='Valid From', tracking=True)
    validity_end = fields.Date(string='Valid Until', tracking=True)
    start_date = fields.Date(
        string='Start Date',
        related='validity_start',
        store=True,
        readonly=False,
        required=True
    )
    end_date = fields.Date(
        string='End Date',
        related='validity_end',
        store=True,
        readonly=False,
        required=True
    )

    # Financial Fields - Wage Matrix
    mgq_target = fields.Float(
        string='MGQ Target (m³)',
        help='Minimum Guaranteed Quantity target for the month',
        digits='Product Unit of Measure'
    )
    part_a_fixed = fields.Monetary(
        string='Part-A Fixed Amount',
        currency_field='currency_id',
        help='Fixed monthly payment component'
    )
    part_b_variable = fields.Monetary(
        string='Part-B Variable Amount',
        currency_field='currency_id',
        help='Variable payment linked to MGQ achievement'
    )
    total_amount = fields.Monetary(
        string='Total Amount',
        currency_field='currency_id',
        compute='_compute_total_amount',
        inverse='_inverse_total_amount',
        store=True,
        help='Sum of Part-A fixed and Part-B variable components'
    )
    manpower_part_a_amount = fields.Monetary(
        string='Manpower Part-A Total',
        currency_field='currency_id',
        help='Aggregated Part-A (fixed) wages from the manpower matrix.'
    )
    manpower_part_b_amount = fields.Monetary(
        string='Manpower Part-B Total',
        currency_field='currency_id',
        help='Aggregated Part-B (variable) wages from the manpower matrix.'
    )
    manpower_matrix_total_amount = fields.Monetary(
        string='Manpower Matrix Total',
        currency_field='currency_id',
        help='Total monthly wages from the manpower matrix.'
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id
    )
    retention_rate = fields.Float(
        string='Retention %',
        default=2.0,
        tracking=True,
        help='Retention percentage held from the configured base.'
    )
    retention_duration = fields.Selection(
        retention_common.RETENTION_DURATION_SELECTION,
        string='Retention Duration',
        required=True,
        default='90_days',
        help='How long the retention amount stays on hold before release.'
    )
    retention_base = fields.Selection(
        retention_common.RETENTION_BASE_SELECTION,
        string='Retention Base',
        required=True,
        default='untaxed',
        help='Select whether retention is computed over untaxed or total invoice amount.'
    )
    auto_apply = fields.Boolean(
        string='Auto Apply Retention',
        default=True,
        help='Automatically book retention holds on linked vendor bills.'
    )
    retention_entry_ids = fields.One2many(
        'rmc.agreement.retention',
        'agreement_id',
        string='Retention Holds',
        copy=False
    )
    retention_balance = fields.Monetary(
        string='Retention Balance',
        currency_field='currency_id',
        compute='_compute_retention_amounts',
        store=True
    )
    retention_released_amount = fields.Monetary(
        string='Retention Released',
        currency_field='currency_id',
        compute='_compute_retention_amounts',
        store=True
    )

    @api.depends('retention_entry_ids.retention_amount', 'retention_entry_ids.release_state')
    def _compute_retention_amounts(self):
        company_currency = self.env.company.currency_id
        for agreement in self:
            balance = 0.0
            released = 0.0
            for entry in agreement.retention_entry_ids:
                amount = entry.retention_amount or 0.0
                if entry.release_state == 'released':
                    released += amount
                elif entry.release_state != 'cancelled':
                    balance += amount
            currency = agreement.currency_id or company_currency
            rounding = currency.rounding if currency else 0.01
            agreement.retention_balance = currency.round(balance) if currency else balance
            agreement.retention_released_amount = currency.round(released) if currency else released

    # Manpower Matrix (One2many)
    manpower_matrix_ids = fields.One2many(
        'rmc.manpower.matrix',
        'agreement_id',
        string='Manpower Matrix',
        help='Designation-wise headcount and rates'
    )

    # Performance Metrics
    performance_score = fields.Float(
        string='Performance Score',
        digits=(5, 2),
        compute='_compute_performance',
        store=True,
        tracking=True
    )
    stars = fields.Selection([
        ('1', '⭐'),
        ('2', '⭐⭐'),
        ('3', '⭐⭐⭐'),
        ('4', '⭐⭐⭐⭐'),
        ('5', '⭐⭐⭐⭐⭐')
    ], string='Star Rating', compute='_compute_stars', store=True)

    # Type-Specific KPIs
    avg_diesel_efficiency = fields.Float(
        string='Avg Diesel Efficiency (km/l or m³/l)',
        digits=(5, 2),
        compute='_compute_diesel_kpi',
        store=True
    )
    maintenance_compliance = fields.Float(
        string='Maintenance Compliance (%)',
        digits=(5, 2),
        compute='_compute_maintenance_kpi',
        store=True
    )
    attendance_compliance = fields.Float(
        string='Attendance Compliance (%)',
        digits=(5, 2),
        compute='_compute_attendance_kpi',
        store=True
    )
    prime_output_qty = fields.Float(
        string='Prime Output (m³)',
        digits='Product Unit of Measure',
        tracking=True,
        help='Cumulative prime output achieved for this agreement.'
    )
    optimized_standby_qty = fields.Float(
        string='Optimized Standby (m³)',
        digits='Product Unit of Measure',
        tracking=True,
        help='Standby volume derived from MGQ vs achieved prime output.'
    )

    @api.depends('part_a_fixed', 'part_b_variable')
    def _compute_total_amount(self):
        for agreement in self:
            agreement.total_amount = (agreement.part_a_fixed or 0.0) + (agreement.part_b_variable or 0.0)

    def _inverse_total_amount(self):
        for agreement in self:
            total = agreement.total_amount or 0.0
            part_a = agreement.part_a_fixed or 0.0
            part_b = total - part_a
            agreement.part_b_variable = part_b if part_b > 0 else 0.0

    def _update_manpower_totals_from_matrix(self):
        """
        Synchronise stored manpower totals and Part-A fixed amount
        based on the current manpower matrix lines.
        """
        for agreement in self:
            part_a_total = 0.0
            part_b_total = 0.0
            has_part_b_lines = False
            for line in agreement.manpower_matrix_ids:
                subtotal = (line.headcount or 0.0) * (line.base_rate or 0.0)
                if line.remark == 'part_b':
                    part_b_total += subtotal
                    has_part_b_lines = True
                else:
                    part_a_total += subtotal
            overall_total = part_a_total + part_b_total
            currency = agreement.currency_id
            rounding = currency.rounding if currency else 0.01
            updates = {}
            if float_compare(part_a_total, agreement.manpower_part_a_amount or 0.0, precision_rounding=rounding):
                updates['manpower_part_a_amount'] = part_a_total
            if float_compare(part_b_total, agreement.manpower_part_b_amount or 0.0, precision_rounding=rounding):
                updates['manpower_part_b_amount'] = part_b_total
            if float_compare(overall_total, agreement.manpower_matrix_total_amount or 0.0, precision_rounding=rounding):
                updates['manpower_matrix_total_amount'] = overall_total
            if float_compare(part_a_total, agreement.part_a_fixed or 0.0, precision_rounding=rounding):
                updates['part_a_fixed'] = part_a_total

            prev_matrix_part_b_total = agreement.manpower_part_b_amount or 0.0
            sync_part_b = has_part_b_lines or not float_is_zero(prev_matrix_part_b_total, precision_rounding=rounding)
            if sync_part_b and float_compare(part_b_total, agreement.part_b_variable or 0.0, precision_rounding=rounding):
                updates['part_b_variable'] = part_b_total

            if updates:
                super(RmcContractAgreement, agreement).write(updates)

    def _snapshot_terms(self):
        """Return a normalized snapshot of key agreement terms for diffing."""
        self.ensure_one()

        def _float(value):
            return float(value or 0.0)

        def _int(value):
            return int(value or 0)

        matrix_rows = []
        matrix_lines = self.manpower_matrix_ids.sorted(key=lambda line: (line.designation or '', line.id))
        for line in matrix_lines:
            matrix_rows.append({
                'designation': line.designation or '',
                'employee_id': line.employee_id.id or False,
                'vehicle_id': line.vehicle_id.id or False,
                'headcount': _int(line.headcount),
                'shift': line.shift or '',
                'remark': line.remark or '',
                'base_rate': _float(line.base_rate),
            })

        clauses = []
        clause_lines = self.clause_ids.sorted(key=lambda clause: (clause.sequence or 0, clause.title or '', clause.id))
        for clause in clause_lines:
            clauses.append({
                'sequence': _int(clause.sequence),
                'title': clause.title or '',
            })

        rules = []
        bonus_lines = self.bonus_rule_ids.sorted(key=lambda rule: (rule.sequence or 0, rule.name or '', rule.id))
        for rule in bonus_lines:
            rules.append({
                'sequence': _int(rule.sequence),
                'name': rule.name or '',
                'rule_type': rule.rule_type or '',
                'trigger_condition': rule.trigger_condition or '',
                'percentage': _float(rule.percentage),
            })

        return {
            'financial': {
                'mgq_target': _float(self.mgq_target),
                'part_a_fixed': _float(self.part_a_fixed),
                'part_b_variable': _float(self.part_b_variable),
            },
            'matrix': matrix_rows,
            'clauses': clauses,
            'bonus_rules': rules,
        }

    @staticmethod
    def _dates_overlap(start_a, end_a, start_b, end_b):
        start_a = start_a or date.min
        start_b = start_b or date.min
        end_a = end_a or date.max
        end_b = end_b or date.max
        return start_a <= end_b and start_b <= end_a

    def _get_retention_base_amount_from_bill(self, bill):
        self.ensure_one()
        if self.retention_base == 'total':
            base_amount = bill.amount_total
        else:
            base_amount = bill.amount_untaxed
        return abs(base_amount or 0.0)

    def _get_retention_release_date(self, bill):
        self.ensure_one()
        reference_date = bill.invoice_date_due or bill.invoice_date or fields.Date.context_today(self)
        duration = self.retention_duration or '90_days'
        if duration == '90_days':
            return reference_date + timedelta(days=90)
        if duration == '6_months':
            return reference_date + relativedelta(months=6)
        if duration == '1_year':
            return reference_date + relativedelta(years=1)
        # over_period
        return self.end_date or self.validity_end or reference_date

    def _prepare_retention_entry_vals(self, bill):
        self.ensure_one()
        if not self.auto_apply or not self.retention_rate or bill.move_type != 'in_invoice':
            return False
        currency = bill.currency_id or self.currency_id
        if not currency:
            return False
        base_amount = self._get_retention_base_amount_from_bill(bill)
        retention_amount = currency.round(base_amount * (self.retention_rate / 100.0))
        if float_is_zero(retention_amount, precision_rounding=currency.rounding):
            return False
        release_date = self._get_retention_release_date(bill)
        company = self.company_id or bill.company_id or self.env.company
        return {
            'agreement_id': self.id,
            'move_id': bill.id,
            'company_id': company.id,
            'currency_id': currency.id,
            'base_amount': base_amount,
            'retention_amount': retention_amount,
            'retention_rate': self.retention_rate,
            'retention_base': self.retention_base,
            'retention_duration': self.retention_duration,
            'scheduled_release_date': release_date,
        }

    def _create_retention_entry_from_bill(self, bill):
        self.ensure_one()
        vals = self._prepare_retention_entry_vals(bill)
        if not vals:
            return False
        existing = bill.retention_entry_ids.filtered(lambda r: r.release_state == 'pending')
        if existing:
            existing.unlink()
        retention = self.env['rmc.agreement.retention'].create(vals)
        bill.write({
            'retention_amount': vals['retention_amount'],
            'retention_base_amount': vals['base_amount'],
            'retention_release_date': vals['scheduled_release_date'],
            'release_due_date': vals['scheduled_release_date'],
        })
        return retention

    # Pending Items
    pending_items_count = fields.Integer(
        string='Pending Items',
        compute='_compute_pending_items',
        store=True
    )
    
    # Payment Hold Logic
    payment_hold = fields.Boolean(
        string='Payment on Hold',
        compute='_compute_payment_hold',
        store=True,
        tracking=True
    )
    payment_hold_reason = fields.Text(
        string='Hold Reason',
        compute='_compute_payment_hold'
    )
    settlement_hold = fields.Boolean(
        string='Settlement On Hold',
        help='Raised when settlement pre-checks fail.'
    )
    settlement_hold_reason = fields.Text(
        string='Settlement Hold Reason'
    )

    # Related Records (One2many)
    diesel_log_ids = fields.One2many(
        'rmc.diesel.log',
        'agreement_id',
        string='Diesel Logs'
    )
    maintenance_check_ids = fields.One2many(
        'rmc.maintenance.check',
        'agreement_id',
        string='Maintenance Checks'
    )
    attendance_compliance_ids = fields.One2many(
        'rmc.attendance.compliance',
        'agreement_id',
        string='Attendance Records'
    )
    vehicle_ids = fields.Many2many(
        'fleet.vehicle',
        'rmc_agreement_vehicle_rel',
        'agreement_id',
        'vehicle_id',
        string='Fleet Vehicles',
        compute='_compute_assignment_resources',
        store=True,
        help='Fleet vehicles assigned to this agreement and used for diesel logging.'
    )
    fleet_vehicle_count = fields.Integer(
        string='Fleet Vehicles',
        compute='_compute_counts',
        store=True
    )
    vehicle_diesel_log_ids = fields.Many2many(
        'diesel.log',
        string='Vehicle Diesel Logs',
        compute='_compute_vehicle_diesel_logs',
        help='Diesel log entries captured on fleet vehicles assigned to this agreement.'
    )
    equipment_ids = fields.Many2many(
        'maintenance.equipment',
        string='Assigned Equipment',
        compute='_compute_equipment_resources',
        store=True,
        help='Maintenance equipment assigned to employees linked to this agreement.'
    )
    equipment_request_ids = fields.Many2many(
        'maintenance.request',
        string='Equipment Maintenance Requests',
        compute='_compute_equipment_resources',
        help='Maintenance requests belonging to equipment assigned on this agreement.'
    )
    equipment_count = fields.Integer(
        string='Equipment',
        compute='_compute_counts',
        store=True
    )
    equipment_request_count = fields.Integer(
        string='Equipment Requests',
        compute='_compute_counts',
        store=True
    )
    employee_attendance_ids = fields.Many2many(
        'hr.attendance',
        string='Employee Attendance',
        compute='_compute_employee_attendance',
        help='HR attendance entries corresponding to agreement employees.'
    )
    employee_attendance_count = fields.Integer(
        string='Attendance Records',
        compute='_compute_counts'
    )
    billing_prepare_log_ids = fields.One2many(
        'rmc.billing.prepare.log',
        'agreement_id',
        string='Billing Logs'
    )
    billing_prepare_log_count = fields.Integer(
        string='Billing Logs',
        compute='_compute_counts'
    )
    activity_start_date = fields.Date(
        string='Activity Start Date',
        compute='_compute_activity_start_date'
    )
    driver_ids = fields.Many2many(
        'hr.employee',
        'rmc_agreement_employee_rel',
        'agreement_id',
        'employee_id',
        string='Assigned Employees',
        compute='_compute_assignment_resources',
        store=True,
        help='Employees (drivers/operators) linked to this agreement for attendance and KPI tracking.'
    )
    signer_ids = fields.One2many(
        'rmc.agreement.signer',
        'agreement_id',
        string='Agreement Signers',
        help='Optional overrides for sign template roles. Leave blank to use defaults.'
    )

    @api.depends(
        'manpower_matrix_ids.employee_id',
        'manpower_matrix_ids.employee_id.car_ids',
        'manpower_matrix_ids.vehicle_id'
    )
    def _compute_assignment_resources(self):
        for agreement in self:
            employees = agreement.manpower_matrix_ids.mapped('employee_id').filtered(lambda e: e)
            matrix_vehicles = agreement.manpower_matrix_ids.mapped('vehicle_id')
            employee_vehicles = employees.mapped('car_ids')
            vehicles = (matrix_vehicles | employee_vehicles).filtered(lambda v: v)
            agreement.driver_ids = employees
            agreement.vehicle_ids = vehicles

    def _get_activity_start_datetime(self):
        self.ensure_one()
        user_tz = pytz.timezone(self.env.user.tz or 'UTC')
        dt_local = None
        if self.validity_start:
            dt_local = user_tz.localize(datetime.combine(self.validity_start, time.min))
        elif self.sign_request_id and self.sign_request_id.completion_date:
            dt_local = user_tz.localize(datetime.combine(self.sign_request_id.completion_date, time.min))
        else:
            base_dt = self.create_date or fields.Datetime.now()
            dt_local = fields.Datetime.context_timestamp(self, base_dt)
        if dt_local.tzinfo is None:
            dt_local = user_tz.localize(dt_local)
        dt_utc = dt_local.astimezone(pytz.UTC).replace(tzinfo=None)
        return dt_utc

    @api.depends(
        'driver_ids',
        'manpower_matrix_ids.employee_id',
        'manpower_matrix_ids.employee_id.equipment_ids'
    )
    def _compute_equipment_resources(self):
        Equipment = self.env['maintenance.equipment']
        Request = self.env['maintenance.request']
        for agreement in self:
            employees = (agreement.driver_ids | agreement.manpower_matrix_ids.mapped('employee_id')).filtered(lambda e: e)
            if employees:
                start_dt = agreement._get_activity_start_datetime()
                domain = [('employee_id', 'in', employees.ids)]
                if 'assignment_date' in Equipment._fields:
                    domain += ['|', ('assignment_date', '=', False), ('assignment_date', '>=', start_dt.date())]
                if 'agreement_id' in Equipment._fields:
                    domain.append(('agreement_id', '=', agreement.id))
                equipments = Equipment.search(domain)
            else:
                equipments = Equipment.browse()
            agreement.equipment_ids = equipments
            if equipments:
                start_dt = agreement._get_activity_start_datetime()
                domain = [('equipment_id', 'in', equipments.ids)]
                if 'request_date' in Request._fields:
                    domain += ['|', ('request_date', '=', False), ('request_date', '>=', start_dt.date())]
                if 'agreement_id' in Request._fields:
                    domain.append(('agreement_id', '=', agreement.id))
                requests = Request.search(domain)
            else:
                requests = Request.browse()
            agreement.equipment_request_ids = requests

    @api.depends('driver_ids', 'manpower_matrix_ids.employee_id')
    def _compute_employee_attendance(self):
        Attendance = self.env['hr.attendance']
        for agreement in self:
            employees = (agreement.driver_ids | agreement.manpower_matrix_ids.mapped('employee_id')).filtered(lambda e: e)
            if employees:
                start_dt = agreement._get_activity_start_datetime()
                attendances = Attendance.search([('employee_id', 'in', employees.ids)])
                attendances = attendances.filtered(lambda a: (a.check_in and a.check_in >= start_dt) or (a.check_out and a.check_out >= start_dt))
            else:
                attendances = Attendance.browse()
            agreement.employee_attendance_ids = attendances

    def _compute_vehicle_diesel_logs(self):
        for agreement in self:
            logs = self.env['diesel.log']
            if agreement.vehicle_ids:
                domain = [('vehicle_id', 'in', agreement.vehicle_ids.ids)]
                start_dt = agreement._get_activity_start_datetime()
                if 'date' in logs._fields:
                    domain += ['|', ('date', '=', False), ('date', '>=', start_dt.date())]
                logs = logs.search(domain)
            else:
                logs = logs.browse()
            agreement.vehicle_diesel_log_ids = logs

    def _generate_contract_pdf(self):
        self.ensure_one()
        self._ensure_clause_defaults()
        report = self.env.ref('rmc_manpower_contractor.action_report_agreement_contract')
        pdf_bytes, _ = report._render_qweb_pdf(report.report_name, res_ids=[self.id])
        filename = f"{self.name or 'agreement'}.pdf"
        return pdf_bytes, filename

    def _compute_preview_cache_key(self):
        """Build a stable fingerprint representing the preview content."""
        self.ensure_one()
        payload = {
            'agreement': {
                'id': self.id,
                'write_date': self.write_date,
                'validity_start': self.validity_start,
                'validity_end': self.validity_end,
                'mgq_target': self.mgq_target,
                'part_a_fixed': self.part_a_fixed,
                'part_b_variable': self.part_b_variable,
                'notes': self.notes,
                'currency_id': self.currency_id.id,
                'company_id': self.company_id.id,
                'sign_template_id': self.sign_template_id.id,
                'state': self.state,
            },
            'manpower_matrix': [
                {
                    'id': line.id,
                    'write_date': line.write_date or line.create_date,
                    'employee_id': line.employee_id.id,
                    'vehicle_id': line.vehicle_id.id,
                    'shift': line.shift,
                    'remark': line.remark,
                    'total_amount': line.total_amount,
                }
                for line in self.manpower_matrix_ids.sorted(key=lambda r: r.id)
            ],
            'clauses': [
                {
                    'id': clause.id,
                    'write_date': clause.write_date or clause.create_date,
                    'title': clause.title,
                }
                for clause in self.clause_ids.sorted(key=lambda r: r.id)
            ],
        }
        serialized = json.dumps(payload, default=str, sort_keys=True)
        return hashlib.sha1(serialized.encode('utf-8')).hexdigest()

    def _store_preview_pdf(self, pdf_bytes, filename, cache_key=None):
        """Persist the generated preview PDF for subsequent requests."""
        self.ensure_one()
        cache_key = cache_key or self._compute_preview_cache_key()
        encoded = base64.b64encode(pdf_bytes).decode('utf-8')
        self.write({
            'preview_pdf': encoded,
            'preview_pdf_filename': filename,
            'preview_cache_key': cache_key,
        })

    def _get_cached_preview_pdf(self):
        self.ensure_one()
        self._update_manpower_totals_from_matrix()
        cache_key = self._compute_preview_cache_key()
        if self.preview_pdf and self.preview_cache_key == cache_key:
            filename = self.preview_pdf_filename or f"{self.name or 'agreement'}.pdf"
            return base64.b64decode(self.preview_pdf), filename
        pdf_bytes, filename = self._generate_contract_pdf()
        self._store_preview_pdf(pdf_bytes, filename, cache_key=cache_key)
        return pdf_bytes, filename

    def _refresh_sign_template(self, pdf_bytes, filename):
        self.ensure_one()
        if not self.sign_template_id:
            raise UserError(_('Please configure a Sign Template before sending for signature.'))

        template = self.sign_template_id
        template_sudo = template.sudo()
        template_sudo.write({
            'authorized_ids': [(4, self.env.user.id)],
            'favorited_ids': [(4, self.env.user.id)],
        })
        if template_sudo.has_sign_requests:
            authorized_ids = template_sudo.authorized_ids.ids
            favorited_ids = template_sudo.favorited_ids.ids
            if self.env.user.id not in authorized_ids:
                authorized_ids.append(self.env.user.id)
            if self.env.user.id not in favorited_ids:
                favorited_ids.append(self.env.user.id)
            copy_vals = {
                'name': '%s - %s' % (
                    self.name or _('Agreement'),
                    _('Signature Template Copy')
                ),
                'authorized_ids': [(6, 0, authorized_ids)],
                'favorited_ids': [(6, 0, favorited_ids)],
            }
            template_sudo = template_sudo.copy(copy_vals)
            template_sudo.write({
                'authorized_ids': [(4, self.env.user.id)],
                'favorited_ids': [(4, self.env.user.id)],
            })
            self.sign_template_id = template_sudo.id
            template = template_sudo

        existing_documents = template_sudo.document_ids.sorted('sequence')
        document_blueprints = []
        for document in existing_documents:
            document_blueprints.append({
                'sequence': document.sequence,
                'items': document.sign_item_ids.copy_data(),
            })

        existing_documents.unlink()

        encoded_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
        template_sudo.update_from_attachment_data([{
            'name': filename,
            'datas': encoded_pdf,
        }])

        new_documents = template_sudo.document_ids.sorted('sequence')
        SignItem = self.env['sign.item'].sudo()
        for blueprint, new_document in zip(document_blueprints, new_documents):
            item_vals_list = blueprint.get('items') or []
            if item_vals_list:
                for item_vals in item_vals_list:
                    item_vals['document_id'] = new_document.id
                SignItem.create(item_vals_list)
            if blueprint.get('sequence') is not None:
                new_document.sequence = blueprint['sequence']

        if not template_sudo.sign_item_ids:
            seed_template = self._get_sign_template_seed()
            if seed_template and seed_template != template:
                seed_documents = seed_template.document_ids.sorted('sequence')
                for index, new_document in enumerate(new_documents):
                    if not seed_documents:
                        break
                    source_document = seed_documents[min(index, len(seed_documents) - 1)]
                    seed_item_vals = source_document.sign_item_ids.copy_data()
                    if seed_item_vals:
                        for seed_vals in seed_item_vals:
                            seed_vals['document_id'] = new_document.id
                        SignItem.create(seed_item_vals)

        template_sudo.write({'name': filename})
        template_sudo._invalidate_cache(fnames=['document_ids', 'sign_item_ids'])
        self._ensure_signature_blocks()
        self._sync_signers_with_template()

    def _default_partner_for_role(self, role):
        self.ensure_one()
        contractor_partner = self.contractor_id
        company_partner = self.env.company.partner_id

        role_name = (role.name or '').lower()
        if contractor_partner and any(keyword in role_name for keyword in ['contractor', 'customer', 'supplier']):
            return contractor_partner
        if company_partner and any(keyword in role_name for keyword in ['company', 'internal', 'manager']):
            return company_partner

        return contractor_partner or company_partner

    def _get_sign_template_seed(self):
        """Return a sign.template record that can be used as a seed for duplication."""
        self.ensure_one()
        template = self.env.ref(
            'rmc_manpower_contractor.sign_template_rmc_contractor',
            raise_if_not_found=False
        )
        if template:
            return template

        domain = [
            ('active', '=', True),
            '|', ('company_id', '=', False),
            ('company_id', '=', self.company_id.id if self.company_id else self.env.company.id),
        ]
        return self.env['sign.template'].search(domain, order='id desc', limit=1)

    def _sync_signers_with_template(self):
        """Align signer overrides with the currently selected sign template."""
        for agreement in self:
            template = agreement.sign_template_id.sudo()
            if not template:
                agreement.signer_ids = [(5, 0, 0)]
                continue

            commands = [(5, 0, 0)]
            roles_added = set()
            sequence_counter = 10
            for item in template.sign_item_ids:
                role = item.responsible_id
                if not role or role.id in roles_added:
                    continue
                roles_added.add(role.id)
                default_partner = agreement._default_partner_for_role(role)
                commands.append((0, 0, {
                    'role_id': role.id,
                    'partner_id': default_partner.id if default_partner else False,
                    'sequence': sequence_counter,
                }))
                sequence_counter += 10
            agreement.signer_ids = commands

    def _get_or_create_role(self, xmlid, name):
        role = self.env.ref(xmlid, raise_if_not_found=False)
        if role:
            return role.sudo()
        role = self.env['sign.item.role'].sudo().search([('name', '=', name)], limit=1)
        if role:
            return role
        return self.env['sign.item.role'].sudo().create({'name': name})

    def _ensure_signature_blocks(self):
        """
        Make sure the sign template has signature + date fields for company and contractor.
        """
        SignItem = self.env['sign.item'].sudo()
        signature_type = self.env.ref('sign.sign_item_type_signature', raise_if_not_found=False)
        date_type = self.env.ref('sign.sign_item_type_date', raise_if_not_found=False)
        if not signature_type or not date_type:
            return

        for agreement in self:
            template = agreement.sign_template_id
            if not template or not template.document_ids:
                continue
            template_sudo = template.sudo()
            sign_items_sudo = template_sudo.sign_item_ids

            company_role = self._get_or_create_role(
                'rmc_manpower_contractor.sign_role_rmc_company',
                _('Company Signatory')
            )
            contractor_role = self._get_or_create_role(
                'rmc_manpower_contractor.sign_role_rmc_contractor',
                _('Contractor Signatory')
            )
            allowed_roles = company_role | contractor_role

            target_document = template_sudo.document_ids.sorted('sequence')[-1]
            target_page = target_document.num_pages or max(sign_items_sudo.mapped('page') or [1])

            stale_items = sign_items_sudo.filtered(
                lambda item: item.type_id in (signature_type, date_type) and item.responsible_id not in allowed_roles
            )
            if stale_items:
                stale_items.unlink()
                sign_items_sudo = template_sudo.sign_item_ids

            def _ensure_field(role, field_type, name, posx, posy, width, height):
                nonlocal sign_items_sudo
                existing = sign_items_sudo.filtered(
                    lambda item: item.responsible_id == role and item.type_id == field_type
                )
                if len(existing) > 1:
                    existing[1:].unlink()
                    sign_items_sudo = template_sudo.sign_item_ids
                    existing = sign_items_sudo.filtered(
                        lambda item: item.responsible_id == role and item.type_id == field_type
                    )
                existing = existing[:1].sudo()
                vals = {
                    'document_id': target_document.id,
                    'type_id': field_type.id,
                    'responsible_id': role.id if role else False,
                    'name': name,
                    'page': target_page,
                    'posX': posx,
                    'posY': posy,
                    'width': width,
                    'height': height,
                    'alignment': 'left',
                    'required': True,
                }
                if existing:
                    existing.write(vals)
                else:
                    new_item = SignItem.create(vals)
                    sign_items_sudo |= new_item

            # company block (left column)
            _ensure_field(company_role, date_type, _('Company Sign Date'), 0.12, 0.70, 0.20, 0.04)
            _ensure_field(company_role, signature_type, _('Company Signature'), 0.12, 0.76, 0.30, 0.08)
            # contractor block (right column)
            _ensure_field(contractor_role, date_type, _('Contractor Sign Date'), 0.56, 0.70, 0.20, 0.04)
            _ensure_field(contractor_role, signature_type, _('Contractor Signature'), 0.56, 0.76, 0.30, 0.08)
            template_sudo._invalidate_cache(fnames=['sign_item_ids'])

    def _ensure_sign_template(self):
        """
        Ensure each agreement has its own sign template.
        If none is linked yet, duplicate a seed template and assign it.
        """
        for agreement in self:
            if agreement.sign_template_id:
                template = agreement.sign_template_id
                template_sudo = template.sudo()
                template_sudo.write({
                    'authorized_ids': [(4, self.env.user.id)],
                    'favorited_ids': [(4, self.env.user.id)],
                })
                template_sudo._invalidate_cache(fnames=['document_ids', 'sign_item_ids'])
                agreement._ensure_signature_blocks()
                agreement._sync_signers_with_template()
                continue

            seed_template = agreement._get_sign_template_seed()
            if not seed_template:
                raise UserError(
                    _('No Sign Template configured. Please create a base template '
                      'under Sign > Configuration > Templates to seed agreement copies.')
                )

            copy_vals = {
                'name': '%s - %s' % (
                    agreement.name or _('Agreement'),
                    _('Signature Template')
                ),
                'user_id': self.env.user.id,
            }
            target_company = agreement.company_id or self.env.company
            seed_template_sudo = seed_template.sudo()
            new_template = seed_template_sudo.with_company(target_company).copy(copy_vals)

            agreement.with_company(target_company).write({
                'sign_template_id': new_template.id,
            })
            new_template_sudo = new_template.sudo()
            new_template_sudo.write({
                'authorized_ids': [(4, self.env.user.id)],
                'favorited_ids': [(4, self.env.user.id)],
            })
            new_template_sudo._invalidate_cache(fnames=['document_ids', 'sign_item_ids'])
            agreement._ensure_signature_blocks()
            agreement._sync_signers_with_template()

    @api.onchange('sign_template_id')
    def _onchange_sign_template_id(self):
        for agreement in self:
            agreement._sync_signers_with_template()
    breakdown_event_ids = fields.One2many(
        'rmc.breakdown.event',
        'agreement_id',
        string='Breakdown Events'
    )
    inventory_handover_ids = fields.One2many(
        'rmc.inventory.handover',
        'agreement_id',
        string='Inventory Handovers'
    )
    vendor_bill_ids = fields.One2many(
        'account.move',
        'agreement_id',
        string='Vendor Bills',
        domain=[('move_type', '=', 'in_invoice')]
    )

    # Smart Button Counts
    diesel_log_count = fields.Integer(compute='_compute_counts')
    maintenance_check_count = fields.Integer(compute='_compute_counts')
    attendance_compliance_count = fields.Integer(compute='_compute_counts')
    breakdown_event_count = fields.Integer(compute='_compute_counts')
    inventory_handover_count = fields.Integer(compute='_compute_counts')
    vendor_bill_count = fields.Integer(compute='_compute_counts')

    # Analytics
    analytic_account_id = fields.Many2one(
        'account.analytic.account',
        string='Analytic Account'
    )

    # Additional Info
    notes = fields.Html(string='Internal Notes')
    clause_ids = fields.One2many(
        'rmc.agreement.clause',
        'agreement_id',
        string='Clauses',
        copy=True,
        help='Editable clause sections that will appear in the agreement PDF.'
    )
    bonus_rule_ids = fields.One2many(
        'rmc.agreement.bonus.rule',
        'agreement_id',
        string='Bonus/Penalty Rules',
        copy=True,
        help='Agreement-specific bonus/penalty adjustments applied during billing.'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Generate sequence and create agreement"""
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'rmc.contract.agreement'
                ) or _('New')
        agreements = super(RmcContractAgreement, self).create(vals_list)
        agreements._ensure_clause_defaults()
        agreements._update_manpower_totals_from_matrix()
        return agreements

    def write(self, vals):
        self._check_locked_records_for_write(vals)
        contract_type_changed = 'contract_type' in vals
        matrix_updated = 'manpower_matrix_ids' in vals
        res = super().write(vals)
        if contract_type_changed:
            self._ensure_clause_defaults()
        if matrix_updated:
            self._update_manpower_totals_from_matrix()
        return res

    @api.depends('previous_agreement_id', 'next_agreement_id')
    def _compute_chain_meta(self):
        Agreement = self.env['rmc.contract.agreement']
        for agreement in self:
            if agreement.id:
                prev_records = agreement._collect_chain_records('previous')
                next_records = agreement._collect_chain_records('next')
            else:
                prev_records = Agreement.browse()
                next_records = Agreement.browse()
            agreement.has_previous_agreement = bool(prev_records)
            agreement.has_next_agreement = bool(next_records)
            agreement.previous_agreement_count = len(prev_records)
            agreement.next_agreement_count = len(next_records)

    @api.model
    def _get_lock_allowed_fields(self):
        cache = self.__class__._LOCK_ALLOWED_FIELDS_CACHE
        if cache is None:
            computed = {
                name for name, field in self._fields.items()
                if field.compute and not field.inverse
            }
            cache = frozenset(computed | self._LOCK_ALWAYS_ALLOWED_FIELDS)
            self.__class__._LOCK_ALLOWED_FIELDS_CACHE = cache
        return cache

    def _is_field_allowed_on_lock(self, field_name):
        if field_name in self._get_lock_allowed_fields():
            return True
        return any(field_name.startswith(prefix) for prefix in self._LOCK_ALLOWED_FIELD_PREFIXES)

    def _check_locked_records_for_write(self, vals):
        if not vals or self.env.context.get(self._LOCK_BYPASS_CONTEXT_KEY):
            return
        locked = self.filtered(lambda agreement: agreement.state in self._LOCKED_STATES)
        if not locked:
            return
        blocked_fields = [
            field_name for field_name in vals
            if not self._is_field_allowed_on_lock(field_name)
        ]
        if blocked_fields:
            raise UserError(
                _('Active or expired agreements are read-only for fields: %s. '
                  'Create a renewal to modify contractual terms.') %
                ', '.join(sorted(blocked_fields))
            )

    @api.depends('sign_request_id', 'sign_request_id.state')
    def _compute_is_signed(self):
        """Check if agreement is signed"""
        for record in self:
            record.is_agreement_signed = record.is_signed()

    def is_signed(self):
        """Returns True if agreement has been signed"""
        self.ensure_one()
        if self.sign_request_id and self.sign_request_id.state == 'signed':
            return True
        # Fallback: check for signed document attachment
        signed_docs = self.env['ir.attachment'].search([
            ('res_model', '=', 'rmc.contract.agreement'),
            ('res_id', '=', self.id),
            ('name', 'ilike', 'signed')
        ], limit=1)
        return bool(signed_docs)

    def _compute_web_path(self):
        """Generate dynamic web path for portal access"""
        for record in self:
            if record.id:
                record.dynamic_web_path = f'/contract/agreement/{record.id}'
            else:
                record.dynamic_web_path = False

    @api.depends('diesel_log_ids', 'diesel_log_ids.state',
                 'maintenance_check_ids', 'maintenance_check_ids.state',
                 'attendance_compliance_ids', 'attendance_compliance_ids.state',
                 'contract_type')
    def _compute_pending_items(self):
        """Count pending/unvalidated items based on contract type"""
        for record in self:
            count = 0
            if record.contract_type == 'driver_transport':
                # Diesel is mandatory
                count += record.diesel_log_ids.filtered(
                    lambda x: x.state in ('draft', 'pending_agreement')
                ).mapped('id').__len__()
            elif record.contract_type == 'pump_ops':
                # Maintenance is mandatory
                count += record.maintenance_check_ids.filtered(
                    lambda x: x.state in ('draft', 'pending_agreement')
                ).mapped('id').__len__()
            elif record.contract_type == 'accounts_audit':
                # Attendance is mandatory
                count += record.attendance_compliance_ids.filtered(
                    lambda x: x.state in ('draft', 'pending_agreement')
                ).mapped('id').__len__()
            record.pending_items_count = count

    @api.depends('diesel_log_ids', 'diesel_log_ids.diesel_efficiency',
                 'diesel_log_ids.state')
    def _compute_diesel_kpi(self):
        """Calculate average diesel efficiency from validated logs"""
        for record in self:
            validated_logs = record.diesel_log_ids.filtered(
                lambda x: x.state == 'validated' and x.diesel_efficiency > 0
            )
            if validated_logs:
                record.avg_diesel_efficiency = sum(
                    validated_logs.mapped('diesel_efficiency')
                ) / len(validated_logs)
            else:
                record.avg_diesel_efficiency = 0.0

    @api.depends('maintenance_check_ids', 'maintenance_check_ids.checklist_ok',
                 'maintenance_check_ids.state')
    def _compute_maintenance_kpi(self):
        """Calculate average maintenance compliance from validated checks"""
        for record in self:
            validated_checks = record.maintenance_check_ids.filtered(
                lambda x: x.state == 'validated'
            )
            if validated_checks:
                record.maintenance_compliance = sum(
                    validated_checks.mapped('checklist_ok')
                ) / len(validated_checks)
            else:
                record.maintenance_compliance = 0.0

    @api.depends('attendance_compliance_ids',
                 'attendance_compliance_ids.compliance_percentage',
                 'attendance_compliance_ids.state')
    def _compute_attendance_kpi(self):
        """Calculate average attendance compliance"""
        for record in self:
            validated_attendance = record.attendance_compliance_ids.filtered(
                lambda x: x.state == 'validated'
            )
            if validated_attendance:
                record.attendance_compliance = sum(
                    validated_attendance.mapped('compliance_percentage')
                ) / len(validated_attendance)
            else:
                record.attendance_compliance = 0.0

    @api.depends('avg_diesel_efficiency', 'maintenance_compliance',
                 'attendance_compliance', 'contract_type')
    def _compute_performance(self):
        """
        Compute weighted performance score based on contract type
        Weights from ir.config_parameter
        """
        ICP = self.env['ir.config_parameter'].sudo()
        weight_diesel = float(ICP.get_param('rmc_score.weight_diesel', 0.5))
        weight_maint = float(ICP.get_param('rmc_score.weight_maintenance', 0.3))
        weight_attend = float(ICP.get_param('rmc_score.weight_attendance', 0.2))

        for record in self:
            score = 0.0
            
            if record.contract_type == 'driver_transport':
                # Diesel is primary (normalize to 0-100 assuming 5km/l = 100%)
                diesel_norm = min(record.avg_diesel_efficiency * 20, 100)
                score = diesel_norm * weight_diesel + \
                        record.maintenance_compliance * weight_maint
            elif record.contract_type == 'pump_ops':
                # Maintenance is primary
                score = record.maintenance_compliance * weight_maint + \
                        (record.avg_diesel_efficiency * 20 * weight_diesel if record.avg_diesel_efficiency else 0)
            elif record.contract_type == 'accounts_audit':
                # Attendance is primary
                score = record.attendance_compliance * weight_attend + \
                        record.maintenance_compliance * weight_maint

            record.performance_score = min(score, 100.0)

    @api.depends('performance_score')
    def _compute_stars(self):
        """
        Convert performance score to star rating
        Thresholds from ir.config_parameter
        """
        ICP = self.env['ir.config_parameter'].sudo()
        star_5 = float(ICP.get_param('rmc_score.star_5_threshold', 90))
        star_4 = float(ICP.get_param('rmc_score.star_4_threshold', 75))
        star_3 = float(ICP.get_param('rmc_score.star_3_threshold', 60))
        star_2 = float(ICP.get_param('rmc_score.star_2_threshold', 40))

        for record in self:
            if record.performance_score >= star_5:
                record.stars = '5'
            elif record.performance_score >= star_4:
                record.stars = '4'
            elif record.performance_score >= star_3:
                record.stars = '3'
            elif record.performance_score >= star_2:
                record.stars = '2'
            else:
                record.stars = '1'

    def _compute_payment_hold(self):
        """Override to disable payment hold logic entirely."""
        for record in self:
            record.payment_hold = False
            record.payment_hold_reason = False

    # ---------------------------------------------------------------------
    # Closure helpers
    # ---------------------------------------------------------------------
    def _closure_locked_states(self):
        return {'closure_review', 'settled'}

    def _check_closure_operation_allowed(self, operation_label):
        """Ensure operational records cannot be modified during closure."""
        privileged = (
            self.env.user.has_group('rmc_manpower_contractor.group_rmc_manager') or
            self.env.user.has_group('account.group_account_user') or
            self.env.user.has_group('account.group_account_manager')
        )
        for agreement in self:
            if agreement.state in self._closure_locked_states() and not privileged:
                raise ValidationError(
                    _('Cannot perform %(operation)s while %(agreement)s is in closure review or settled.') % {
                        'operation': operation_label,
                        'agreement': agreement.display_name,
                    }
                )

    def _get_settlement_blockers(self, currency=None, inventory_records=None):
        """Return blocking reasons that should hold settlement."""
        self.ensure_one()
        currency = currency or self.currency_id or self.env.company.currency_id
        precision = currency.rounding or self.env.company.currency_id.rounding or 0.01
        Inventory = self.env['rmc.inventory.handover']
        if inventory_records is None:
            inventory_records = Inventory.search([
                ('agreement_id', '=', self.id),
                ('state', '!=', 'reconciled'),
                ('settlement_included', '=', False),
            ])
        reasons = []
        if not self.is_signed():
            reasons.append(_('Agreement is not fully signed.'))
        breakdown_blockers = self.breakdown_event_ids.filtered(
            lambda ev: ev.responsibility == 'contractor' and (
                ev.state != 'closed' or not ev.settlement_included
            )
        )
        if breakdown_blockers:
            reasons.append(
                _('Breakdown events pending contractor action: %s')
                % self._format_blocker_sample(breakdown_blockers)
            )
        outstanding_inventory = self.inventory_handover_ids.filtered(
            lambda rec: rec.state in ('draft', 'issued')
        )
        if outstanding_inventory:
            reasons.append(
                _('Inventory still issued and pending return: %s')
                % self._format_blocker_sample(outstanding_inventory)
            )
        final_variances = inventory_records.filtered(
            lambda rec: rec.is_final
            and (not rec.acknowledged_by or not rec.ack_signature)
            and (
                not float_is_zero(rec.variance_value or 0.0, precision_rounding=precision)
                or not float_is_zero(rec.damage_cost or 0.0, precision_rounding=precision)
            )
        )
        if final_variances:
            reasons.append(
                _('Final handbacks awaiting acknowledgement: %s')
                % self._format_blocker_sample(final_variances)
            )
        return reasons

    @staticmethod
    def _format_blocker_sample(records, limit=3):
        names = records.mapped('display_name')
        if not names:
            return ''
        if len(names) > limit:
            remainder = len(names) - limit
            return '%s (+%s)' % (', '.join(names[:limit]), remainder)
        return ', '.join(names)

    def _ensure_not_settled(self, operation_label):
        for agreement in self:
            if agreement.state == 'settled':
                raise ValidationError(
                    _('Cannot %(operation)s because agreement %(agreement)s is already settled.') % {
                        'operation': operation_label,
                        'agreement': agreement.display_name,
                    }
                )

    def _default_settlement_period(self):
        """Return (period_start, period_end) tuple for closure wizard."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        period_end = min(filter(None, [self.validity_end, today])) if self.validity_end else today
        period_end = period_end or today
        current_month_start = period_end.replace(day=1)
        previous_start = current_month_start + relativedelta(months=-1)
        previous_end = current_month_start + relativedelta(days=-1)
        return previous_start, previous_end

    def action_start_closure(self):
        for agreement in self:
            if agreement.state not in ('active', 'expired'):
                raise ValidationError(
                    _('Closure can only be initiated from active or expired agreements.')
                )
            agreement.state = 'closure_review'
            agreement.message_post(
                body=_('Closure review started by %s.') % (self.env.user.display_name,),
                subject=_('Closure Review')
            )
        return True

    def action_open_settlement_wizard(self):
        self.ensure_one()
        if self.state != 'closure_review':
            raise ValidationError(_('Settlement wizard can only be opened in closure review state.'))
        period_start, period_end = self._default_settlement_period()
        view = self.env.ref('rmc_manpower_contractor.view_rmc_agreement_settlement_wizard', raise_if_not_found=False)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rmc.agreement.settlement.wizard',
            'view_mode': 'form',
            'views': [(view.id, 'form')] if view else [(False, 'form')],
            'target': 'new',
            'context': {
                'default_agreement_id': self.id,
                'default_period_start': period_start,
                'default_period_end': period_end,
            }
        }

    @api.depends(
        'diesel_log_ids',
        'diesel_log_ids.vehicle_id',
        'maintenance_check_ids',
        'attendance_compliance_ids',
        'breakdown_event_ids',
        'inventory_handover_ids',
        'vendor_bill_ids',
        'vehicle_ids',
        'vehicle_diesel_log_ids',
        'equipment_ids',
        'equipment_request_ids',
        'employee_attendance_ids'
    )
    def _compute_counts(self):
        """Compute smart button counts"""
        DieselLog = self.env['rmc.diesel.log']
        for record in self:
            logs = record.diesel_log_ids
            ext_count = len(record.vehicle_diesel_log_ids)
            record.diesel_log_count = len(logs) + ext_count
            record.maintenance_check_count = len(record.maintenance_check_ids)
            record.attendance_compliance_count = len(record.attendance_compliance_ids)
            record.breakdown_event_count = len(record.breakdown_event_ids)
            inventory_records = record.inventory_handover_ids.filtered(lambda r: not r.date or r.date >= record.activity_start_date)
            record.inventory_handover_count = len(inventory_records)
            record.vendor_bill_count = len(record.vendor_bill_ids)
            record.fleet_vehicle_count = len(record.vehicle_ids)
            record.equipment_count = len(record.equipment_ids)
            record.equipment_request_count = len(record.equipment_request_ids)
            record.employee_attendance_count = len(record.employee_attendance_ids)
            record.billing_prepare_log_count = len(record.billing_prepare_log_ids)

    @api.model
    def _refresh_agreements_for_employees(self, employees):
        """Utility to recompute attendance related data when employees log time."""
        employees = employees.filtered(lambda e: e)
        if not employees:
            return
        agreements = self.search([
            '|',
            ('manpower_matrix_ids.employee_id', 'in', employees.ids),
            ('driver_ids', 'in', employees.ids)
        ])
        if agreements:
            agreements._compute_employee_attendance()
            agreements._compute_counts()

    @api.constrains('validity_start', 'validity_end')
    def _check_validity_dates(self):
        """Ensure validity_end is after validity_start"""
        for record in self:
            if record.validity_start and record.validity_end:
                if record.validity_end < record.validity_start:
                    raise ValidationError(
                        _('Validity end date must be after start date.')
                    )

    @api.constrains('retention_rate')
    def _check_retention_rate(self):
        for record in self:
            if record.retention_rate is not None and record.retention_rate < 0.0:
                raise ValidationError(_('Retention rate cannot be negative.'))

    @api.constrains('contractor_id', 'analytic_account_id', 'company_id', 'state', 'validity_start', 'validity_end')
    def _check_active_overlap(self):
        """Allow only one active agreement per vendor/analytic/company on overlapping dates."""
        for record in self:
            if record.state != 'active':
                continue
            if not record.contractor_id:
                continue
            domain = [
                ('id', '!=', record.id),
                ('contractor_id', '=', record.contractor_id.id),
                ('company_id', '=', record.company_id.id if record.company_id else self.env.company.id),
                ('state', '=', 'active'),
            ]
            if record.analytic_account_id:
                domain.append(('analytic_account_id', '=', record.analytic_account_id.id))
            else:
                domain.append(('analytic_account_id', '=', False))
            others = self.search(domain)
            for other in others:
                if self._dates_overlap(record.validity_start, record.validity_end, other.validity_start, other.validity_end):
                    analytic_name = record.analytic_account_id.display_name if record.analytic_account_id else _('No Analytic')
                    raise ValidationError(
                        _('Vendor %(vendor)s already has an active agreement for %(analytic)s in %(company)s overlapping %(start)s - %(end)s.') % {
                            'vendor': record.contractor_id.display_name,
                            'analytic': analytic_name,
                            'company': (record.company_id or self.env.company).name,
                            'start': record.validity_start,
                            'end': record.validity_end,
                        }
                    )

    @api.constrains('contract_type')
    def _check_contract_type_immutable(self):
        """Contract type cannot be changed after signing"""
        for record in self:
            # Consider the agreement immutable either if it is signed (via sign request
            # or signed document) or if its workflow state has reached 'active'.
            immutable = False
            try:
                immutable = record.is_signed() or record.state == 'active'
            except Exception:
                # defensive: if is_signed fails for any reason, fall back to state check
                immutable = record.state == 'active'

            if immutable and record._origin and record.contract_type != record._origin.contract_type:
                raise ValidationError(
                    _('Cannot change contract type after agreement is signed or activated.')
                )

    def _get_clause_template_commands(self, contract_type):
        ClauseTemplate = self.env['rmc.agreement.clause.template']
        templates = ClauseTemplate.search(
            [('contract_type', '=', contract_type)],
            order='sequence, id'
        )
        commands = []
        for template in templates:
            commands.append((0, 0, {
                'sequence': template.sequence,
                'title': template.title,
                'body_html': template.body_html,
            }))
        return commands

    def _ensure_clause_defaults(self, force=False):
        """
        Ensure default clause set is created for supported contract types.
        Clauses remain editable after creation.
        """
        for agreement in self:
            if agreement.contract_type != 'pump_ops':
                continue
            needs_refresh = force or not agreement.clause_ids
            if not needs_refresh:
                # detect placeholder-only clauses (auto-created but empty)
                placeholder_title = _('New Clause')
                if all((not clause.title or clause.title == placeholder_title) and not clause.body_html for clause in agreement.clause_ids):
                    needs_refresh = True
            if not needs_refresh:
                continue
            if agreement.clause_ids:
                agreement.clause_ids.unlink()
            commands = agreement._get_clause_template_commands(agreement.contract_type)
            if not commands:
                continue
            agreement.write({'clause_ids': commands})

    @api.onchange('contract_type')
    def _onchange_contract_type(self):
        for agreement in self:
            if agreement.contract_type == 'pump_ops' and not agreement.clause_ids:
                agreement.clause_ids = agreement._get_clause_template_commands('pump_ops')

    def action_preview_and_send(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rmc.agreement.send.preview.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_id': self.id,
                'active_model': self._name,
            }
        }

    def action_send_for_sign(self):
        """
        Prepare (or reuse) the sign request and open it in the Sign app so the
        document can be reviewed/edited before emailing the contractor.
        """
        self.ensure_one()

        sign_request, created = self._create_sign_request(require_email=False, allow_existing=True)
        if not created and sign_request.state == 'signed':
            raise UserError(
                _('The existing sign request is already completed. Create a new agreement to request another signature.')
            )

        if created:
            self.message_post(
                body=_('Sign request prepared in Sign. Review and send to the contractor from the Sign app.'),
                subject=_('Sign Request Prepared')
            )

        return self._action_open_sign_request(sign_request)

    def action_view_sign_request(self):
        self.ensure_one()
        if not self.sign_request_id:
            raise UserError(_('No sign request is linked to this agreement yet.'))
        return self._action_open_sign_request(self.sign_request_id)

    def action_push_to_sign_app(self):
        """
        Create (or reuse) the sign request but do not send emails.
        Returns the form view in the Sign app for manual handling.
        """
        self.ensure_one()
        sign_request, created = self._create_sign_request(require_email=False, allow_existing=True)
        if created:
            self.message_post(
                body=_('Sign request prepared in Sign. Review and send to the contractor from the Sign app.'),
                subject=_('Sign Request Prepared')
            )
        return self._action_open_sign_request(sign_request)

    def _action_open_sign_request(self, sign_request):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sign.request',
            'res_id': sign_request.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _create_sign_request(self, require_email=True, allow_existing=False):
        """
        Shared helper that prepares a sign request from the current agreement.
        Returns a tuple (sign_request, created_bool).
        """
        self.ensure_one()

        self._ensure_sign_template()

        if not self.sign_template_id:
            raise UserError(
                _('Please configure a Sign Template before creating a signature request.')
            )

        if self.sign_request_id:
            if allow_existing:
                return self.sign_request_id, False
            raise UserError(
                _('A sign request already exists for this agreement.')
            )

        if require_email and not self.contractor_id.email:
            raise UserError(
                _('Please set an email address on the contractor before sending for signature.')
            )

        seen = set()
        template_roles = []
        for item in self.sign_template_id.sign_item_ids:
            role = item.responsible_id
            if role and role.id not in seen:
                seen.add(role.id)
                template_roles.append(role)
        if not template_roles:
            raise UserError(
                _('The selected Sign Template does not define any signer roles. Add at least one signer block.')
            )

        signer_map = {signer.role_id.id: signer for signer in self.signer_ids}

        request_items = []
        for idx, role in enumerate(template_roles, start=1):
            partner = False
            sequence = idx

            signer = signer_map.get(role.id)
            if signer:
                partner = signer.partner_id
                sequence = signer.sequence or idx
            if not partner:
                partner = self._default_partner_for_role(role)

            if not partner:
                raise UserError(
                    _('Unable to determine signer partner for role %s. Please adjust the Sign Template or agreement data.') % role.name
                )

            if require_email and not partner.email:
                raise UserError(
                    _('Signer %s must have an email address to receive the signature request.') % partner.display_name
                )

            request_items.append((0, 0, {
                'partner_id': partner.id,
                'role_id': role.id,
                'mail_sent_order': sequence,
            }))

        sign_request = self.env['sign.request'].create({
            'template_id': self.sign_template_id.id,
            'reference': self.name,
            'subject': f'Contract Agreement - {self.name}',
            'request_item_ids': request_items,
        })

        self.sign_request_id = sign_request.id
        self.state = 'sign_pending'

        return sign_request, True

    def action_activate_on_sign(self):
        """
        Called when signature is completed
        - Activate agreement
        - Reconcile pending entries
        - Clear payment hold if conditions met
        """
        self.ensure_one()

        if not self.is_signed():
            raise UserError(_('Agreement must be signed before activation.'))

        self.state = 'active'
        previous = self.previous_agreement_id
        
        # Set validity if not already set
        if not self.validity_start:
            self.validity_start = fields.Date.today()
        if not self.validity_end:
            self.validity_end = fields.Date.today() + timedelta(days=365)

        # Reconcile pending entries - validate those that meet thresholds
        self._reconcile_pending_entries()

        # Recompute performance and payment hold
        self._compute_performance()
        self._compute_payment_hold()

        if previous:
            prev_ctx = previous.with_context({self._LOCK_BYPASS_CONTEXT_KEY: True})
            prev_vals = {}
            if previous.state != 'expired':
                prev_vals['state'] = 'expired'
            if previous.next_agreement_id != self:
                prev_vals['next_agreement_id'] = self.id
            if prev_vals:
                prev_ctx.write(prev_vals)
            prev_ctx.message_post(
                body=_('Agreement superseded by %s (Rev %s).') % (
                    self.display_name or self.name,
                    self.revision_no or 1,
                ),
                subject=_('Agreement Superseded')
            )

        # Notify stakeholders
        self.message_post(
            body=_('Agreement activated. Payment hold status: %s') % 
                 ('ON HOLD' if self.payment_hold else 'CLEARED'),
            subject=_('Agreement Activated')
        )
        if previous:
            self.message_post(
                body=_('Revision %(new_rev)s activated over %(prev_name)s (Rev %(prev_rev)s).') % {
                    'new_rev': self.revision_no or 1,
                    'prev_name': previous.display_name or previous.name,
                    'prev_rev': previous.revision_no or 1,
                },
                subject=_('Renewal Linked')
            )

        # Create activity for Accounts if payment cleared
        if not self.payment_hold:
            account_group = self.env.ref('account.group_account_invoice', raise_if_not_found=False)
            account_users = getattr(account_group, 'users', self.env['res.users']) if account_group else self.env['res.users']
            user_id = account_users[:1].id if account_users else self.env.user.id
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=user_id,
                summary=_('Agreement ready for billing'),
                note=_('Agreement %s is active and payment hold is cleared.') % self.name
            )

        return True

    def _reconcile_pending_entries(self):
        """
        Validate pending entries that meet minimum thresholds
        """
        self.ensure_one()

        # Diesel logs
        pending_diesel = self.diesel_log_ids.filtered(
            lambda x: x.state == 'pending_agreement'
        )
        for log in pending_diesel:
            if log.diesel_efficiency > 0:  # Simple threshold
                log.state = 'validated'
                log.message_post(
                    body=_('Auto-validated on agreement activation')
                )

        # Maintenance checks
        pending_maint = self.maintenance_check_ids.filtered(
            lambda x: x.state == 'pending_agreement'
        )
        for check in pending_maint:
            if check.checklist_ok >= 50:  # 50% threshold
                check.state = 'validated'
                check.message_post(
                    body=_('Auto-validated on agreement activation')
                )

        # Attendance
        pending_attend = self.attendance_compliance_ids.filtered(
            lambda x: x.state == 'pending_agreement'
        )
        for attend in pending_attend:
            if attend.compliance_percentage >= 70:  # 70% threshold
                attend.state = 'validated'
                attend.message_post(
                    body=_('Auto-validated on agreement activation')
                )

    def compute_performance(self):
        """
        Public method to manually trigger performance computation
        Called by monthly cron
        """
        self._compute_diesel_kpi()
        self._compute_maintenance_kpi()
        self._compute_attendance_kpi()
        self._compute_performance()
        self._compute_stars()
        
        _logger.info(
            f'Performance computed for {self.name}: '
            f'Score={self.performance_score:.2f}, Stars={self.stars}'
        )

    def _collect_chain_records(self, direction):
        """Return previous or next chain recordset relative to self."""
        self.ensure_one()
        Agreement = self.env['rmc.contract.agreement']
        records = Agreement.browse()
        current = self.previous_agreement_id if direction == 'previous' else self.next_agreement_id
        seen = set()
        while current and current.id not in seen:
            records |= current
            seen.add(current.id)
            current = current.previous_agreement_id if direction == 'previous' else current.next_agreement_id
        return records.sorted(lambda agreement: agreement.revision_no or agreement.id)

    def _action_open_chain(self, records, title):
        action = self.env.ref('rmc_manpower_contractor.action_rmc_agreement', raise_if_not_found=False)
        if not action:
            raise UserError(_('Agreement action is missing.'))
        data = action.read()[0]
        data['name'] = title
        data['domain'] = [('id', 'in', records.ids or [])]
        chain_tree = self.env.ref('rmc_manpower_contractor.view_rmc_agreement_chain_tree', raise_if_not_found=False)
        if chain_tree:
            data['views'] = [(chain_tree.id, 'list'), (False, 'form')]
        return data

    def action_open_prev_chain(self):
        self.ensure_one()
        records = self._collect_chain_records('previous')
        title = _('Previous Versions of %s') % (self.display_name or self.name)
        return self._action_open_chain(records, title)

    def action_open_next_chain(self):
        self.ensure_one()
        records = self._collect_chain_records('next')
        title = _('Next Versions of %s') % (self.display_name or self.name)
        return self._action_open_chain(records, title)

    def action_open_renewal_wizard(self):
        self.ensure_one()
        self._ensure_not_settled(_('open the renewal wizard'))
        action = self.env.ref('rmc_manpower_contractor.action_rmc_agreement_renewal_wizard', raise_if_not_found=False)
        if not action:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Renewal Wizard Unavailable'),
                    'message': _('The renewal wizard will be available in a future update.'),
                    'sticky': False,
                }
            }
        data = action.read()[0]
        ctx = data.get('context') or {}
        if isinstance(ctx, str):
            ctx = safe_eval(ctx, {
                'active_id': self.id,
                'active_model': self._name,
                'uid': self._uid,
                'user': self.env.user,
            })
        context = dict(ctx)
        context.update({
            'default_source_agreement_id': self.id,
            'active_id': self.id,
            'active_ids': self.ids,
            'active_model': self._name,
        })
        data['context'] = context
        return data

    # Smart Button Actions
    def action_view_diesel_logs(self):
        """Open diesel logs for this agreement"""
        self.ensure_one()
        action = self.env.ref('diesel_log.action_diesel_log_list', raise_if_not_found=False)
        if not action:
            action = self.env.ref('rmc_manpower_contractor.action_diesel_log', raise_if_not_found=False)
        if not action:
            raise UserError(_('Diesel log action is missing.'))
        action = action.read()[0]
        start_dt = self._get_activity_start_datetime()
        res_model = action.get('res_model')
        domain = []
        if res_model == 'diesel.log':
            domain = [('log_type', '=', 'diesel')]
            if 'rmc_agreement_id' in self.env['diesel.log']._fields:
                domain.append(('rmc_agreement_id', '=', self.id))
            elif self.vehicle_ids:
                domain.append(('vehicle_id', 'in', self.vehicle_ids.ids))
            elif self.driver_ids:
                domain.append(('driver_id', 'in', self.driver_ids.ids))
            if 'date' in self.env['diesel.log']._fields:
                domain += ['|', ('date', '=', False), ('date', '>=', start_dt.date())]
        else:
            domain = [('agreement_id', '=', self.id)]
            if 'date' in self.env['rmc.diesel.log']._fields:
                domain += ['|', ('date', '=', False), ('date', '>=', start_dt.date())]
        action['domain'] = domain
        ctx = action.get('context') or {}
        if isinstance(ctx, str):
            ctx = safe_eval(ctx, {'active_id': self.id, 'active_model': self._name})
        context = dict(ctx)
        if res_model == 'diesel.log':
            context.setdefault('default_log_type', 'diesel')
            if 'rmc_agreement_id' in self.env['diesel.log']._fields:
                context.setdefault('default_rmc_agreement_id', self.id)
            if self.vehicle_ids:
                context.setdefault('default_vehicle_id', self.vehicle_ids.ids[0])
            if self.driver_ids:
                context.setdefault('default_driver_id', self.driver_ids.ids[0])
        else:
            context.setdefault('default_agreement_id', self.id)
            if self.vehicle_ids:
                context.setdefault('default_vehicle_id', self.vehicle_ids.ids[0])
            if self.driver_ids:
                context.setdefault('default_driver_id', self.driver_ids.ids[0])
        action['context'] = context
        return action

    def action_view_equipment(self):
        """Open equipment assigned to agreement employees"""
        self.ensure_one()
        action = self.env.ref('maintenance.hr_equipment_action', raise_if_not_found=False)
        if not action:
            raise UserError(_('Maintenance equipment action is missing.'))
        action = action.read()[0]
        domain = [('id', 'in', self.equipment_ids.ids)] if self.equipment_ids else [('id', '=', False)]
        action['domain'] = domain
        ctx = action.get('context') or {}
        if isinstance(ctx, str):
            ctx = safe_eval(ctx, {'active_id': self.id, 'active_model': self._name})
        context = dict(ctx)
        if self.driver_ids:
            context.setdefault('search_default_employee_id', self.driver_ids.ids)
        action['context'] = context
        return action

    def action_view_equipment_requests(self):
        """Open maintenance requests for assigned equipment"""
        self.ensure_one()
        action = self.env.ref('maintenance.hr_equipment_request_action', raise_if_not_found=False)
        if not action:
            raise UserError(_('Maintenance request action is missing.'))
        action = action.read()[0]
        start_dt = self._get_activity_start_datetime()
        domain = [('id', 'in', self.equipment_request_ids.ids)] if self.equipment_request_ids else [('id', '=', False)]
        if 'request_date' in self.env['maintenance.request']._fields:
            domain += ['|', ('request_date', '=', False), ('request_date', '>=', start_dt.date())]
        action['domain'] = domain
        ctx = action.get('context') or {}
        if isinstance(ctx, str):
            ctx = safe_eval(ctx, {
                'active_id': self.id,
                'active_model': self._name,
                'uid': self._uid,
                'user': self.env.user,
            })
        context = dict(ctx)
        if self.equipment_ids:
            context.setdefault('search_default_equipment_id', self.equipment_ids.ids)
        context.setdefault('default_user_id', self._uid)
        action['context'] = context
        return action

    def action_new_inventory_handover(self):
        self.ensure_one()
        form_view = self.env.ref('rmc_manpower_contractor.view_inventory_handover_form', raise_if_not_found=False)
        ctx = {
            'default_agreement_id': self.id,
            'default_contractor_id': self.contractor_id.id,
            'default_employee_id': self.driver_ids[:1].id if self.driver_ids else False,
            'default_operation_type': 'contract_issue_product',
        }
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Inventory Request'),
            'res_model': 'rmc.inventory.handover',
            'view_mode': 'form',
            'view_id': form_view.id if form_view else False,
            'target': 'new',
            'context': ctx,
        }

    def action_view_employee_attendance(self):
        """Open HR attendance entries for agreement employees"""
        self.ensure_one()
        action = self.env.ref('hr_attendance.hr_attendance_action', raise_if_not_found=False)
        if not action:
            raise UserError(_('Attendance action is missing.'))
        action = action.read()[0]
        employees = (self.driver_ids | self.manpower_matrix_ids.mapped('employee_id')).filtered(lambda e: e)
        start_dt = self._get_activity_start_datetime()
        if employees:
            domain = ['&', ('employee_id', 'in', employees.ids), '|', ('check_in', '>=', start_dt), ('check_out', '>=', start_dt)]
        else:
            domain = [('employee_id', '=', False)]
        action['domain'] = domain
        ctx = action.get('context') or {}
        if isinstance(ctx, str):
            ctx = safe_eval(ctx, {
                'active_id': self.id,
                'active_model': self._name,
                'uid': self._uid,
                'user': self.env.user,
            })
        context = dict(ctx)
        if employees:
            context.setdefault('search_default_employee_id', employees.ids)
        action['context'] = context
        return action

    def _compute_activity_start_date(self):
        for record in self:
            record.activity_start_date = record._get_activity_start_datetime().date()

    def action_view_fleet_vehicles(self):
        """Open fleet vehicles associated with this agreement"""
        self.ensure_one()
        action = self.env.ref('fleet.fleet_vehicle_action', raise_if_not_found=False)
        if not action:
            raise UserError(_('Fleet module action not found.'))
        action = action.read()[0]
        domain = [('id', 'in', self.vehicle_ids.ids)] if self.vehicle_ids else [('id', '=', False)]
        action['domain'] = domain
        ctx = action.get('context') or {}
        if isinstance(ctx, str):
            ctx = safe_eval(ctx, {'active_id': self.id, 'active_model': self._name})
        context = dict(ctx)
        context.setdefault('default_agreement_id', self.id)
        if self.driver_ids:
            context.setdefault('default_driver_id', self.driver_ids[:1].id)
        action['context'] = context
        return action

    def action_view_maintenance_checks(self):
        """Open maintenance checks for this agreement"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Maintenance Checks'),
            'res_model': 'rmc.maintenance.check',
            'domain': [('agreement_id', '=', self.id)],
            'view_mode': 'list,form',
            'context': {'default_agreement_id': self.id}
        }

    def action_view_attendance(self):
        """Open attendance records for this agreement"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Attendance Compliance'),
            'res_model': 'rmc.attendance.compliance',
            'domain': [('agreement_id', '=', self.id)],
            'view_mode': 'list,form',
            'context': {'default_agreement_id': self.id}
        }

    def action_view_breakdowns(self):
        """Open breakdown events for this agreement"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Breakdown Events'),
            'res_model': 'rmc.breakdown.event',
            'domain': [('agreement_id', '=', self.id)],
            'view_mode': 'list,form',
            'context': {'default_agreement_id': self.id}
        }

    def action_view_inventory(self):
        """Open inventory handovers for this agreement"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Inventory Handovers'),
            'res_model': 'rmc.inventory.handover',
            'domain': [('agreement_id', '=', self.id)],
            'view_mode': 'list,form',
            'context': {'default_agreement_id': self.id}
        }

    def action_view_vendor_bills(self):
        """Open vendor bills for this agreement"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Vendor Bills'),
            'res_model': 'account.move',
            'domain': [('agreement_id', '=', self.id), ('move_type', '=', 'in_invoice')],
            'view_mode': 'list,form',
            'context': {
                'default_agreement_id': self.id,
                'default_move_type': 'in_invoice',
                'default_partner_id': self.contractor_id.id
            }
        }

    def action_view_billing_prepare_logs(self):
        """Open billing snapshot logs for this agreement."""
        self.ensure_one()
        log_model = self.env['rmc.billing.prepare.log']
        log_model.ensure_current_month_log(self.id)
        action = self.env.ref('rmc_manpower_contractor.action_rmc_billing_prepare_log', raise_if_not_found=False)
        if not action:
            raise UserError(_('Billing log action is missing.'))
        result = action.read()[0]
        result['domain'] = [('agreement_id', '=', self.id)]
        context = result.get('context') or {}
        if isinstance(context, str):
            try:
                context = safe_eval(context)
            except Exception:
                context = {}
        result['context'] = dict(context, default_agreement_id=self.id, active_agreement_id=self.id)
        return result

    def action_view_monthly_report(self):
        self.ensure_one()
        log = self.env['rmc.billing.prepare.log'].ensure_current_month_log(self.id)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rmc.billing.prepare.log',
            'view_mode': 'form',
            'res_id': log.id,
            'target': 'current',
        }

    def action_print_performance_summary(self):
        """Print the PDF performance summary for this agreement."""
        self.ensure_one()
        report = self.env.ref(
            'rmc_manpower_contractor.action_report_agreement_performance_summary',
            raise_if_not_found=False,
        )
        if not report:
            raise UserError(_('Performance summary report action is missing.'))
        return report.report_action(self)

    def action_prepare_monthly_bill(self):
        """Open wizard to prepare monthly vendor bill"""
        self.ensure_one()
        self._ensure_not_settled(_('prepare monthly bills'))
        wizard_model = self.env['rmc.billing.prepare.wizard']
        existing_wizard = wizard_model.search([
            ('agreement_id', '=', self.id),
            ('state', 'in', ['prepare', 'review'])
        ], limit=1, order='create_date desc')

        action = {
            'type': 'ir.actions.act_window',
            'name': _('Prepare Monthly Bill'),
            'res_model': 'rmc.billing.prepare.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_agreement_id': self.id},
        }

        if existing_wizard:
            action['res_id'] = existing_wizard.id
        return action
