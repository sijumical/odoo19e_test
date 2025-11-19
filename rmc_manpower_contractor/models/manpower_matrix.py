# -*- coding: utf-8 -*-
"""
Manpower Matrix - Designation-wise wage structure
"""

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class RmcManpowerMatrix(models.Model):
    _name = 'rmc.manpower.matrix'
    _description = 'RMC Manpower Matrix'
    _order = 'designation'

    agreement_id = fields.Many2one(
        'rmc.contract.agreement',
        string='Agreement',
        required=True,
        ondelete='cascade'
    )
    designation = fields.Char(
        string='Designation',
        required=True,
        help='Legacy designation label (auto-filled from Job Position when available).'
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='Assigned Employee',
        help='Single employee linked to this designation',
        ondelete='restrict'
    )
    vehicle_id = fields.Many2one(
        'fleet.vehicle',
        string='Assigned Vehicle/Equipment',
        help='Vehicle or equipment linked to this employee/designation',
        ondelete='restrict'
    )
    headcount = fields.Integer(
        string='Headcount',
        required=True,
        default=1,
        help='Defaults to 1 when an employee is assigned.'
    )
    job_position_name = fields.Char(
        string='Job Position',
        compute='_compute_job_position',
        store=True
    )
    shift = fields.Selection([
        ('day', 'Day Shift'),
        ('night', 'Night Shift'),
        ('rotational', 'Rotational'),
        ('general', 'General (8 hrs)')
    ], string='Shift', default='general')
    base_rate = fields.Monetary(
        string='Base Rate (per person/month)',
        currency_field='currency_id',
        required=True
    )
    remark = fields.Selection([
        ('part_a', 'Part-A (Fixed)'),
        ('part_b', 'Part-B (Variable - MGQ linked)')
    ], string='Payment Component', default='part_a', required=True)
    attendance_present_days = fields.Float(
        string='Present Days',
        digits=(6, 2),
        default=0.0,
        help='Actual working days captured for this designation during the billing period.'
    )
    attendance_total_days = fields.Float(
        string='Scheduled Days',
        digits=(6, 2),
        default=0.0,
        help='Total workable days considered for attendance proration.'
    )
    attendance_ratio = fields.Float(
        string='Attendance Ratio',
        digits=(6, 4),
        compute='_compute_attendance_proration',
        store=True,
        help='Auto-computed present/total ratio (capped between 0% and 100%).'
    )
    attendance_prorated_amount = fields.Monetary(
        string='Prorated Part-A Amount',
        currency_field='currency_id',
        compute='_compute_attendance_proration',
        store=True,
        help='Part-A payout after applying the attendance ratio.'
    )
    attendance_deduction_amount = fields.Monetary(
        string='Attendance Deduction',
        currency_field='currency_id',
        compute='_compute_attendance_proration',
        store=True,
        help='Difference between the base Part-A amount and the attendance-prorated amount.'
    )
    
    currency_id = fields.Many2one(
        related='agreement_id.currency_id',
        string='Currency',
        store=True
    )
    total_amount = fields.Monetary(
        string='Total Amount',
        compute='_compute_total',
        store=True,
        currency_field='currency_id'
    )

    @api.depends('headcount', 'base_rate')
    def _compute_total(self):
        """Calculate total = headcount × base_rate"""
        for record in self:
            record.total_amount = record.headcount * record.base_rate

    @api.depends('employee_id', 'employee_id.job_id', 'designation')
    def _compute_job_position(self):
        for record in self:
            job_name = record.employee_id.job_id.name if record.employee_id and record.employee_id.job_id else False
            record.job_position_name = job_name or record.designation or False
            # keep legacy designation in sync when employee changes and designation empty
            if record.employee_id and not record.designation and job_name:
                record.designation = job_name

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        for record in self:
            if record.employee_id:
                record.headcount = 1
        return {}

    @api.constrains('headcount', 'base_rate')
    def _check_positive(self):
        """Ensure positive values"""
        for record in self:
            if record.headcount < 1:
                raise ValidationError(_('Headcount must be at least 1.'))
            if record.base_rate < 0:
                raise ValidationError(_('Base rate cannot be negative.'))
            if record.employee_id and record.headcount != 1:
                raise ValidationError(
                    _('Headcount must be 1 when an employee is assigned.')
                )

    @api.constrains('employee_id', 'agreement_id')
    def _check_unique_employee(self):
        for record in self.filtered('employee_id'):
            duplicates = record.agreement_id.manpower_matrix_ids.filtered(
                lambda r: r.id != record.id and r.employee_id == record.employee_id
            )
            if duplicates:
                raise ValidationError(
                    _('Employee %s is already assigned on manpower matrix.')
                    % record.employee_id.name
                )

    @api.constrains('vehicle_id', 'agreement_id')
    def _check_vehicle_consistency(self):
        for record in self.filtered('vehicle_id'):
            duplicates = record.agreement_id.manpower_matrix_ids.filtered(
                lambda r: r.id != record.id and r.vehicle_id == record.vehicle_id
            )
            if duplicates:
                raise ValidationError(
                    _('Vehicle %s is already assigned on another manpower line.') %
                    record.vehicle_id.display_name
                )

    def _update_parent_agreements(self):
        agreements = self.mapped('agreement_id')
        if agreements:
            agreements._update_manpower_totals_from_matrix()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._update_parent_agreements()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._update_parent_agreements()
        return res

    def unlink(self):
        agreements = self.mapped('agreement_id')
        res = super().unlink()
        if agreements:
            agreements._update_manpower_totals_from_matrix()
        return res

    @api.depends(
        'attendance_present_days',
        'attendance_total_days',
        'total_amount',
        'remark',
        'currency_id'
    )
    def _compute_attendance_proration(self):
        """Compute attendance ratio and related monetary adjustments."""
        for record in self:
            ratio = 0.0
            prorated = 0.0
            deduction = 0.0
            if record.remark == 'part_a':
                total_days = record.attendance_total_days or 0.0
                present_days = record.attendance_present_days or 0.0
                if total_days > 0:
                    ratio = present_days / total_days if total_days else 0.0
                else:
                    # When total days not provided, keep full Part-A amount
                    ratio = 1.0
                ratio = max(0.0, min(ratio, 1.0))
                base_amount = record.total_amount or 0.0
                prorated = base_amount * ratio
                currency = record.currency_id
                if currency:
                    prorated = currency.round(prorated)
                    deduction = currency.round(base_amount - prorated)
                else:
                    deduction = base_amount - prorated
            record.attendance_ratio = ratio
            record.attendance_prorated_amount = prorated
            record.attendance_deduction_amount = deduction

    _sql_constraints = [
        (
            'rmc_manpower_headcount_positive',
            'CHECK(headcount > 0)',
            'Headcount must be positive.'
        ),
        (
            'rmc_manpower_base_rate_positive',
            'CHECK(base_rate >= 0)',
            'Base rate must be non-negative.'
        ),
    ]
