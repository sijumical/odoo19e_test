# Universal Daily Reporting & Performance System

## Overview
Universal Daily Reporting provides a reusable, multi-company daily manager reporting flow for Odoo 19 Enterprise. Managers capture daily activities, complaints, staff logs, contractor performance, configurable KPI metrics, and dynamic branch/department sections with dashboards and printable PDFs. The module is built with native ORM, views, and QWeb—no Studio artifacts.

## Key Features
- **Daily Reports:** Draft/submit workflow with submission metadata and chatter support.
- **Configurable Metrics:** Templates generate KPI metric lines per report (global, branch, or department specific) with type-aware fields.
- **Branch & Company Security:** Reporting Admin (full access) and Branch Manager (branch-limited, draft-only edits) with record rules enforcing company/branch scoping.
- **Dynamic Sections:** Section templates scoped by company/branch/department auto-create report sections for localized tracking.
- **Dashboards:** Graph and pivot dashboards for complaints, staff issues, contractor ratings, numeric metrics, and dynamic sections with branch/global actions.
- **PDF Reports:** QWeb PDFs for daily manager reports (including dynamic sections) and contractor performance summaries.
- **Demo Data:** Sample companies, departments, employees, templates, and reports to explore the module in multi-company context.

## Models
- **daily.manager.report:** Report header (date, company/branch/department, manager, activities, notes, state, submission metadata) with O2M lines.
- **daily.manager.report.complaint:** Complaint lines capturing severity, customer, resolution status, and responsible user.
- **daily.manager.report.staff.log:** Staff/attendance/issues with role, shift, and actions taken.
- **daily.manager.report.contractor.performance:** Contractor ratings/comments with branch alignment for future aggregations.
- **daily.manager.report.metric.line:** KPI metric values aligned to templates with type-specific value fields.
- **daily.manager.metric.template:** Metric definitions (int/float/text/selection), optional company/branch/department targeting, sequence, and default values.
- **daily.manager.section.template:** Dynamic section definitions scoped globally or to company/branch/department.
- **daily.manager.report.section:** Per-report dynamic sections generated from templates with optional employee/partner references.

## Workflow
1. Manager creates a draft report (defaults company/branch/department/manager, auto-generated metric lines and dynamic sections).
2. Fill activities, complaints, staff logs, contractor performance, metrics, and dynamic sections.
3. Submit the report; submission timestamps user and locks editing for branch managers. Reporting Admin can reopen if needed.
4. Use dashboards and PDFs for review, audits, and branch/global oversight.

## Security
- **Groups:** Reporting Admin (full access), Branch Manager/Department Head (company/branch/department scoped, draft-only edits).
- **Record Rules:** Company/branch/department domain filters across all models; branch managers cannot modify submitted reports.
- **ACLs:** CRUD for admins; branch managers restricted per model rules and states.

## Dashboards & Reports
- **Dashboards:** Complaint severity counts, staff issue/attendance analytics, contractor rating averages, numeric KPI pivot/graph, and dynamic section pivots with branch/global menu separation.
- **PDFs:** Daily manager report with sections for activities, complaints, staff logs, contractor performance, metrics, and dynamic sections; contractor performance summary per report.

## Installation
1. Deploy on Odoo 19 Enterprise with dependencies `base`, `mail`, `hr`, and `web` available.
2. Add the module to the addons path and update app list.
3. Install `Universal Daily Reporting & Performance` module.
4. Enable demo data if you want sample records for testing.

## Configuration Tips
- Configure KPI metric templates (global, branch, or department) in Reporting ▸ Configuration ▸ KPI Metrics.
- Configure dynamic section templates (global/branch/department) in Reporting ▸ Configuration ▸ Dynamic Sections.
- Assign users to **Reporting Admin** or **Branch Manager** groups for proper access.
- Set default companies/branches on users to streamline report creation defaults.
