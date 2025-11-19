# -*- coding: utf-8 -*-
"""
Diesel Log - Track fuel consumption and efficiency
"""
import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

class RmcDieselLog(models.Model):
    _name = 'rmc.diesel.log'
    _description = 'RMC Diesel Log'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _DIESEL_SYNC_PARAM = 'rmc.diesel.log.last_sync'

    name = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New')
    )
    agreement_id = fields.Many2one(
        'rmc.contract.agreement',
        string='Agreement',
        required=True,
        ondelete='restrict',
        tracking=True
    )
    contractor_id = fields.Many2one(
        related='agreement_id.contractor_id',
        string='Contractor',
        store=True
    )
    date = fields.Date(
        string='Date',
        required=True,
        default=fields.Date.context_today,
        tracking=True
    )
    vehicle_id = fields.Many2one(
        'fleet.vehicle',
        string='Vehicle',
        help='Optional: link to fleet vehicle'
    )
    driver_id = fields.Many2one(
        'hr.employee',
        string='Driver',
        help='Driver associated with this diesel log entry'
    )
    
    # Diesel Measurements
    opening_ltr = fields.Float(
        string='Opening Stock (Liters)',
        digits='Product Unit of Measure',
        required=True
    )
    issued_ltr = fields.Float(
        string='Issued (Liters)',
        digits='Product Unit of Measure',
        required=True
    )
    closing_ltr = fields.Float(
        string='Closing Stock (Liters)',
        digits='Product Unit of Measure',
        required=True
    )
    
    # Work Done
    work_done_m3 = fields.Float(
        string='Work Done (m³)',
        digits='Product Unit of Measure',
        help='Concrete delivered in cubic meters'
    )
    work_done_km = fields.Float(
        string='Distance Traveled (km)',
        digits='Product Unit of Measure',
        help='Kilometers traveled'
    )
    
    # Efficiency
    diesel_efficiency = fields.Float(
        string='Diesel Efficiency',
        compute='_compute_efficiency',
        store=True,
        digits=(5, 2),
        help='m³/liter or km/liter depending on work type'
    )
    efficiency_unit = fields.Char(
        string='Efficiency Unit',
        compute='_compute_efficiency',
        store=True
    )
    
    # State
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending_agreement', 'Pending Agreement Signature'),
        ('validated', 'Validated')
    ], string='Status', default='draft', required=True, tracking=True)
    
    # Additional
    notes = fields.Text(string='Notes')
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Generate sequence and check agreement signature"""
        for vals in vals_list:
            agreement_id = vals.get('agreement_id')
            if agreement_id:
                self.env['rmc.contract.agreement'].browse(agreement_id)._check_closure_operation_allowed(_('create diesel logs'))
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'rmc.diesel.log'
                ) or _('New')

        records = super(RmcDieselLog, self).create(vals_list)
        for record in records:
            record._check_agreement_signature()
            record._validate_agreement_assignments()
            record._default_assignments_from_agreement()
        return records

    def write(self, vals):
        """Check agreement signature on write"""
        if self:
            agreements = self.mapped('agreement_id')
            if agreements:
                agreements._check_closure_operation_allowed(_('update diesel logs'))
        res = super(RmcDieselLog, self).write(vals)
        if 'state' not in vals:  # Don't check when updating state
            self._check_agreement_signature()
        if {'agreement_id', 'vehicle_id', 'driver_id'} & set(vals.keys()):
            self._validate_agreement_assignments()
        if 'agreement_id' in vals and not vals.get('vehicle_id'):
            self._default_assignments_from_agreement()
        return res

    @api.onchange('agreement_id')
    def _onchange_agreement_id(self):
        for record in self:
            if not record.agreement_id:
                record.vehicle_id = False
                record.driver_id = False
                continue
            record._default_assignments_from_agreement()

    def _check_agreement_signature(self):
        """
        If agreement is not signed, set state to pending_agreement
        and create activity
        """
        for record in self:
            if not record.agreement_id.is_signed():
                record.state = 'pending_agreement'
                record.message_post(
                    body=_('Diesel log is pending because agreement is not signed yet.'),
                    subject=_('Pending Agreement Signature')
                )
                # Create activity for agreement owner
                record.agreement_id.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_('Sign agreement to validate diesel logs'),
                    note=_('Diesel log %s is waiting for agreement signature.') % record.name
                )

    def _validate_agreement_assignments(self):
        """
        Ensure selected vehicle/driver belong to the agreement configuration
        """
        for record in self:
            if record.agreement_id:
                if record.vehicle_id and record.vehicle_id not in record.agreement_id.vehicle_ids:
                    raise ValidationError(
                        _('Vehicle %s is not assigned to agreement %s.') %
                        (record.vehicle_id.display_name, record.agreement_id.name)
                    )
                if record.driver_id and record.driver_id not in record.agreement_id.driver_ids:
                    raise ValidationError(
                        _('Driver %s is not assigned to agreement %s.') %
                        (record.driver_id.name, record.agreement_id.name)
                    )

    def _default_assignments_from_agreement(self):
        for record in self:
            if not record.agreement_id:
                continue
            if not record.vehicle_id and record.agreement_id.vehicle_ids:
                record.vehicle_id = record.agreement_id.vehicle_ids[:1]
            if not record.driver_id and record.agreement_id.driver_ids:
                record.driver_id = record.agreement_id.driver_ids[:1]

    @api.depends('issued_ltr', 'work_done_m3', 'work_done_km')
    def _compute_efficiency(self):
        """Calculate diesel efficiency based on work done"""
        for record in self:
            if record.issued_ltr > 0:
                if record.work_done_m3 > 0:
                    record.diesel_efficiency = record.work_done_m3 / record.issued_ltr
                    record.efficiency_unit = 'm³/liter'
                elif record.work_done_km > 0:
                    record.diesel_efficiency = record.work_done_km / record.issued_ltr
                    record.efficiency_unit = 'km/liter'
                else:
                    record.diesel_efficiency = 0.0
                    record.efficiency_unit = ''
            else:
                record.diesel_efficiency = 0.0
                record.efficiency_unit = ''

    @api.constrains('opening_ltr', 'issued_ltr', 'closing_ltr')
    def _check_positive_liters(self):
        """Ensure non-negative liter values"""
        for record in self:
            if record.opening_ltr < 0 or record.issued_ltr < 0 or record.closing_ltr < 0:
                raise ValidationError(_('Liter values cannot be negative.'))

    @api.constrains('work_done_m3', 'work_done_km')
    def _check_positive_work(self):
        """Ensure non-negative work values"""
        for record in self:
            if record.work_done_m3 < 0 or record.work_done_km < 0:
                raise ValidationError(_('Work done values cannot be negative.'))

    def action_validate(self):
        """Validate diesel log"""
        for record in self:
            if not record.agreement_id.is_signed():
                raise ValidationError(
                    _('Cannot validate: Agreement %s is not signed yet.') % 
                    record.agreement_id.name
                )
            record.state = 'validated'
            record.message_post(body=_('Diesel log validated'))

    def action_reset_to_draft(self):
        """Reset to draft"""
        self.write({'state': 'draft'})

    # ------------------------------------------------------------------
    # Auto-sync helpers
    # ------------------------------------------------------------------

    @api.model
    def _resolve_agreement_for_vehicle(self, vehicle):
        if not vehicle:
            return self.env['rmc.contract.agreement']
        Agreement = self.env['rmc.contract.agreement'].sudo()
        domain = [
            '|',
            ('vehicle_ids', 'in', vehicle.ids),
            ('manpower_matrix_ids.vehicle_id', 'in', vehicle.ids)
        ]
        return Agreement.search(domain, limit=1)

    @api.model
    def _resolve_driver_employee(self, vehicle, agreement):
        Employee = self.env['hr.employee'].sudo()
        if not vehicle or not vehicle.driver_id:
            return Employee
        employee = Employee.search([
            ('address_home_id', '=', vehicle.driver_id.id)
        ], limit=1)
        if employee and agreement.driver_ids and employee not in agreement.driver_ids:
            return Employee.browse()
        return employee

    @api.model
    def _extract_work_payload(self, log):
        work_m3 = 0.0
        work_km = 0.0
        production_name = getattr(log, 'production_name', False)
        if production_name:
            try:
                value = float(production_name)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                work_m3 = value
        odometer_diff = getattr(log, 'odometer_difference', False)
        if odometer_diff:
            work_km = odometer_diff
        elif getattr(log, 'current_odometer', False) and getattr(log, 'last_odometer', False):
            work_km = (log.current_odometer or 0.0) - (log.last_odometer or 0.0)
        return work_m3, work_km

    @api.model
    def _prepare_sync_vals(self, log, agreement):
        work_m3, work_km = self._extract_work_payload(log)
        date_value = fields.Date.to_date(log.date) if log.date else fields.Date.context_today(self)
        vals = {
            'agreement_id': agreement.id,
            'date': date_value,
            'vehicle_id': log.vehicle_id.id,
            'opening_ltr': log.opening_diesel or 0.0,
            'issued_ltr': (log.issue_diesel or log.quantity) or 0.0,
            'closing_ltr': log.closing_diesel or 0.0,
            'work_done_m3': work_m3,
            'work_done_km': work_km,
        }
        driver = self._resolve_driver_employee(log.vehicle_id, agreement)
        if driver:
            vals['driver_id'] = driver.id
        # Allow zero-quantity logs to sync as well so Operations view mirrors
        # everything captured in the fleet module (some audit logs have Qty = 0).
        return vals

    def _upsert_from_diesel_log(self, log, agreement):
        vals = self._prepare_sync_vals(log, agreement)
        if not vals:
            return False
        sync_name = f"SYNC-DL-{log.id}"
        record = self.search([('name', '=', sync_name)], limit=1)
        if record:
            record.write(vals)
            return record
        vals['name'] = sync_name
        vals.setdefault('notes', _('Auto-synced from diesel log %s') % (log.name or log.id))
        return self.create(vals)

    @api.model
    def cron_sync_from_fleet_issues(self):
        """Mirror approved/done diesel.log entries into RMC diesel log."""
        self = self.sudo()
        DieselLog = self.env['diesel.log'].sudo()
        ICP = self.env['ir.config_parameter'].sudo()
        last_sync_str = ICP.get_param(self._DIESEL_SYNC_PARAM)
        last_sync = fields.Datetime.from_string(last_sync_str) if last_sync_str else None
        domain = [
            ('log_type', '=', 'diesel'),
            ('state', 'in', ('approved', 'done')),
        ]
        if last_sync:
            domain.append(('write_date', '>', fields.Datetime.to_string(last_sync - timedelta(hours=1))))
        else:
            baseline = fields.Datetime.from_string(fields.Datetime.now())
            domain.append(('create_date', '>=', fields.Datetime.to_string(baseline - timedelta(days=7))))
        logs = DieselLog.search(domain, order='write_date asc, id asc')
        if not logs:
            return
        latest = last_sync
        processed = 0
        for log in logs:
            agreement = self._resolve_agreement_for_vehicle(log.vehicle_id)
            if not agreement:
                continue
            record = self._upsert_from_diesel_log(log, agreement)
            if record:
                processed += 1
                timestamp = fields.Datetime.from_string(log.write_date) if log.write_date else False
                if not timestamp and log.create_date:
                    timestamp = fields.Datetime.from_string(log.create_date)
                if timestamp:
                    latest = timestamp if not latest else max(latest, timestamp)
        if latest:
            ICP.set_param(self._DIESEL_SYNC_PARAM, fields.Datetime.to_string(latest))
        _logger.info("Diesel auto-sync processed %s fleet issues.", processed)

    _rmc_diesel_closing_ltr_positive = models.Constraint(
        'CHECK(closing_ltr >= 0)',
        'Closing liters must be non-negative.',
    )
    _rmc_diesel_issued_ltr_positive = models.Constraint(
        'CHECK(issued_ltr >= 0)',
        'Issued liters must be non-negative.',
    )


class DieselLog(models.Model):
    _inherit = 'diesel.log'

    rmc_agreement_id = fields.Many2one(
        'rmc.contract.agreement',
        string=' Agreement',
        compute='_compute_rmc_agreement',
        store=True,
        index=True,
        help='Agreement inferred from the vehicle assignment.'
    )

    @api.depends('vehicle_id', 'vehicle_id.rmc_agreement_ids')
    def _compute_rmc_agreement(self):
        for log in self:
            agreement = False
            vehicle = log.vehicle_id
            if vehicle and vehicle.rmc_agreement_ids:
                agreements = vehicle.rmc_agreement_ids
                preferred = agreements.filtered(lambda agr: agr.state not in ('cancelled', 'expired'))
                target = (preferred or agreements).sorted(key=lambda agr: (agr.state != 'active', agr.id))[:1]
                agreement = target.id if target else False
            log.rmc_agreement_id = agreement
