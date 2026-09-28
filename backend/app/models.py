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
    created_at = Column(Float)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String(16), primary_key=True)
    host_ids = Column(JSON, default=list)
    status = Column(String(16), default="running")
    snapshot = Column(JSON, default=dict)
    events = Column(JSON, default=list)
    created_at = Column(Float)
    finished_at = Column(Float, nullable=True)
