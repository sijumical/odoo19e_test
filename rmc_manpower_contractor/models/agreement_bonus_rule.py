# -*- coding: utf-8 -*-
"""Bonus/Penalty rule definitions stored per agreement."""

from odoo import fields, models, _


class RmcAgreementBonusRule(models.Model):
    _name = 'rmc.agreement.bonus.rule'
    _description = 'Agreement Bonus/Penalty Rule'
    _order = 'sequence, id'

    agreement_id = fields.Many2one(
        'rmc.contract.agreement',
        string='Agreement',
        required=True,
        ondelete='cascade'
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Rule Label', required=True, translate=True)
    rule_type = fields.Selection(
        selection=[
            ('bonus', 'Bonus'),
            ('penalty', 'Penalty'),
        ],
        string='Rule Type',
        required=True,
        default='bonus'
    )
    trigger_condition = fields.Char(
        string='Trigger',
        help='Condition or KPI threshold that activates this adjustment.'
    )
    percentage = fields.Float(
        string='Adjustment (%)',
        help='Positive for bonuses, negative for penalties.'
    )
    notes = fields.Text(string='Notes')

    def name_get(self):
        result = []
        for rule in self:
            label = rule.name or _('Rule')
            if rule.percentage:
                label = f"{label} ({rule.percentage:+.1f}%)"
            result.append((rule.id, label))
        return result
