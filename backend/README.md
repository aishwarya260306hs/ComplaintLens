# ComplaintLens: Flask backend + frontend + fine-tuned Sentence Transformer

One runnable project: the redesigned frontend (`templates/`, `static/`), a Flask + SQLAlchemy + SQLite
backend, and the supplied fine-tuned Sentence Transformer at
`../ai_model/models/hacknetix_sentence_transformer/` for semantic similarity.

## Run it
Run these commands from the `backend/` directory (Windows):
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# Check that the configured local model loads.
flask --app app model-info
# Create tables + REVA University, Bengaluru.
flask --app app seed
# Add the replaceable, non-exhaustive organization directory sample.
flask --app app import-organizations
# Create a staff account for REVA (staff are never self-registered).
flask --app app create-staff --org-id 1
# Start at http://127.0.0.1:5000.
flask --app app run --debug
```
The model-info command should report `hacknetix_sentence_transformer` and its loaded embedding dimension.
Students and employees register at `/register` and select their user type during complaint intake.
Optional sample data: `flask --app app seed --demo`
(the UI then labels statistics as sample data).

`backend/data/organizations.json` is a small representative sample, not a complete official directory.
It contains source links and dates where available; it does not assert that linked source material is
openly licensed. `import-organizations` is explicit and idempotent: it inserts missing organizations
without changing existing organizations, complaints, or their embeddings. Replace or import a verified
dataset with `flask --app app import-organizations --file path\to\organizations.json`.

Production: `waitress-serve --call --threads=4 app:create_app` (one process only: the model lives in memory).

## How the ML connects
1. A complaint is saved first, so it is never lost.
2. `similarity.py` encodes `title + description` with the supplied fine-tuned model (offline, CPU).
3. Cosine similarity is computed against complaints in the SAME organization that sit in other groups.
   Groups at or above the existing cutoff become `Suggestion` rows (max 5). The existing 0.60 cutoff
   is retained for compatibility; it has not been calibrated for this fine-tuned model and is not an
   accuracy claim. Evaluate it on held-out complaint data before relying on it.
4. Nothing is merged automatically. Staff Confirm or Reject on the staff pages. Confirm moves the complaint
   into the group; every individual complaint is preserved. Scores are shown as "similarity", never probability.
5. Embeddings are stored with the model name. If you ever change model, run `flask --app app reindex`.
6. If the model cannot be loaded the app refuses to start with a clear message (no silent downgrade).
   `ALLOW_FALLBACK_EMBEDDER=1` is a development-only stand-in and is NOT the supplied model.

Changing the model identity does not update existing embeddings automatically. After backing up the
database, explicitly reindex with `flask --app app reindex` from `backend/` if you want stored
complaint embeddings recalculated with the supplied model.

## Roles and access control
- `user`: sees only their own complaints (other IDs return 404); submissions may target any listed organization.
- `staff`: scoped to their organization; reviews suggestions, changes status/priority, adds notes.
- `admin`: all organizations. Create with `flask --app app create-admin`.
- Flask-Login sessions, hashed passwords, CSRF token on every POST/PATCH, login throttling,
  upload type sniffing (PNG/JPG/WebP, 5 MB) and access-checked image serving.
- Student/employee is a complaint-intake choice, not a permission role. Users may submit to listed
  organizations; complaint detail remains visible only to its submitter and that organization's staff.
- Similarity scores, pair comparisons, suggested/confirmed groups, and organizer metrics are staff/admin
  only. Public and user endpoints do not expose those analytics. No labeled held-out evaluation dataset
  is configured, so the staff dashboard reports "Evaluation data unavailable."

## Your existing database
This backend defines its own schema (`db_models.py`) and creates it with `db.create_all()`.
It does NOT migrate an older HACKNETIX database. Point `DATABASE_URL` at a fresh file, or adapt the
models to your old table and column names.

## Tests
`python -m pytest -q tests` runs the full journey (register, submit, AI suggestion, staff confirm,
status updates, feedback, reopen, cross-organization access) with the development fallback embedder.
`$env:HX_TEST_REAL_MODEL=1; python -m pytest -q tests` uses the supplied model in PowerShell.

## Endpoint map
Pages: `/ /organizations /dashboard /complaints/new /complaints/<id>/analysis /my-complaints /complaints/<id> /notifications /staff /staff/groups/<id> /login /register /logout`

API (JSON): `/api/stats /organizations /categories /dashboard /complaints /complaints/<id> /complaints/<id>/analysis /complaints/<id>/image /complaints/<id>/feedback /complaints/<id>/reopen /notifications /notifications/read /staff/summary /staff/groups /staff/suggestions /staff/suggestions/<id>/confirm|reject /groups/<id> (GET, PATCH) /staff/announcements /health`

CLI: `seed [--demo] import-organizations [--file ...] add-org create-staff create-admin reanalyze reindex model-info`
