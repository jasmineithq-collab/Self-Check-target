# Sales Target Check

Salespeople see their monthly orders in their assigned areas. DSMs see orders and targets for their active direct reports. The dashboard compares confirmed orders against the monthly target in MYR and shows order lines by customer, item category and item name.

## Run the demo

With Python 3.12, run these commands from the repository root. Enter a staff code and select **Continue**. Use **Switch user** to try another role.

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Place the roster at `private/staff list.xlsx` or set `STAFF_ROSTER_PATH` to a private copy. Use the `DSM Code` or `Salesman Code` from its `DSM Management` sheet. The app assigns sample areas because the sheet has no area field. A clean checkout accepts `JEWD` and `SADSM1` as demo IDs with fictional names and team data. `REP001` and `DSM001` also work in the built-in demo.

ID entry checks roster membership for the presentation. Verified identity is required in live-data mode. For each salesperson, each demo month has 17 orders across five customers, including 12 confirmed orders, three pending orders and two cancelled orders. Ten orders have invoices with a mix of delivery and POD statuses. The app uses Malaysia calendar dates.

## Deploy on Streamlit Community Cloud

Select `jasmineithq-collab/Self-Check-target`, branch `main`, and entry point `app.py`. Choose Python 3.12. The default deployment uses the fictional roster. To present with the supplied roster in a private Streamlit deployment, set `STAFF_ROSTER_XLSX_B64` to the base64-encoded workbook in the **Secrets** panel. The private local copy is ignored by Git. [Streamlit deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)

On Windows, run this from the repository root to copy the complete secrets entry to the clipboard, then paste it into the private app's **Secrets** panel.

```powershell
$rosterPath = (Resolve-Path -LiteralPath 'private/staff list.xlsx').Path
$encodedRoster = [Convert]::ToBase64String([IO.File]::ReadAllBytes($rosterPath))
('STAFF_ROSTER_XLSX_B64 = "' + $encodedRoster + '"') | Set-Clipboard
```

For real figures, configure the data source and Google OpenID Connect. The verified email must match the active account for the entered staff ID. Put the settings below in Streamlit's **Secrets** panel, or in `.streamlit/secrets.toml` for local testing. Register the exact callback URL with Google. For local testing it is `http://localhost:8501/oauth2callback`. [Google sign-in setup](https://docs.streamlit.io/develop/tutorials/authentication/google)

```toml
SALES_MODE = "live"
SALES_DB_PATH = "/path/to/private/sales.sqlite"

[auth]
redirect_uri = "https://YOUR-APP.streamlit.app/oauth2callback"
cookie_secret = "REPLACE_WITH_A_RANDOM_SECRET"
client_id = "REPLACE_WITH_GOOGLE_CLIENT_ID"
client_secret = "REPLACE_WITH_GOOGLE_CLIENT_SECRET"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

## Data source

The live adapter expects a private SQLite snapshot accessible on the app host. To use the real Excel or CSV exports, map their columns and arrange for refreshed snapshots. Keep real snapshots and secrets outside Git. The reference schema is in `mcp_db_server.py`.

| Table | Required fields |
| --- | --- |
| `staff` | `staff_id`, verified login `email`, `name`, `role` (`salesperson` or `sales_manager`), `manager_id`, `active` |
| `sales_targets` | `staff_id`, `month_start` (first day as `YYYY-MM-DD`), `target_sen` |
| `sales_orders` | `order_id`, `staff_id`, `order_date` (`YYYY-MM-DD` in Malaysia time), `status`, `amount_sen` |
| `staff_areas` | `staff_id`, `area` |
| `order_details` | `order_id`, `area`, `customer`, `invoice`, `pod_status`, `pod_reference` |
| `order_items` | `order_id`, `line_number`, `item_category`, `item_name`, `amount_sen` |
| `sales_sync` | One row with `id = 1` and `updated_at` identifying the last successful source sync |

Only orders with status `confirmed` count. The app sums confirmed orders through the current date for this month and through the last day for earlier months. Staff without a target still see order totals. When a direct report has no target, the team target and attainment are unavailable and individual figures remain visible.

## Assistants

After signing in, type a question in **Ask an assistant**. The app shows the answer for the selected month and your access scope. Turn on **Show all** to see the full dashboard with targets, team progress where applicable, and order lines. The guardrail checks questions before routing them and identifies questions outside the supported topics. The demo requires no API key.

- **Sales** answers questions about order totals and statuses, progress against target, customer breakdowns and sales invoices. Invoice values are the linked order amounts in this demo.
- **HR** explains the sample commission policy and calculates the example amount for the visible salesperson or team. Live mode requires an approved policy before calculating commission.
- **Logistics** looks up POD status and reference by invoice number within the selected month and the user's access scope.

Set `GOOGLE_API_KEY` in Streamlit secrets to enable an optional Gemini summary for general Sales questions. Set `GEMINI_MODEL` to override the model. Gemini receives the visible aggregate figures and a fixed summary task. Staff names, customer names, invoice details and the typed question stay in the app.
