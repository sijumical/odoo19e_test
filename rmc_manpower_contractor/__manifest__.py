# -*- coding: utf-8 -*-
{
    'name': 'RMC Manpower Contractor Integration',
    'version': '19.0.1.0.0',
    'category': 'Human Resources',
    'summary': 'Unified HRM-style contractor lifecycle with Agreements, Dynamic Web Agreement (QWeb/Website), Odoo Sign, and type-based performance dependencies driving payment holds and monthly scores.',
    'description': """
        End-to-end contractor lifecycle for RMC & manpower operations integrating:
        • HRM-style onboarding → dynamic web agreement (QWeb + Odoo Sign mandatory)
        • Designation-wise wage matrix (Part-A fixed + Part-B variable linked to MGQ)
        • Contract-type-based KPIs (driver/pump/accounts)
        • Clause 9 logic (Breakdown / NGT / Force Majeure) impacting variable billing
        • Star-rating performance system
        • Monthly performance → draft vendor bill with multi-level approvals + TDS auto
        • Monthly billing attaches supporting reports and reconciles contractor inventory handover
    """,
    'author': 'SmarterPeak',
    'website': 'https://www.smarterpeak.com',
    'depends': [
        'base',
        'base_automation',
        'documents',
        'sign',
        'hr',
        'hr_attendance',
        'approvals',
        'website',
        'website_hr_recruitment',
        'portal',
        'mail',
        'account',
        'analytic',
        'l10n_in',
        'fleet',
        'maintenance',
        'stock',
        'diesel_log',
    ],
    'data': [
        'security/security_groups.xml',
        'security/bonus_rule_models.xml',
        'security/ir.model.access.csv',
        'security/record_rules.xml',
        'data/config_parameters.xml',
        'data/automated_action.xml',
        'data/cron.xml',
        'data/stock_picking_type.xml',
        'data/sign_template_data.xml',
        'data/agreement_clause_templates.xml',
        'data/hr_job_data.xml',
        'views/manpower_matrix_views.xml',
        'views/diesel_log_views.xml',
        'views/maintenance_check_views.xml',
        'views/attendance_compliance_views.xml',
        'views/breakdown_event_views.xml',
        'views/inventory_handover_views.xml',
        'views/billing_prepare_log_views.xml',
        'views/agreement_views.xml',
        'wizards/billing_prepare_wizard_views.xml',
        'wizards/agreement_send_preview_wizard_views.xml',
        'wizards/agreement_renewal_wizard_views.xml',
        'wizards/settlement_wizard_views.xml',
        'views/website_manpower_partner_templates.xml',
        'views/website_templates.xml',
        'views/website_job_templates.xml',
        'views/menuitems.xml',
        'reports/performance_report.xml',
        'reports/agreement_report.xml',
        'reports/agreement_performance_summary.xml',
        'reports/billing_support_report.xml',
        'reports/monthly_summary_templates.xml',
        'reports/settlement_report.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'rmc_manpower_contractor/static/src/js/sign_request_documents_dropdown_patch.js',
            'rmc_manpower_contractor/static/src/js/selection_field_patch.js',
            'rmc_manpower_contractor/static/src/scss/billing_dashboard.scss',
        ],
        'web.assets_frontend': [
            'rmc_manpower_contractor/static/src/scss/manpower_partner.scss',
            'rmc_manpower_contractor/static/src/js/manpower_partner.js',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
