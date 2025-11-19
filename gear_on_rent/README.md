# Gear On Rent (RMC Production Flow)

This module refactors the legacy rental daily log into an Odoo 19 MRP focused workflow tailored for ready-mix concrete (RMC) batching plants. It links sales contracts, MRP work orders, telemetry driven dockets, NGT/LOTO relief, month-end billing, and portal reporting in a single stack.

## End-to-End Flow

1. **Sale Order**
   - Flag RMC products with `RMC Production Item` and (optionally) a default work center.
   - On confirmation, `x_billing_category` switches to `rmc`, MGQ & contract dates default, and a primary work center is selected.

2. **Monthly Work Order**
   - The contract spawns one `gear.rmc.monthly.order` per calendar month in the contract window.
   - Daily manufacturing orders (`mrp.production`) are generated with `x_daily_target_qty`.

3. **Daily Work Orders (MRP)**
   - Work orders (`mrp.workorder`) are automatically split into ≤ 7 m³ chunks (configurable via `gear_on_rent.workorder_max_qty`).
   - Tabs show live recipe selection, aggregated batches, and linked dockets.
   - A draft docket is auto-created alongside each work order.

4. **Dockets**
   - `gear.rmc.docket` records capture IDS telemetry, manual edits, recipe lines, batching, operator notes, and attachments.
   - Batch generation occurs when a recipe and batching capacity are set; totals are visible within the docket and work order tabs.

5. **Telemetry (IDS)**
   - `/ids/workcenter/update` ingests payloads (`workcenter_external_id`, produced m³, runtime/idle, alarms).
   - The controller resolves the current work order, updates metrics, and appends a docket entry.

6. **NGT / LOTO Integration**
   - Approving downtime requests pro-rates MGQ, sets wave-off vs. chargeable hours, and records relief against MOs and the monthly order.

7. **Billing & Reporting**
   - `gear.prepare_invoice_from_mrp` wizard builds a monthly invoice from dockets (prime output, optimized standby, NGT, LOTO wave-off overs).
   - QWeb month-end report mirrors the invoice and attaches to the move, highlighting wave-off summaries and production KPIs.

8. **Portal / Website**
   - Public `/gear-on-rent` page remains intact; portal endpoints surface dockets/work orders with partner filtering.

## Key Features

- Work order ↔ equipment mapping (work center `x_equipment_id`).
- RMC billing fields on sale order (`x_monthly_mgq`, `x_loto_waveoff_hours`, contract window).
- Auto docket creation aligned to work order quantity.
- Recipe & batch management with material variance tracking.
- IDS telemetry ingestion with chatter notes and alarm storage.
- NGT/LOTO relief propagation + wave-off accounting.
- Month-end invoice + report generator using dockets instead of daily logs.
- Configurable 7 m³ cap via `Settings → Technical → Parameters → System Parameters → gear_on_rent.workorder_max_qty`.

## Configuration Checklist

1. **Products**
   - Mark RMC items with `RMC Production Item`.
   - Optionally set `Default Work Center` on the product template.

2. **Work Centers**
   - Map `Equipment` (custom Many2one) and ensure external IDs align with IDS payloads.

3. **Sale Order Template**
   - Confirm MGQ, contract dates, and wave-off allowance.

4. **IDS Integration**
   - Define system parameter `gear_on_rent.ids_token`.
   - Point IDS gateway to `/ids/workcenter/update` with the token in headers.

5. **Security**
   - Assign `gear_on_rent` user groups to responsible staff.
   - Ensure portal rules limit visibility by partner/site.

## Usage Guide

1. Confirm an RMC sale order.
2. Review generated monthly order → `Generate Daily Orders`.
3. Check work orders (RMC Dockets tab) – each chunk (≤ 7 m³) has a draft docket.
4. Enter recipe & capacity once available; batches auto-populate, and totals show at the bottom of the tab.
5. Allow IDS telemetry to overwrite/add dockets as production progresses.
6. Approve NGT/LOTO downtime from respective menus; mgq relief is recorded automatically.
7. Run the “Prepare RMC Invoice” wizard at month-end; verify the generated PDF report is attached to the invoice.

## Batch Totals

Both the docket form and work order batch tabs display summed totals for:

- Quantity Ordered (m³)
- 10 mm aggregate
- 20 mm aggregate
- FACS
- Water / WaterR
- Flyash
- Admixture

These totals automatically recalculate as batches are edited or regenerated.

## Testing Recommendations

- Execute targeted tests with `./odoo-bin -c odoo.conf -d <db> --test-tags gear_on_rent`.
- Validate IDS webhook using sample payloads (see `controllers/ids.py` docstring).
- Ensure MGQ cap logic creates multiple work orders when daily target > 7 m³.
- Confirm month-end invoice shows expected prime/standby, NGT, and LOTO wave-off data.

## Support & Maintenance

- Update IDS token or MGQ cap via system parameters without code changes.
- Recipe/BOM updates propagate automatically to dockets; regenerate batches if mix design changes mid-month.
- For additional dashboards or custom KPIs, extend `gear.rmc.docket` and reuse the existing notebook tabs.

