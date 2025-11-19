# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

class RmcMaintenanceCheck(models.Model):
    _name = 'rmc.maintenance.check'
    _description = 'RMC Maintenance Check'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc'
    _MAINTENANCE_SYNC_PARAM = 'rmc.maintenance.last_sync'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    agreement_id = fields.Many2one('rmc.contract.agreement', string='Agreement', required=True, ondelete='restrict', tracking=True)
    contractor_id = fields.Many2one(related='agreement_id.contractor_id', string='Contractor', store=True)
    date = fields.Date(string='Check Date', required=True, default=fields.Date.context_today, tracking=True)
    machine_id = fields.Char(string='Machine/Equipment ID')
    employee_id = fields.Many2one(
        'hr.employee',
        string='Responsible Employee',
        help='Employee/operator who performed or reported this check'
    )
    checklist_ok = fields.Float(string='Checklist Completion (%)', digits=(5, 2), required=True, default=100.0)
    defects_found = fields.Text(string='Defects Found')
    repaired = fields.Boolean(string='Repaired', default=False)
    cost = fields.Monetary(string='Repair Cost', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id)
    state = fields.Selection([('draft', 'Draft'), ('pending_agreement', 'Pending Agreement'), ('validated', 'Validated')], default='draft', required=True, tracking=True)
    notes = fields.Text(string='Notes')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            agreement_id = vals.get('agreement_id')
            if agreement_id:
                self.env['rmc.contract.agreement'].browse(agreement_id)._check_closure_operation_allowed(_('create maintenance checks'))
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('rmc.maintenance.check') or _('New')
        records = super(RmcMaintenanceCheck, self).create(vals_list)
        for record in records:
            record._check_agreement_signature()
            record._validate_agreement_employee()
            record._default_employee_from_agreement()
        return records

    def write(self, vals):
        if self:
            agreements = self.mapped('agreement_id')
            if agreements:
                agreements._check_closure_operation_allowed(_('update maintenance checks'))
        res = super(RmcMaintenanceCheck, self).write(vals)
        if 'state' not in vals:
            self._check_agreement_signature()
        if 'employee_id' in vals:
            self._validate_agreement_employee()
        if 'employee_id' not in vals and 'agreement_id' in vals:
            self._default_employee_from_agreement()
        return res

    def _check_agreement_signature(self):
        for record in self:
            if not record.agreement_id.is_signed():
                record.state = 'pending_agreement'
                record.message_post(body=_('Maintenance check pending agreement signature.'))
                record.agreement_id.activity_schedule('mail.mail_activity_data_todo', summary=_('Sign agreement to validate maintenance'), note=_('Check %s waiting.') % record.name)

    @api.constrains('checklist_ok', 'cost')
    def _check_values(self):
        for record in self:
            if record.checklist_ok < 0 or record.checklist_ok > 100:
                raise ValidationError(_('Checklist completion must be between 0 and 100%.'))
            if record.cost < 0:
                raise ValidationError(_('Cost cannot be negative.'))
            if record.employee_id and record.agreement_id.driver_ids and record.employee_id not in record.agreement_id.driver_ids:
                raise ValidationError(
                    _('Employee %s is not assigned to agreement %s.') %
                    (record.employee_id.name, record.agreement_id.name)
                )

    def _validate_agreement_employee(self):
        for record in self.filtered(lambda r: r.employee_id):
            if record.agreement_id and record.agreement_id.driver_ids and record.employee_id not in record.agreement_id.driver_ids:
                raise ValidationError(
                    _('Employee %s is not assigned to agreement %s.') %
                    (record.employee_id.name, record.agreement_id.name)
                )

    def _default_employee_from_agreement(self):
        for record in self:
            if record.agreement_id and not record.employee_id and record.agreement_id.driver_ids:
                record.employee_id = record.agreement_id.driver_ids[:1]

    def action_validate(self):
        for record in self:
            if not record.agreement_id.is_signed():
                raise ValidationError(_('Cannot validate: Agreement not signed.'))
            record.state = 'validated'
            record.message_post(body=_('Maintenance check validated'))

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})

    # ------------------------------------------------------------------
    # Auto-sync helpers
    # ------------------------------------------------------------------

    @api.model
    def _build_employee_agreement_map(self, employee_ids):
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
    def _safe_employee_for_agreement(self, employee, agreement):
        if not employee or not agreement:
            return False
        if agreement.driver_ids and employee not in agreement.driver_ids:
            return False
        return employee

    def _copy_attachments_from_source(self, source_model, source_id, target_record):
        Attachment = self.env['ir.attachment'].sudo()
        source_attachments = Attachment.search([
            ('res_model', '=', source_model),
            ('res_id', '=', source_id)
        ])
        if not source_attachments:
            return
        dest_attachments = Attachment.search([
            ('res_model', '=', target_record._name),
            ('res_id', '=', target_record.id)
        ])
        existing_checksums = set(filter(None, dest_attachments.mapped('checksum')))
        for attachment in source_attachments:
            checksum = attachment.checksum
            if checksum and checksum in existing_checksums:
                continue
            Attachment.create({
                'name': attachment.name,
                'datas': attachment.datas,
                'mimetype': attachment.mimetype,
                'res_model': target_record._name,
                'res_id': target_record.id,
                'type': attachment.type,
            })
            if checksum:
                existing_checksums.add(checksum)

    @api.model
    def _prepare_request_vals(self, request, agreement, employee_map):
        if hasattr(request, 'agreement_id') and request.agreement_id:
            agreement = request.agreement_id
        employee = request.employee_id
        if not employee and request.equipment_id and 'employee_id' in request.equipment_id._fields:
            employee = request.equipment_id.employee_id
        if not agreement and employee:
            agreement = employee_map.get(employee.id)
        if not agreement:
            return {}
        employee = self._safe_employee_for_agreement(employee, agreement)
        stage = getattr(request, 'stage_id', False)
        stage_done = bool(
            (stage and getattr(stage, 'done', False)) or
            (stage and getattr(stage, 'is_done', False)) or
            getattr(request, 'kanban_state', '') == 'done'
        )
        checklist = 100.0 if stage_done else 60.0
        request_date = getattr(request, 'request_date', False)
        if not request_date:
            request_date = request.create_date
        date_value = fields.Date.to_date(request_date) if request_date else fields.Date.context_today(self)
        cost_field = request._fields.get('cost')
        cost_value = request.cost if cost_field else 0.0
        vals = {
            'agreement_id': agreement.id,
            'date': date_value,
            'machine_id': request.equipment_id.display_name if request.equipment_id else request.name,
            'employee_id': employee.id if employee else False,
            'checklist_ok': checklist,
            'defects_found': request.description or request.name,
            'repaired': stage_done,
            'cost': cost_value or 0.0,
            'currency_id': agreement.currency_id.id,
            'notes': _('Auto-synced from maintenance request %s') % (request.name or request.id),
        }
        return vals

    def _upsert_request(self, request, employee_map):
        agreement = request.agreement_id or employee_map.get(request.employee_id.id if request.employee_id else False)
        vals = self._prepare_request_vals(request, agreement, employee_map)
        if not vals:
            return False, False
        sync_name = f"SYNC-MR-{request.id}"
        record = self.search([('name', '=', sync_name)], limit=1)
        created = False
        if record:
            record.write(vals)
        else:
            vals['name'] = sync_name
            record = self.create(vals)
            created = True
        self._copy_attachments_from_source('maintenance.request', request.id, record)
        return record, created

    @api.model
    def _prepare_breakdown_vals(self, breakdown):
        checklist = 100.0 if breakdown.state == 'closed' else 60.0
        date_value = fields.Date.to_date(breakdown.start_time) if breakdown.start_time else fields.Date.context_today(self)
        return {
            'agreement_id': breakdown.agreement_id.id,
            'date': date_value,
            'machine_id': breakdown.event_type,
            'employee_id': False,
            'checklist_ok': checklist,
            'defects_found': breakdown.description,
            'repaired': breakdown.state == 'closed',
            'cost': breakdown.deduction_amount or 0.0,
            'currency_id': breakdown.currency_id.id,
            'notes': _('Auto-synced from breakdown %s') % (breakdown.name or breakdown.id),
        }

    def _upsert_breakdown(self, breakdown):
        vals = self._prepare_breakdown_vals(breakdown)
        sync_name = f"SYNC-BD-{breakdown.id}"
        record = self.search([('name', '=', sync_name)], limit=1)
        created = False
        if record:
            record.write(vals)
        else:
            vals['name'] = sync_name
            record = self.create(vals)
            created = True
        self._copy_attachments_from_source('rmc.breakdown.event', breakdown.id, record)
        return record, created

    @api.model
    def _timestamp_from_record(self, record):
        ts = record.write_date or record.create_date
        return fields.Datetime.from_string(ts) if ts else None

    @api.model
    def cron_sync_from_maintenance(self):
        """Create/update maintenance checks from maintenance requests & breakdowns."""
        self = self.sudo()
        Request = self.env['maintenance.request'].sudo()
        Breakdown = self.env['rmc.breakdown.event'].sudo()
        ICP = self.env['ir.config_parameter'].sudo()
        last_sync_str = ICP.get_param(self._MAINTENANCE_SYNC_PARAM)
        last_sync = fields.Datetime.from_string(last_sync_str) if last_sync_str else None
        buffer_start = (last_sync - timedelta(hours=1)) if last_sync else None
        if buffer_start:
            request_domain = [('write_date', '>', fields.Datetime.to_string(buffer_start))]
            breakdown_domain = [('write_date', '>', fields.Datetime.to_string(buffer_start))]
        else:
            baseline = fields.Datetime.from_string(fields.Datetime.now())
            cutoff = fields.Datetime.to_string(baseline - timedelta(days=30))
            request_domain = [('create_date', '>=', cutoff)]
            breakdown_domain = [('create_date', '>=', cutoff)]
        requests = Request.search(request_domain, order='write_date asc, id asc')
        breakdowns = Breakdown.search(breakdown_domain, order='write_date asc, id asc')
        employees = requests.mapped('employee_id').ids
        equipment_employees = requests.mapped('equipment_id.employee_id').ids
        employee_map = self._build_employee_agreement_map(employees + equipment_employees)
        latest = last_sync
        created = updated = 0
        for request in requests:
            record, is_created = self._upsert_request(request, employee_map)
            if not record:
                continue
            if is_created:
                created += 1
            else:
                updated += 1
            timestamp = self._timestamp_from_record(request)
            if timestamp:
                latest = timestamp if not latest else max(latest, timestamp)
        for breakdown in breakdowns:
            record, is_created = self._upsert_breakdown(breakdown)
            if not record:
                continue
            if is_created:
                created += 1
            else:
                updated += 1
            timestamp = self._timestamp_from_record(breakdown)
            if timestamp:
                latest = timestamp if not latest else max(latest, timestamp)
        if latest:
            ICP.set_param(self._MAINTENANCE_SYNC_PARAM, fields.Datetime.to_string(latest))
        if created or updated:
            _logger.info("Maintenance auto-sync: created=%s, updated=%s", created, updated)

    _rmc_maintenance_cost_positive = models.Constraint(
        'CHECK(cost >= 0)',
        'Cost must be non-negative.',
    )
