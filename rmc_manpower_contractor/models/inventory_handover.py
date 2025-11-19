# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class RmcInventoryHandover(models.Model):
    _name = 'rmc.inventory.handover'
    _description = 'RMC Inventory Handover'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    agreement_id = fields.Many2one('rmc.contract.agreement', string='Agreement', required=True, ondelete='restrict', tracking=True)
    contractor_id = fields.Many2one(related='agreement_id.contractor_id', string='Contractor', store=True)
    date = fields.Date(string='Date', required=True, default=fields.Date.context_today, tracking=True)
    employee_id = fields.Many2one(
        'hr.employee',
        string='Responsible Employee',
        help='Employee accountable for this handover'
    )
    
    item_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
    )
    uom_id = fields.Many2one('uom.uom', string='Unit of Measure', readonly=True)
    
    issued_qty = fields.Float(string='Issued Quantity', digits='Product Unit of Measure', required=True, default=0.0)
    returned_qty = fields.Float(string='Returned Quantity', digits='Product Unit of Measure', default=0.0)
    variance_qty = fields.Float(string='Variance Qty', compute='_compute_variance', store=True, digits='Product Unit of Measure')
    variance_value = fields.Monetary(string='Variance Value', compute='_compute_variance', store=True, currency_field='currency_id', help='Shortage/Excess cost impact')
    operation_type = fields.Selection([
        ('contract_issue_product', 'Contract Issue Product'),
        ('other', 'Other'),
    ], string='Operation Type', default='contract_issue_product', required=True)
    picking_id = fields.Many2one('stock.picking', string='Inventory Request', readonly=True, copy=False)
    inventory_request_ref = fields.Char(string='Inventory Ref', related='picking_id.name', readonly=True)
    
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id)
    unit_price = fields.Monetary(string='Unit Price', currency_field='currency_id', help='Standard cost per unit')
    
    state = fields.Selection([('draft', 'Draft'), ('issued', 'Issued'), ('returned', 'Returned'), ('reconciled', 'Reconciled')], default='draft', required=True, tracking=True)
    notes = fields.Text(string='Notes')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    is_final = fields.Boolean(string='Final Handback', help='Mark when this record represents the final handover for closure.')
    damage_cost = fields.Monetary(string='Damage Cost', currency_field='currency_id', help='Charge for any damage noted during final handback.', default=0.0)
    acknowledged_by = fields.Many2one('res.users', string='Acknowledged By', tracking=True)
    ack_signature = fields.Binary(string='Acknowledgement Signature', attachment=True)
    settlement_included = fields.Boolean(string='Included in Settlement', default=False, copy=False)
    settlement_reference = fields.Char(string='Settlement Reference', copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('rmc.inventory.handover') or _('New')
        records = super(RmcInventoryHandover, self).create(vals_list)
        for record in records:
            record._validate_agreement_employee()
            record._default_employee_from_agreement()
        return records

    @api.depends('issued_qty', 'returned_qty', 'unit_price')
    def _compute_variance(self):
        for record in self:
            record.variance_qty = record.issued_qty - record.returned_qty
            record.variance_value = record.variance_qty * record.unit_price

    @api.onchange('item_id')
    def _onchange_item_id(self):
        if self.item_id:
            self.uom_id = self.item_id.uom_id
            self.unit_price = self.item_id.standard_price

    def monthly_reconcile_inventory(self):
        """
        Called by billing wizard to reconcile inventory for the month
        Returns total variance value (positive = shortage, negative = excess)
        """
        self.ensure_one()
        self.state = 'reconciled'
        self.message_post(body=_('Inventory reconciled. Variance: %s %s') % (self.variance_qty, self.uom_id.name))
        return self.variance_value

    def action_issue(self):
        self.write({'state': 'issued'})
        self.message_post(body=_('Inventory issued'))

    def action_return(self):
        self.write({'state': 'returned'})
        self.message_post(body=_('Inventory returned'))
        self._validate_agreement_employee()

    def action_create_inventory_request(self):
        picking_type = self.env.ref('rmc_manpower_contractor.picking_type_contract_issue', raise_if_not_found=False)
        if not picking_type:
            raise ValidationError(_('Contract Issue Product picking type is not configured.'))
        for record in self:
            if record.picking_id:
                continue
            if record.issued_qty <= 0:
                raise ValidationError(_('Issued quantity must be greater than zero to create an inventory request.'))
            move_qty = record.issued_qty
            picking_vals = {
                'picking_type_id': picking_type.id,
                'partner_id': record.contractor_id.id,
                'origin': record.name,
                'company_id': record.company_id.id,
                'agreement_id': record.agreement_id.id if 'agreement_id' in self.env['stock.picking']._fields else False,
            }
            picking = self.env['stock.picking'].create(picking_vals)
            move_vals = {
                'name': record.item_id.display_name,
                'product_id': record.item_id.id,
                'product_uom_qty': move_qty,
                'product_uom': record.uom_id.id,
                'picking_id': picking.id,
                'location_id': picking_type.default_location_src_id.id or picking.location_id.id,
                'location_dest_id': picking_type.default_location_dest_id.id or picking.location_dest_id.id,
            }
            picking.move_ids_without_package = [(0, 0, move_vals)]
            record.picking_id = picking.id
            record.state = 'issued'
        return True

    def action_open_inventory_request(self):
        self.ensure_one()
        if not self.picking_id:
            raise ValidationError(_('No inventory request is linked yet.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Inventory Request'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.picking_id.id,
            'target': 'current',
        }

    def write(self, vals):
        res = super(RmcInventoryHandover, self).write(vals)
        if 'employee_id' in vals:
            self._validate_agreement_employee()
        if 'employee_id' not in vals and 'agreement_id' in vals:
            self._default_employee_from_agreement()
        return res

    @api.constrains('issued_qty', 'returned_qty')
    def _check_quantities(self):
        for record in self:
            if record.issued_qty < 0:
                raise ValidationError(_('Issued quantity cannot be negative.'))
            if record.returned_qty < 0:
                raise ValidationError(_('Returned quantity cannot be negative.'))
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

    _rmc_inventory_issued_qty_positive = models.Constraint(
        'CHECK(issued_qty >= 0)',
        'Issued quantity must be non-negative.',
    )
    _rmc_inventory_returned_qty_positive = models.Constraint(
        'CHECK(returned_qty >= 0)',
        'Returned quantity must be non-negative.',
    )

    @api.constrains('agreement_id', 'item_id', 'state')
    def _check_unique_active_request(self):
        active_states = {'draft', 'issued', 'returned'}
        for record in self:
            if record.state in active_states:
                duplicate = self.search([
                    ('id', '!=', record.id),
                    ('agreement_id', '=', record.agreement_id.id),
                    ('item_id', '=', record.item_id.id),
                    ('state', 'in', list(active_states))
                ], limit=1)
                if duplicate:
                    raise ValidationError(
                        _('Product %s already has an active inventory request for agreement %s.')
                        % (record.item_id.display_name, record.agreement_id.name)
                    )
