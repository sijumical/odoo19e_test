# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class RmcAgreementSigner(models.Model):
    _name = 'rmc.agreement.signer'
    _description = 'Agreement Signer'
    _order = 'sequence, id'

    agreement_id = fields.Many2one(
        'rmc.contract.agreement',
        string='Agreement',
        required=True,
        ondelete='cascade'
    )
    role_id = fields.Many2one(
        'sign.item.role',
        string='Signer Role',
        required=True,
        ondelete='restrict'
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Signer',
        required=True,
        ondelete='restrict'
    )
    sequence = fields.Integer(default=10)

    _sql_constraints = [
        (
            'unique_agreement_role',
            'unique(agreement_id, role_id)',
            'A role can be assigned only once per agreement.'
        )
    ]

    @api.constrains('partner_id')
    def _check_partner_company(self):
        for signer in self:
            if signer.partner_id and not signer.partner_id.email:
                raise ValidationError(
                    _('Signer %s must have an email address to receive the signature request.') %
                    signer.partner_id.display_name
                )
