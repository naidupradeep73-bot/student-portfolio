# Student Portfolio Builder

A Flask + MongoDB application for creating, publishing, and managing student portfolios.

## Run locally

1. Create a virtual environment and install `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` and set `SECRET_KEY`, `MONGO_URI`, and `MONGO_DB`.
3. Run `flask --app app run --debug`.

MongoDB Atlas is required in normal operation. Configure `ADMIN_EMAIL` and a Werkzeug-generated `ADMIN_PASSWORD_HASH` to enable `/admin/login`; no default administrator account exists.

For a quick local interface demo without MongoDB, set `USE_IN_MEMORY_DB=true` in `.env`. This mode resets data whenever the app restarts and must not be used in production.
