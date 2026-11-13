# Sales Target Check

Salespeople can see their own monthly progress. Sales managers can see totals and individual progress for their active direct reports. The dashboard shows the target, confirmed order value, attainment, and remaining value in MYR.

## Run the demo

With Python 3.12, run these commands from the repository root. Select a demo profile in the sidebar to try both roles.

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

The demo creates fictional records in memory. Refresh rereads the selected source for the current month or the selected past month. The app uses Malaysia calendar dates.

## Deploy on Streamlit Community Cloud

After the files are on GitHub, select `jasmineithq-collab/Self-Check-target`, branch `main`, and entry point `app.py`. Choose Python 3.12. With default settings, the deployment shows the demo. [Streamlit deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)

For real figures, configure the data source and Google OpenID Connect first. Put the settings below in Streamlit's **Secrets** panel, or in `.streamlit/secrets.toml` for local testing. Register the exact callback URL with Google. For local testing it is `http://localhost:8501/oauth2callback`. [Google sign-in setup](https://docs.streamlit.io/develop/tutorials/authentication/google)

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

The live adapter expects a private SQLite snapshot accessible on the app host. A source integration needs to publish refreshed snapshots before the app can show real figures on Community Cloud. Keep real snapshots and secrets outside Git. The reference schema is in `mcp_db_server.py`.

| Table | Required fields |
| --- | --- |
| `staff` | `staff_id`, verified login `email`, `name`, `role` (`salesperson` or `sales_manager`), `manager_id`, `active` |
| `sales_targets` | `staff_id`, `month_start` (first day as `YYYY-MM-DD`), `target_sen` |
| `sales_orders` | `order_id`, `staff_id`, `order_date` (`YYYY-MM-DD` in Malaysia time), `status`, `amount_sen` |
| `sales_sync` | One row with `id = 1` and `updated_at` identifying the last successful source sync |

Only orders with status `confirmed` count. The app sums confirmed orders through the current date for this month and through the last day for earlier months. Staff without a target still see order totals. When a direct report has no target, the team target and attainment are unavailable and individual figures remain visible.

## Optional AI assistant

Set `GOOGLE_API_KEY` at the top level of Streamlit secrets to enable Gemini. You can set `GEMINI_MODEL` there to override the default model. Gemini receives the submitted question and the displayed aggregate figures for the selected period. Staff names and individual order records are excluded from the app's AI context.
