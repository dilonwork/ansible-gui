"""SQLAlchemy models. Skeleton stage: plain columns, no migrations yet.

NOTE: hosts.private_key is stored in cleartext for now.
Encrypting it at rest (Vault / M7 credentials) is the next security step.
"""
from sqlalchemy import JSON, Boolean, Column, Float, Integer, String, Text

from .db import Base


class Host(Base):
    __tablename__ = "hosts"
    id = Column(String(16), primary_key=True)
    name = Column(String(128), nullable=False)
    address = Column(String(256), nullable=False)
    port = Column(Integer, default=22)
    username = Column(String(64), default="root")
    private_key = Column(Text, nullable=False)
    host_key = Column(Text, nullable=False)
    added_at = Column(Float)


class Playbook(Base):
    __tablename__ = "playbooks"
    id = Column(String(16), primary_key=True)
    name = Column(String(128), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(Float)


class Template(Base):
    __tablename__ = "templates"
    id = Column(String(16), primary_key=True)
    name = Column(String(128), nullable=False)
    playbook_id = Column(String(16), nullable=False)
    host_ids = Column(JSON, default=list)
    extra_vars = Column(JSON, default=dict)
    check_mode = Column(Boolean, default=False)
    notification_policy = Column(String(16), default="failure_only")
    # always | failure_only | never  (M8.1)
    created_at = Column(Float)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String(16), primary_key=True)
    host_ids = Column(JSON, default=list)
    status = Column(String(16), default="running")
    snapshot = Column(JSON, default=dict)
    events = Column(JSON, default=list)
    schedule_id = Column(String(16), nullable=True)  # set for scheduled runs
    created_at = Column(Float)
    finished_at = Column(Float, nullable=True)


class MaintenanceRun(Base):
    """One-click rolling node maintenance (M5).

    The orchestrator (app/maintenance_tasks.py) walks nodes strictly in order
    (serial=1). Per-node step states live in ``steps`` JSON; the run pauses on
    the first node failure and waits for an operator decision
    (retry-node / skip-node / abort). Completed nodes are never re-run.
    """
    __tablename__ = "maintenance_runs"
    id = Column(String(16), primary_key=True)
    name = Column(String(128), nullable=False)
    workflow = Column(String(32), nullable=False)  # os-patch | kubelet-upgrade
    node_ids = Column(JSON, default=list)  # ordered host ids
    status = Column(String(16), default="running")
    # running|pausing|paused|failed|completed|aborted
    steps = Column(JSON, default=list)      # per-node step records
    preflight = Column(JSON, default=list)  # preflight check results
    snapshot = Column(JSON, default=dict)   # frozen workflow + params
    kubeconfig = Column(Text, nullable=True)  # Fernet-encrypted, optional
    events = Column(JSON, default=list)
    created_at = Column(Float)
    finished_at = Column(Float, nullable=True)


class Schedule(Base):
    """A cron schedule that launches a job template on a recurring basis.

    next_run_at is persisted so the cadence survives backend restarts.
    Missed occurrences (backend down) are counted in missed_count instead of
    being silently skipped; at most one catch-up run fires per tick.
    """
    __tablename__ = "schedules"
    id = Column(String(16), primary_key=True)
    name = Column(String(128), nullable=False)
    template_id = Column(String(16), nullable=False)
    cron = Column(String(64), nullable=False)          # 5-field cron
    timezone = Column(String(64), default="UTC")
    enabled = Column(Boolean, default=True)
    next_run_at = Column(Float, nullable=True)         # UTC epoch
    last_run_at = Column(Float, nullable=True)
    last_job_id = Column(String(16), nullable=True)
    last_status = Column(String(32), nullable=True)
    missed_count = Column(Integer, default=0)
    recent_missed = Column(JSON, default=list)         # last missed UTC epochs
    skipped_overlap = Column(Integer, default=0)
    created_at = Column(Float)


class NotificationChannel(Base):
    """Outbound notification channel (M8.1). v1: webhook only."""
    __tablename__ = "notification_channels"
    id = Column(String(16), primary_key=True)
    name = Column(String(128), nullable=False)
    type = Column(String(16), default="webhook")  # webhook | line | slack (later)
    config = Column(JSON, default=dict)  # webhook: {url, headers}
    enabled = Column(Boolean, default=True)
    created_at = Column(Float)


class Cluster(Base):
    """A Kubernetes cluster onboarded via kubeconfig (M4.1).

    The kubeconfig is Fernet-encrypted at rest and never returned by the API.
    """
    __tablename__ = "clusters"
    id = Column(String(16), primary_key=True)
    name = Column(String(128), nullable=False)
    kubeconfig = Column(Text, nullable=False)  # encrypted
    server = Column(String(256), nullable=True)
    k8s_version = Column(String(32), nullable=True)
    status = Column(String(16), default="unknown")  # ok | error | unknown
    last_error = Column(Text, nullable=True)
    last_sync_at = Column(Float, nullable=True)
    created_at = Column(Float)


class User(Base):
    """Local user account (auth/RBAC). Passwords are bcrypt hashes."""
    __tablename__ = "users"
    id = Column(String(16), primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(16), default="operator")  # admin | operator | viewer
    created_at = Column(Float)


class Session(Base):
    """Opaque login token with expiry; revocable on logout."""
    __tablename__ = "sessions"
    token = Column(String(64), primary_key=True)
    user_id = Column(String(16), nullable=False)
    created_at = Column(Float)
    expires_at = Column(Float)
