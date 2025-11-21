# Universal Daily Reporting & Performance System — Updated Specification (CR Stage-1)

## Module Overview
- **Purpose:** Provide a reusable, multi-company/branch daily reporting system for managers to record activities, complaints, staff logs, contractor performance, configurable KPI metrics, and dynamic branch/department-specific sections with dashboards and printable reports.
- **Scope:** Independent Odoo 19 Enterprise custom module using only native ORM, views, security, and QWeb assets. No dependencies on other customized apps or Studio artifacts.

## Model List & Purpose
1. **daily.manager.report** — Main daily manager report capturing overall activities and linked lines per branch/department.
2. **daily.manager.report.complaint** — Complaints/issues raised in a report (universal section).
3. **daily.manager.report.staff.log** — Staff attendance/behavior/issue logs (universal section).
4. **daily.manager.report.contractor.performance** — Contractor performance ratings/comments per report; designed for future aggregation (universal section).
5. **daily.manager.report.metric.line** — Per-report KPI metric values generated from templates.
6. **daily.manager.metric.template** — Configurable KPI metric templates (global or branch-specific) that auto-generate metric lines.
7. **daily.manager.section.template** — Dynamic section templates configurable per company/branch/department for additional report sections.
8. **daily.manager.report.section** — Dynamic section lines attached to the daily report, created from section templates during report creation.

## Fields (by Model)
- **daily.manager.report**: date, company_id, branch_id (if distinct), department_id (optional), manager_id (M2O res.users), activities (text/html), complaint_ids (O2M), staff_log_ids (O2M), contractor_performance_ids (O2M), metric_line_ids (O2M), dynamic_section_ids (O2M), state (selection: draft/submitted), submitted_on (datetime), submitted_by (M2O res.users), notes (text), active (bool).
- **daily.manager.report.complaint**: report_id (M2O), company_id, branch_id, department_id (optional), description (text), customer_id (M2O res.partner), severity (selection), action_taken (text), resolved (bool), responsible_id (M2O res.users), date (date), reference (char).
- **daily.manager.report.staff.log**: report_id, company_id, branch_id, department_id (optional), staff_id (M2O hr.employee or res.partner depending on availability), role (char), shift (char/selection), issue (text), action (text), attendance_flag (selection), note (text).
- **daily.manager.report.contractor.performance**: report_id, company_id, branch_id, department_id (optional), contractor_id (M2O res.partner), rating (float/int), comment (text), follow_up_action (text), attachment_ids (M2M ir.attachment), reference_period (char/date range placeholder), active (bool).
- **daily.manager.report.metric.line**: report_id, template_id (M2O metric template), name (char), metric_type (selection from template), int_value, float_value, text_value, selection_value, branch_id, company_id, department_id (optional), sequence.
- **daily.manager.metric.template**: name, code, metric_type (selection: int/float/text/selection), selection_options (text/json), active (bool), company_id (optional for branch), branch_id (optional), department_id (optional), description, default_value fields (per type), required (bool), sequence.
- **daily.manager.section.template**: name, code, company_id (optional), branch_id (optional), department_id (optional), active (bool), sequence, description.
- **daily.manager.report.section**: report_id (M2O), company_id, branch_id, department_id (optional), template_id (M2O section template), title (char, default from template), subject (char), description (text/html), employee_id (optional M2O hr.employee), partner_id (optional M2O res.partner), sequence.

## Business Rules
- Reports are linked to company/branch/department and manager; defaults follow user context.
- Metric lines auto-created on new report for active templates applicable to the report’s company/branch/department (global + matching scope).
- Dynamic section lines auto-created on new report from applicable section templates: global, branch-specific, and department-specific templates aligned with report company/branch/department.
- Universal sections (complaints, staff logs, contractor performance) remain present for all reports and inherit company/branch/department from the parent report; no removal of these sections.
- State flow: draft → submitted; submission records submitted_on and submitted_by; submitted reports are read-only except by Reporting Admin.
- Child records must match parent company/branch/department; onchange/default logic keeps alignment.
- Multi-company: data isolated by company_id; branch/department filters respect company and user allowed companies; create/update restricted by company rules.
- Deletion allowed only in draft state; submitted records archived via active flag when necessary.

## Workflow Explanation
1. Manager creates daily report in draft, prefilled company/branch/department/manager.
2. On create, metric lines and dynamic sections are generated from templates relevant to company/branch/department scope.
3. Manager fills activities, complaints, staff logs, contractor performance, dynamic sections, and metric values.
4. Manager submits; record becomes submitted with timestamp/user; non-admin users cannot edit submitted reports.
5. Reporting Admin can reopen/edit if necessary and access all branches/departments.

## Multi-Company / Multi-Branch Behavior
- company_id required on all models; branch_id aligns with company_id; department_id optional but must match allowed departments for the user where applicable.
- Default company/branch derived from user context; allowed companies restrict accessible records.
- Record rules enforce users only see data in their allowed companies/branches/departments.
- Branch managers limited to their company_ids and branch scopes; department heads further constrained by department_id where used.
- Reporting admins have unrestricted multi-company access and can view branch/global dashboards.

## Security Design
- **Groups:**
  - Reporting Admin: full access to all models/records; can view global dashboards and print/send reports.
  - Branch Manager / Department Head: CRUD within own company/branch (and department where applicable); can submit reports; cannot edit submitted reports (except own before submission).
- **ACLs:**
  - Admin: read/write/create/unlink on all models.
  - Branch Manager/Department Head: read/write/create on scoped records; unlink only in draft; no delete after submission.
- **Record Rules:**
  - Branch Manager/Department Head: domain `['&', ('company_id', 'in', user.company_ids.ids), '|', ('branch_id', '=', False), ('branch_id', 'in', user.branch_ids.ids)]` plus department filter when department_id is present/required; applied to all models including dynamic sections and templates.
  - Admin: global unrestricted rule.
- **Button Access:** submission/open, email/WhatsApp send actions restricted to proper groups with company scope checks.

## View Requirements
- **Main Report Form:** sections for header (date, company, branch, department, manager, state), activities, complaints (O2M list), staff logs (O2M list), contractor performance (O2M list), KPI metric lines (editable grid respecting metric_type), dynamic sections (O2M list/form leveraging templates), notes, submission and communication buttons, statusbar for state.
- **List View:** columns date, company, branch, department, manager, state; filters for date ranges, branch, company, department, state; default filter on current company/branch.
- **Search:** facets for manager, contractor, complaint severity, metric template, dynamic section template, state, date.
- **Graph/Pivot Dashboards:**
  - Complaints: count by severity/branch/date/department.
  - Staff Issues: count by attendance_flag/issue type/branch/department.
  - Contractor Rating: average rating by contractor/branch/date/department.
  - Custom Metrics: pivot by template vs date/branch/department; graph supporting numeric metrics only.
  - Dynamic Sections: optional pivot/graph by template vs branch/department if feasible within scope.
- **Menus:**
  - Reporting → Daily Reports (list/form).
  - Reporting → Dashboards → Branch Dashboard (pre-filter by user branch), Global Dashboard (admin only).
  - Configuration → KPI Metrics (templates), Dynamic Section Templates, possibly sequences/settings.

## QWeb Report Outline
- **Daily Manager Report PDF:** company header with logo/address, report metadata (date, branch, department, manager, state), activities summary, complaints table, staff logs table, contractor performance table with ratings, KPI metrics section showing values by template type, dynamic section details (title/subject/description), footer with submission info and branding.
- **Contractor Performance Summary (optional):** grouped ratings/comments for contractor lines within a report, suited for attachment or print.

## Task Breakdown
- **TASK-01:** Scaffold module structure (manifest, init files) for Odoo 19E custom module; define dependencies on base, mail (for chatter/communication), hr (if staff uses hr.employee), report; add data directories. *Files:* `__manifest__.py`, `__init__.py`, package folders. *Depends on:* none. (~100–200 LOC)
- **TASK-02:** Define core models and basic fields for reports and universal sections (complaint, staff log, contractor performance) plus metric templates/lines with company/branch/department relations, states, constraints; include auto-propagation of company/branch/department to child lines. *Files:* `models/*.py`, `__init__.py`. *Depends on:* TASK-01. (~100–200 LOC)
- **TASK-03:** Implement metric template logic to auto-generate metric lines on report creation; add onchange/defaults for company/branch/department, and state transition methods (submit/reopen) enforcing business rules. *Files:* `models/*.py`. *Depends on:* TASK-02. (~100–200 LOC)
- **TASK-04:** Introduce dynamic section framework models (templates and report sections) with company/branch/department scoping, sequencing, and descriptions. *Files:* `models/section_template.py`, `models/report_section.py`, `__init__.py`. *Depends on:* TASK-02. (~100–200 LOC)
- **TASK-05:** Add auto-generation logic for dynamic sections on report creation using applicable templates (global, branch-specific, department-specific) and ensure alignment with report company/branch/department. *Files:* `models/report_models.py` (or related). *Depends on:* TASK-04. (~100–200 LOC)
- **TASK-06:** Create security groups, access control lists, and record rules for Branch Manager/Department Head and Reporting Admin; ensure submitted records are read-only for non-admins; include rules for dynamic sections and templates. *Files:* `security/ir.model.access.csv`, `security/reporting_security.xml`. *Depends on:* TASK-02, TASK-04. (~100–200 LOC)
- **TASK-07:** Build views for reports (form/list/search) including dynamic sections inline, universal sections, metric lines, communication buttons, and menu actions. *Files:* `views/report_views.xml`, `views/menu.xml`. *Depends on:* TASK-03, TASK-05, TASK-06. (~100–200 LOC)
- **TASK-08:** Add dashboards (graph/pivot) for complaints, staff issues, contractor ratings, metrics, and dynamic sections with branch/global menu separation and default filters. *Files:* `views/dashboard_views.xml`, `views/menu.xml`. *Depends on:* TASK-07. (~100–200 LOC)
- **TASK-09:** Create configuration views for KPI metric templates and dynamic section templates with filtering by branch/company/department and sequence ordering. *Files:* `views/metric_template_views.xml`, `views/section_template_views.xml`. *Depends on:* TASK-04, TASK-06. (~100–200 LOC)
- **TASK-10:** Implement QWeb PDF report templates and report actions for Daily Manager Report (and optional contractor summary) including dynamic sections; ensure company header usage and branding. *Files:* `reports/report_templates.xml`, `reports/report_actions.xml`. *Depends on:* TASK-07. (~100–200 LOC)
- **TASK-11:** Add demo/sample data for metric templates, dynamic section templates, and example reports ensuring multi-company and department coverage (optional but useful for testing). *Files:* `data/demo.xml`. *Depends on:* TASK-10. (~100–200 LOC)
- **TASK-12:** Update README/documentation describing module purpose, models, workflows, dashboards, dynamic sections, and installation notes. *Files:* `README.md` (module-specific). *Depends on:* completion of prior tasks. (~100–200 LOC)

