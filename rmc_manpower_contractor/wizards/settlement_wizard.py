# -*- coding: utf-8 -*-
"""Settlement wizard for agreement closure"""

import base64
from datetime import datetime, time

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_is_zero, float_compare


class RmcAgreementSettlementWizard(models.TransientModel):
    _name = 'rmc.agreement.settlement.wizard'
    _description = 'Agreement Settlement Wizard'

    agreement_id = fields.Many2one(
        'rmc.contract.agreement',
        string='Agreement',
        required=True,
        readonly=True
    )
    contractor_id = fields.Many2one(
        related='agreement_id.contractor_id',
        string='Contractor',
        store=True,
        readonly=True
    )
    period_start = fields.Date(string='Period Start', required=True)
    period_end = fields.Date(string='Period End', required=True)
    currency_id = fields.Many2one(
        related='agreement_id.currency_id',
        string='Currency',
        readonly=True,
        store=True
    )
    mgq_target = fields.Float(related='agreement_id.mgq_target', string='MGQ Target', readonly=True)
    mgq_actual_qty = fields.Float(string='Actual Production (m³)', digits='Product Unit of Measure', readonly=True)
    mgq_achieved = fields.Boolean(string='MGQ Achieved?', readonly=True)
    variable_pay_amount = fields.Monetary(string='Variable Pay (Part-B)', readonly=True, currency_field='currency_id')
    breakdown_deduction_total = fields.Monetary(string='Breakdown Deductions', currency_field='currency_id', readonly=True)
    inventory_variance_total = fields.Monetary(string='Inventory Variance', currency_field='currency_id', readonly=True)
    damage_cost_total = fields.Monetary(string='Damage Charges', currency_field='currency_id', readonly=True)
    open_bills_total = fields.Monetary(
        string='Open Vendor Bills',
        currency_field='currency_id',
        readonly=True,
        compute='_compute_open_bills_total',
        store=True,
    )
    final_payable_amount = fields.Monetary(
        string='Final Payable',
        currency_field='currency_id',
        compute='_compute_final_payable',
        store=True
    )
    proposed_action = fields.Selection([
        ('final_bill', 'Create Final Bill'),
        ('credit_note', 'Create Credit Note'),
        ('zero_balance', 'Zero Balance'),
    ], string='Proposed Action', default='zero_balance', required=True)
    notes = fields.Text(string='Notes')
    breakdown_event_ids = fields.Many2many(
        'rmc.breakdown.event',
        'rmc_agreement_settlement_breakdown_rel',
        'wizard_id',
        'event_id',
        string='Breakdown Events',
        readonly=True
    )
    inventory_handover_ids = fields.Many2many(
        'rmc.inventory.handover',
        'rmc_agreement_settlement_inventory_rel',
        'wizard_id',
        'handover_id',
        string='Inventory Records',
        readonly=True
    )
    open_bill_ids = fields.Many2many(
        'account.move',
        'rmc_agreement_settlement_bill_rel',
        'wizard_id',
        'move_id',
        string='Open Bills',
        readonly=True
    )
    hold_detected = fields.Boolean(string='Hold Detected', readonly=True)
    hold_reason = fields.Text(string='Hold Reasons', readonly=True)
    proposed_action_label = fields.Char(string='Action Label', compute='_compute_action_label', store=False)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        agreement_id = (
            defaults.get('agreement_id')
            or self.env.context.get('default_agreement_id')
            or self.env.context.get('active_id')
        )
        if agreement_id:
            agreement = self.env['rmc.contract.agreement'].browse(agreement_id)
            defaults.setdefault('agreement_id', agreement.id)
            if not defaults.get('period_start') or not defaults.get('period_end'):
                period_start, period_end = agreement._default_settlement_period()
                defaults.setdefault('period_start', period_start)
                defaults.setdefault('period_end', period_end)
        return defaults

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._prefill_financials()
        return records

    def _compute_action_label(self):
        selection = dict(self._fields['proposed_action'].selection)
        for wizard in self:
            wizard.proposed_action_label = selection.get(wizard.proposed_action)

    def _prefill_financials(self):
        for wizard in self:
            if not wizard.agreement_id:
                continue
            wizard._load_mgq_snapshot()
            wizard._load_breakdown_records()
            wizard._load_inventory_records()
            wizard._load_open_bills()
            wizard._evaluate_hold_state()

    def _load_mgq_snapshot(self):
        self.ensure_one()
        BillingWizard = self.env['rmc.billing.prepare.wizard']
        billing = BillingWizard.create({
            'agreement_id': self.agreement_id.id,
            'period_start': self.period_start,
            'period_end': self.period_end,
        })
        billing._sync_mgq_with_prime_output()
        billing._apply_attendance_proration()
        billing._compute_billing_amounts()
        actual_qty = billing.mgq_achieved or 0.0
        target = billing.mgq_target or 0.0
        variable_amount = billing.part_b_amount or 0.0
        mgq_ok = bool(target and actual_qty >= target)
        vals = {
            'mgq_actual_qty': actual_qty,
            'mgq_achieved': mgq_ok,
            'variable_pay_amount': variable_amount,
        }
        billing.unlink()
        self.write(vals)

    def _load_breakdown_records(self):
        self.ensure_one()
        Breakdown = self.env['rmc.breakdown.event']
        start_date = fields.Date.from_string(self.period_start) if self.period_start else fields.Date.context_today(self)
        end_date = fields.Date.from_string(self.period_end) if self.period_end else fields.Date.context_today(self)
        start_dt = datetime.combine(start_date, time.min)
        end_dt = datetime.combine(end_date, time.max)
        events = Breakdown.search([
            ('agreement_id', '=', self.agreement_id.id),
            ('start_time', '>=', fields.Datetime.to_string(start_dt)),
            ('start_time', '<=', fields.Datetime.to_string(end_dt)),
            ('responsibility', '=', 'contractor'),
            ('settlement_included', '=', False),
        ])
        self.write({
            'breakdown_event_ids': [(6, 0, events.ids)],
            'breakdown_deduction_total': sum(events.mapped('deduction_amount')),
        })

    def _load_inventory_records(self):
        self.ensure_one()
        Inventory = self.env['rmc.inventory.handover']
        records = Inventory.search([
            ('agreement_id', '=', self.agreement_id.id),
            ('state', '!=', 'reconciled'),
            ('settlement_included', '=', False),
        ])
        damage_total = sum(records.filtered('is_final').mapped('damage_cost'))
        variance_total = sum(records.mapped('variance_value'))
        self.write({
            'inventory_handover_ids': [(6, 0, records.ids)],
            'damage_cost_total': damage_total,
            'inventory_variance_total': variance_total + damage_total,
        })

    def _load_open_bills(self):
        self.ensure_one()
        AccountMove = self.env['account.move']
        bills = AccountMove.search([
            ('agreement_id', '=', self.agreement_id.id),
            ('move_type', 'in', ('in_invoice', 'in_refund')),
            ('payment_state', '!=', 'paid'),
            ('state', 'in', ('posted', 'draft')),
        ])
        self.write({
            'open_bill_ids': [(6, 0, bills.ids)],
        })

    @api.depends('open_bill_ids', 'open_bill_ids.amount_residual')
    def _compute_open_bills_total(self):
        for wizard in self:
            wizard.open_bills_total = sum(wizard.open_bill_ids.mapped('amount_residual'))

    @api.depends('variable_pay_amount', 'breakdown_deduction_total', 'inventory_variance_total', 'open_bills_total')
    def _compute_final_payable(self):
        for wizard in self:
            wizard.final_payable_amount = (
                (wizard.variable_pay_amount or 0.0)
                - (wizard.breakdown_deduction_total or 0.0)
                - (wizard.inventory_variance_total or 0.0)
                - (wizard.open_bills_total or 0.0)
            )

    def _evaluate_hold_state(self):
        for wizard in self:
            reasons = wizard.agreement_id._get_settlement_blockers(
                currency=wizard.currency_id,
                inventory_records=wizard.inventory_handover_ids,
            )
            wizard.write({
                'hold_detected': bool(reasons),
                'hold_reason': '\n'.join(reasons) if reasons else False,
            })

    def action_confirm(self):
        self.ensure_one()
        agreement = self.agreement_id
        if agreement.state != 'closure_review':
            raise ValidationError(_('Agreement must be in closure review before settlement.'))
        self._evaluate_hold_state()
        if self.hold_detected:
            agreement.write({
                'settlement_hold': True,
                'settlement_hold_reason': self.hold_reason,
            })
            raise ValidationError(_('Settlement cannot proceed:\n%s') % (self.hold_reason or ''))
        agreement.write({'settlement_hold': False, 'settlement_hold_reason': False})
        move = self._perform_financial_action()
        self._mark_consumed_records()
        log = self._create_settlement_log(move)
        attachment = self._generate_report_attachment()
        summary = _(
            'Settlement packet prepared with action %(action)s. Final balance: %(amount).2f.'
        ) % {
            'action': self.proposed_action_label,
            'amount': self.final_payable_amount,
        }
        agreement.message_post(body=summary, attachment_ids=attachment.ids if attachment else False)
        if log and attachment:
            log.message_post(body=_('Settlement PDF attached.'), attachment_ids=attachment.ids)
        agreement.state = 'settled'
        self._schedule_settlement_activities()
        return {'type': 'ir.actions.act_window_close'}

    def _perform_financial_action(self):
        self.ensure_one()
        amount = self.final_payable_amount or 0.0
        precision = self.currency_id.rounding or 0.01
        if self.proposed_action == 'zero_balance':
            if not float_is_zero(amount, precision_rounding=precision):
                raise ValidationError(_('Final amount must be zero for a zero balance closure.'))
            return False
        if self.proposed_action == 'final_bill' and float_compare(amount, 0.0, precision_rounding=precision) <= 0:
            raise ValidationError(_('Final amount must be positive for a final bill.'))
        if self.proposed_action == 'credit_note' and float_compare(amount, 0.0, precision_rounding=precision) >= 0:
            raise ValidationError(_('Final amount must be negative for a credit note.'))
        move_type = 'in_invoice' if self.proposed_action == 'final_bill' else 'in_refund'
        move_amount = abs(amount)
        if float_is_zero(move_amount, precision_rounding=precision):
            return False
        expense_account = self._get_settlement_account()
        line_vals = [(0, 0, {
            'name': _('Agreement Closure Adjustment'),
            'quantity': 1.0,
            'price_unit': move_amount,
            'account_id': expense_account.id,
        })]
        move = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': self.contractor_id.id,
            'agreement_id': self.agreement_id.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_origin': self.agreement_id.name,
            'invoice_line_ids': line_vals,
        })
        return move

    def _get_settlement_account(self):
        agreement = self.agreement_id
        Account = self.env['account.account']
        account = Account.search([
            ('company_id', '=', agreement.company_id.id),
            ('internal_type', '=', 'expense'),
            ('deprecated', '=', False),
        ], limit=1)
        if not account:
            raise ValidationError(_('Please configure at least one expense account for settlement entries.'))
        return account

    def _mark_consumed_records(self):
        reference = '%s/%s' % (self.agreement_id.name, fields.Date.context_today(self))
        if self.breakdown_event_ids:
            self.breakdown_event_ids.write({'settlement_included': True, 'settlement_reference': reference})
        if self.inventory_handover_ids:
            self.inventory_handover_ids.write({'settlement_included': True, 'settlement_reference': reference})

    def _create_settlement_log(self, move):
        Log = self.env['rmc.billing.prepare.log']
        vals = {
            'agreement_id': self.agreement_id.id,
            'contractor_id': self.contractor_id.id,
            'period_start': self.period_start,
            'period_end': self.period_end,
            'mgq_target': self.mgq_target,
            'mgq_achieved': self.mgq_actual_qty,
            'mgq_achievement_pct': 100.0 if self.mgq_achieved else 0.0,
            'part_b_amount': self.variable_pay_amount,
            'breakdown_deduction': self.breakdown_deduction_total,
            'inventory_variance': self.inventory_variance_total,
            'total_amount': self.final_payable_amount,
            'currency_id': self.currency_id.id,
            'notes': self.notes,
            'category': 'closure',
            'state': 'done',
            'settlement_hold': self.hold_detected,
            'settlement_hold_reason': self.hold_reason,
        }
        if move:
            vals['bill_id'] = move.id
        log = Log.create(vals)
        return log

    def _generate_report_attachment(self):
        report = self.env.ref('rmc_manpower_contractor.action_rmc_agreement_settlement_report', raise_if_not_found=False)
        if not report:
            return False
        pdf_content, _ = report._render_qweb_pdf(self.ids)
        filename = 'Settlement-%s.pdf' % (self.agreement_id.name,)
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'datas': base64.b64encode(pdf_content),
            'type': 'binary',
            'res_model': 'rmc.contract.agreement',
            'res_id': self.agreement_id.id,
            'mimetype': 'application/pdf',
        })
        return attachment

    def _schedule_settlement_activities(self):
        agreement = self.agreement_id
        finance_group = self.env.ref('account.group_account_user', raise_if_not_found=False)
        finance_user = finance_group.users[:1] if finance_group and finance_group.users else self.env.user
        agreement.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=finance_user.id,
            summary=_('Settlement packet ready'),
            note=_('Please review the settlement packet for %s.') % agreement.display_name,
        )
        owner = agreement.create_uid or self.env.user
        agreement.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=owner.id,
            summary=_('Settlement completed'),
            note=_('Agreement %s moved to settled state.') % agreement.display_name,
        )
