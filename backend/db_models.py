"""SQLAlchemy models.

Design notes
- Every complaint is preserved forever. Grouping only sets Complaint.group_id.
- A new complaint starts in its own single-complaint group. The AI only creates
  Suggestion rows ("this complaint may belong in that group"). Nothing is merged
  until an authorised staff member confirms it.
"""
import json
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


STATUSES = ["Submitted", "Under AI Analysis", "Awaiting Staff Review", "In Progress", "Resolved"]
STAFF_STATUSES = ["Awaiting Staff Review", "In Progress", "Resolved"]
OPEN_STATUSES = ["Submitted", "Under AI Analysis", "Awaiting Staff Review"]
PRIORITIES = ["low", "medium", "high"]
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}
ROLES = ("user", "staff", "admin")


class Organization(db.Model):
    __tablename__ = "organizations"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), unique=True, nullable=False)
    sector = db.Column(db.String(40), nullable=False, index=True)   # education, corporate, ...
    city = db.Column(db.String(80), default="")
    locations_json = db.Column(db.Text, default="[]")
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def locations(self):
        try:
            return json.loads(self.locations_json or "[]")
        except ValueError:
            return []

    @locations.setter
    def locations(self, value):
        self.locations_json = json.dumps(list(value or []))


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(300), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="user")       # user | staff | admin
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    organization = db.relationship("Organization")

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class ComplaintGroup(db.Model):
    __tablename__ = "complaint_groups"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, default="")
    priority = db.Column(db.String(10), default="medium")
    priority_locked = db.Column(db.Boolean, default=False)   # True once staff set it manually
    status = db.Column(db.String(30), default="Submitted", index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow)

    complaints = db.relationship("Complaint", back_populates="group")
    events = db.relationship("GroupEvent", back_populates="group", cascade="all, delete-orphan",
                             order_by="GroupEvent.at.desc(), GroupEvent.id.desc()")
    notes = db.relationship("GroupNote", back_populates="group", cascade="all, delete-orphan",
                            order_by="GroupNote.created_at.desc(), GroupNote.id.desc()")


class Complaint(db.Model):
    __tablename__ = "complaints"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("complaint_groups.id"), index=True)

    category = db.Column(db.String(60), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    location = db.Column(db.String(120), nullable=False)
    severity = db.Column(db.String(10), nullable=False, default="medium")   # what the complainant chose
    priority = db.Column(db.String(10), nullable=False, default="medium")   # mirrors the group's priority
    status = db.Column(db.String(30), nullable=False, default="Submitted", index=True)
    image_filename = db.Column(db.String(80))

    embedding = db.Column(db.LargeBinary)                  # float32 vector (384 dims for the supplied model)
    embedding_model = db.Column(db.String(80), index=True)

    rating = db.Column(db.Integer)
    feedback_comment = db.Column(db.Text)
    feedback_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow)

    organization = db.relationship("Organization")
    user = db.relationship("User")
    group = db.relationship("ComplaintGroup", back_populates="complaints")
    history = db.relationship("StatusHistory", back_populates="complaint", cascade="all, delete-orphan",
                              order_by="StatusHistory.at, StatusHistory.id")


class Suggestion(db.Model):
    """AI proposal: `complaint` may belong in `group`. Needs staff confirmation."""
    __tablename__ = "suggestions"
    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("complaints.id"), nullable=False, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("complaint_groups.id"), nullable=False, index=True)
    score = db.Column(db.Float, nullable=False)            # cosine similarity, NOT a probability
    status = db.Column(db.String(12), nullable=False, default="pending", index=True)  # pending|confirmed|rejected|superseded
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (db.UniqueConstraint("complaint_id", "group_id", name="uq_suggestion_pair"),)

    complaint = db.relationship("Complaint")
    group = db.relationship("ComplaintGroup")


class StatusHistory(db.Model):
    __tablename__ = "status_history"
    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("complaints.id"), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False)
    at = db.Column(db.DateTime, default=utcnow)
    complaint = db.relationship("Complaint", back_populates="history")


class GroupEvent(db.Model):
    __tablename__ = "group_events"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("complaint_groups.id"), nullable=False, index=True)
    action = db.Column(db.String(200), nullable=False)
    by_name = db.Column(db.String(120))
    detail = db.Column(db.String(300))
    at = db.Column(db.DateTime, default=utcnow)
    group = db.relationship("ComplaintGroup", back_populates="events")


class GroupNote(db.Model):
    __tablename__ = "group_notes"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("complaint_groups.id"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    author_name = db.Column(db.String(120))
    text = db.Column(db.Text, nullable=False)
    is_internal = db.Column(db.Boolean, default=True)      # False = visible to complainants
    created_at = db.Column(db.DateTime, default=utcnow)
    group = db.relationship("ComplaintGroup", back_populates="notes")


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    message = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(200))
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)


class Announcement(db.Model):
    __tablename__ = "announcements"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), index=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=utcnow)


class FAQ(db.Model):
    __tablename__ = "faqs"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), index=True)  # NULL = shown everywhere
    question = db.Column(db.String(240), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, default=0)


class AppMeta(db.Model):
    __tablename__ = "app_meta"
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(200))
