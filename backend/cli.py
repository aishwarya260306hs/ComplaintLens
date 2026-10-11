"""Management commands:  flask --app app <command>

  seed [--demo]       create REVA University + FAQs (+ sample complaints with --demo)
  import-organizations
                      idempotently add organizations from the replaceable JSON directory
  add-org             add another organization
  create-staff        create a staff account bound to an organization
  create-admin        create an admin account
  reanalyze           finish AI analysis for complaints stuck in "Under AI Analysis"
  reindex             re-embed every complaint with the currently loaded model
  model-info          show which embedding model is active
"""
import secrets
from pathlib import Path

import click
from flask.cli import with_appcontext
from sqlalchemy import func

import services
import similarity
from db_models import FAQ, Announcement, Complaint, Organization, User, db
from organization_data import DEFAULT_DATASET, load_dataset


def seed_base():
    """Idempotent: creates REVA University, Bengaluru and some FAQs."""
    org = Organization.query.filter_by(name="REVA University").first()
    if not org:
        org = Organization(name="REVA University", sector="education", city="Bengaluru")
        org.locations = ["Main Campus", "Hostel Block", "Library", "Sports Complex", "Cafeteria"]
        db.session.add(org)
        db.session.flush()
    if not FAQ.query.first():
        faqs = [
            ("How does ComplaintLens detect duplicate complaints?",
             "A local AI model compares the meaning of your complaint with existing ones and suggests similar issues. "
             "Authorised staff confirm or reject every suggestion. Your own complaint is always kept."),
            ("Does a similarity score mean my complaint is a duplicate?",
             "No. The score shows how close the wording and meaning are. It is not a probability and only a suggestion."),
            ("How do I track my complaint?", "Open My Complaints to see its status, timeline and any staff updates."),
            ("Can I add a photo?", "Yes. PNG, JPG or WebP up to 5 MB."),
        ]
        for i, (q, a) in enumerate(faqs):
            db.session.add(FAQ(organization_id=None, question=q, answer=a, position=i))
    if not Announcement.query.filter_by(organization_id=org.id).first():
        db.session.add(Announcement(organization_id=org.id, title="Complaint portal is live",
                                    body="Raise issues here and track them until they are resolved."))
    db.session.commit()
    return org


DEMO_COMPLAINTS = [
    ("Infrastructure", "Water leakage in Block B corridor",
     "Water is continuously leaking from the ceiling in the second floor corridor of Block B and the floor is slippery.", "Hostel Block", "high"),
    ("Infrastructure", "Ceiling dripping near Block B stairs",
     "The ceiling in the Block B second floor corridor drips water, especially after rain. Students are slipping on the wet floor.", "Hostel Block", "high"),
    ("IT & Network", "Wi-Fi keeps disconnecting in library",
     "The Wi-Fi in the library disconnects every few minutes and the speed is very slow during exam weeks.", "Library", "medium"),
    ("IT & Network", "Very slow internet in library reading hall",
     "Internet in the library reading hall is extremely slow and the wireless network keeps dropping.", "Library", "medium"),
    ("Canteen & Food", "Hygiene concerns at the cafeteria",
     "The cafeteria counters and tables are not cleaned properly and food is often left uncovered.", "Cafeteria", "medium"),
    ("Infrastructure", "Broken benches at sports complex",
     "Several benches near the basketball court are broken and have sharp edges.", "Sports Complex", "low"),
]


def seed_demo(org):
    password = secrets.token_urlsafe(9)
    demo = User.query.filter_by(email="demo.student@example.com").first()
    if not demo:
        demo = User(name="Demo Student", email="demo.student@example.com", role="user", organization_id=org.id)
        demo.set_password(password)
        db.session.add(demo)
        db.session.commit()
        click.echo(f"Demo user  demo.student@example.com / {password}")
    for cat, title, desc, loc, sev in DEMO_COMPLAINTS:
        services.create_complaint(demo, org, category=cat, title=title, description=desc, location=loc, severity=sev)
    services.meta_set("sample_data", "1")      # the UI will label statistics as sample data
    db.session.commit()


def _create_account(name, email, role, org_id, password):
    if User.query.filter_by(email=email.lower()).first():
        raise click.ClickException("That email already exists.")
    u = User(name=name, email=email.lower(), role=role, organization_id=org_id)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return u


def register(app):
    @app.cli.command("seed")
    @click.option("--demo", is_flag=True, help="Also add clearly-labelled sample complaints and a demo user.")
    @with_appcontext
    def seed(demo):
        org = seed_base()
        click.echo(f"Organization ready: {org.name} (id={org.id})")
        if demo:
            seed_demo(org)
            click.echo("Sample complaints added. The UI labels statistics as sample data.")

    @app.cli.command("add-org")
    @click.option("--name", prompt=True)
    @click.option("--sector", prompt="Sector (education/corporate/government/healthcare/transport/housing)")
    @click.option("--city", prompt=True)
    @click.option("--locations", default="", help="Comma-separated locations")
    @with_appcontext
    def add_org(name, sector, city, locations):
        o = Organization(name=name.strip(), sector=sector.strip().lower(), city=city.strip())
        o.locations = [x.strip() for x in locations.split(",") if x.strip()]
        db.session.add(o)
        db.session.commit()
        click.echo(f"Created organization id={o.id}")

    @app.cli.command("import-organizations")
    @click.option("--file", "dataset_file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                  default=DEFAULT_DATASET, show_default=True)
    @with_appcontext
    def import_organizations(dataset_file):
        try:
            dataset = load_dataset(dataset_file)
        except (OSError, ValueError) as exc:
            raise click.ClickException(f"Could not import organization dataset: {exc}") from exc

        added = 0
        for item in dataset["organizations"]:
            existing = Organization.query.filter(
                func.lower(Organization.name) == item["name"].strip().lower(),
                func.lower(Organization.sector) == item["sector"].strip().lower(),
                func.lower(Organization.city) == item["city"].strip().lower(),
            ).first()
            if existing:
                continue
            db.session.add(Organization(name=item["name"].strip(), sector=item["sector"].strip().lower(),
                                        city=item["city"].strip(), locations=[]))
            added += 1
        db.session.commit()
        click.echo(f"Added {added} organization(s); existing organization records were left unchanged.")

    @app.cli.command("create-staff")
    @click.option("--name", prompt=True)
    @click.option("--email", prompt=True)
    @click.option("--org-id", type=int, prompt="Organization id")
    @click.password_option()
    @with_appcontext
    def create_staff(name, email, org_id, password):
        if not db.session.get(Organization, org_id):
            raise click.ClickException("No such organization id.")
        u = _create_account(name, email, "staff", org_id, password)
        click.echo(f"Staff account created: {u.email}")

    @app.cli.command("create-admin")
    @click.option("--name", prompt=True)
    @click.option("--email", prompt=True)
    @click.password_option()
    @with_appcontext
    def create_admin(name, email, password):
        u = _create_account(name, email, "admin", None, password)
        click.echo(f"Admin account created: {u.email}")

    @app.cli.command("reanalyze")
    @with_appcontext
    def reanalyze():
        stuck = Complaint.query.filter_by(status="Under AI Analysis").all()
        for c in stuck:
            services.finish_analysis(c)
            db.session.commit()
        click.echo(f"Re-analysed {len(stuck)} complaint(s).")

    @app.cli.command("reindex")
    @with_appcontext
    def reindex():
        emb = similarity.get_embedder()
        rows = Complaint.query.all()
        for i in range(0, len(rows), 64):
            chunk = rows[i:i + 64]
            vecs = emb.encode([services.complaint_text(c) for c in chunk])
            for c, v in zip(chunk, vecs):
                c.embedding, c.embedding_model = v.tobytes(), emb.name
        db.session.commit()
        click.echo(f"Re-embedded {len(rows)} complaint(s) with {emb.name}.")

    @app.cli.command("model-info")
    def model_info():
        e = similarity.get_embedder()
        click.echo(f"Active embedder: {e.name} (dim={e.dim}, default threshold={e.default_threshold})")
