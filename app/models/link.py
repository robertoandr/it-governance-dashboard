"""SQLAlchemy model para gerenciamento de links WAN/Internet."""

from __future__ import annotations

from datetime import UTC, datetime

from app.extensions import db

LINK_TYPES = ("fibra", "mpls", "dedicado", "vdsl", "vsat", "4g", "outros")


class Link(db.Model):
    __tablename__ = "links"

    id: int = db.Column(db.Integer, primary_key=True)
    name: str = db.Column(db.String(120), nullable=False)
    ip: str = db.Column(db.String(45), nullable=False, unique=True)
    cidr: str = db.Column(db.String(50), nullable=False)
    provider: str = db.Column(db.String(120), nullable=False, default="")
    circuit_id: str = db.Column(db.String(120), nullable=False, default="")
    link_type: str = db.Column(
        db.String(20),
        nullable=False,
        default="dedicado",
    )
    bandwidth_mbps: float = db.Column(db.Float, nullable=True)
    zabbix_hostid: str = db.Column(db.String(20), nullable=True)
    active: bool = db.Column(db.Boolean, nullable=False, default=True)
    notes: str = db.Column(db.Text, nullable=False, default="")
    created_at: datetime = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: datetime = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "ip": self.ip,
            "cidr": self.cidr,
            "provider": self.provider,
            "circuit_id": self.circuit_id,
            "link_type": self.link_type,
            "bandwidth_mbps": self.bandwidth_mbps,
            "zabbix_hostid": self.zabbix_hostid,
            "active": self.active,
            "notes": self.notes,
        }
