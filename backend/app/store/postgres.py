"""Sketsa repository PostgreSQL async (target produksi).

Mengimplementasikan *surface* yang sama dengan :class:`MemoryRepository`
(``app/store/memory.py``) memakai SQLAlchemy 2.0 async + asyncpg, sehingga
lapisan API tidak berubah sama sekali (Dependency Inversion — lihat
``app/store/__init__.py``). Untuk mengaktifkannya, ganti ``get_repo()`` agar
mengembalikan ``PostgresRepository(session_factory)``; kontrak API/skema tetap.

Status: **sketsa** — skema ORM kanonik (DDL, index, constraint) dimiliki agent
``db-devops-architect`` di ``infra/db/schema.sql``. Modul ini menunjukkan
*bentuk* implementasi (mapping ORM <-> Pydantic, transaksi, cascade) dan
sengaja TIDAK dipakai sebagai default agar backend tetap import-able tanpa
Postgres hidup. Lihat backend/NEEDS.md.

Catatan pemetaan:
- ``interfaces`` disimpan sebagai kolom JSONB pada baris node (topologi adalah
  graph JSON-serializable per MASTER_SPEC §2). Alternatif normalisasi penuh
  (tabel ``interfaces`` terpisah) diserahkan ke schema.sql infra.
- ``config artifacts`` append-only (riwayat), diurut ``generated_at``.

Catatan lingkungan:
- Modul ini dirancang untuk **Python 3.12** (tech-stack WAJIB, MASTER_SPEC §2)
  dengan SQLAlchemy 2.0. Di Python 3.14 (bleeding-edge) terdapat bug resolusi
  tipe pada ``Mapped[X | None]`` di SQLAlchemy 2.0.35 (``make_union_type``),
  sehingga *konfigurasi mapper* (saat import) bisa gagal. Ini TIDAK memengaruhi
  jalur default backend: ``MemoryRepository`` adalah store default dan
  ``from app.main import app`` + seluruh test hijau tanpa modul ini. Aktifkan
  PostgresRepository hanya pada runtime 3.12 sesuai spec.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    delete,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models import (
    ConfigArtifact,
    Link,
    Node,
    Project,
    Scenario,
    Topology,
)
from app.store.memory import NotFound
from app.utils.ids import new_id


# --- ORM (ringkas; DDL kanonik ada di infra/db/schema.sql) ------------------
class Base(DeclarativeBase):
    """Declarative base untuk model ORM NetGeo."""


class ProjectRow(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NodeRow(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    nos: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(8))
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="stopped")
    config_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # topologi graph JSON-serializable (MASTER_SPEC §2)
    interfaces: Mapped[list] = mapped_column(JSON, default=list)
    intent: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class LinkRow(Base):
    __tablename__ = "links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    a_iface: Mapped[str] = mapped_column(String(36), index=True)
    b_iface: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(16))
    bandwidth: Mapped[int] = mapped_column(Integer)
    delay: Mapped[float] = mapped_column(Float)
    loss: Mapped[float] = mapped_column(Float)
    mtu: Mapped[int] = mapped_column(Integer)


class ScenarioRow(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    steps: Mapped[list] = mapped_column(JSON, default=list)
    expected_outcomes: Mapped[list] = mapped_column(JSON, default=list)


class ConfigRow(Base):
    __tablename__ = "config_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nodes.id", ondelete="CASCADE"), index=True
    )
    vendor: Mapped[str] = mapped_column(String(32))
    format: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# --- mappers ORM <-> Pydantic ----------------------------------------------
def _project_out(row: ProjectRow) -> Project:
    return Project(
        id=row.id, name=row.name, description=row.description,
        version=row.version, created_at=row.created_at,
    )


def _node_out(row: NodeRow) -> Node:
    return Node.model_validate({
        "id": row.id, "project_id": row.project_id, "name": row.name,
        "kind": row.kind, "nos": row.nos, "mode": row.mode, "x": row.x, "y": row.y,
        "status": row.status, "config_ref": row.config_ref,
        "interfaces": row.interfaces, "intent": row.intent,
    })


def _link_out(row: LinkRow) -> Link:
    return Link.model_validate({
        "id": row.id, "project_id": row.project_id, "a_iface": row.a_iface,
        "b_iface": row.b_iface, "type": row.type, "bandwidth": row.bandwidth,
        "delay": row.delay, "loss": row.loss, "mtu": row.mtu,
    })


def _config_out(row: ConfigRow) -> ConfigArtifact:
    return ConfigArtifact.model_validate({
        "id": row.id, "node_id": row.node_id, "vendor": row.vendor,
        "format": row.format, "content": row.content, "generated_at": row.generated_at,
    })


class PostgresRepository:
    """Implementasi async dari surface ``MemoryRepository`` di atas SQLAlchemy.

    Setiap method membuka satu transaksi (``async with session.begin()``) agar
    operasi (mis. cascade delete node->link) atomik di sisi DB.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @asynccontextmanager
    async def _txn(self) -> AsyncIterator[AsyncSession]:
        async with self._sf() as session, session.begin():
            yield session

    # --- projects -----------------------------------------------------------
    async def list_projects(self) -> list[Project]:
        async with self._sf() as s:
            rows = (await s.execute(select(ProjectRow))).scalars().all()
            return [_project_out(r) for r in rows]

    async def get_project(self, pid: str) -> Project:
        async with self._sf() as s:
            row = await s.get(ProjectRow, pid)
            if row is None:
                raise NotFound(pid)
            return _project_out(row)

    async def create_project(self, name: str, description: str = "") -> Project:
        proj = Project(id=new_id(), name=name, description=description)
        async with self._txn() as s:
            s.add(ProjectRow(
                id=proj.id, name=proj.name, description=proj.description,
                version=proj.version, created_at=proj.created_at,
            ))
        return proj

    async def topology(self, pid: str) -> Topology:
        async with self._sf() as s:
            prow = await s.get(ProjectRow, pid)
            if prow is None:
                raise NotFound(pid)
            nodes = (await s.execute(
                select(NodeRow).where(NodeRow.project_id == pid)
            )).scalars().all()
            links = (await s.execute(
                select(LinkRow).where(LinkRow.project_id == pid)
            )).scalars().all()
            return Topology(
                project=_project_out(prow),
                nodes=[_node_out(n) for n in nodes],
                links=[_link_out(l) for l in links],
            )

    # --- nodes --------------------------------------------------------------
    async def get_node(self, nid: str) -> Node:
        async with self._sf() as s:
            row = await s.get(NodeRow, nid)
            if row is None:
                raise NotFound(nid)
            return _node_out(row)

    async def add_node(self, node: Node) -> Node:
        async with self._txn() as s:
            s.add(NodeRow(
                id=node.id, project_id=node.project_id, name=node.name,
                kind=str(node.kind), nos=str(node.nos), mode=str(node.mode),
                x=node.x, y=node.y, status=str(node.status), config_ref=node.config_ref,
                interfaces=[i.model_dump() for i in node.interfaces], intent=node.intent,
            ))
        return node

    async def update_node(self, nid: str, patch: dict) -> Node:
        async with self._txn() as s:
            row = await s.get(NodeRow, nid)
            if row is None:
                raise NotFound(nid)
            for key, val in patch.items():
                if val is None:
                    continue
                if key == "interfaces":
                    row.interfaces = [
                        i if isinstance(i, dict) else i.model_dump() for i in val
                    ]
                else:
                    setattr(row, key, val)
            await s.flush()
            return _node_out(row)

    async def delete_node(self, nid: str) -> None:
        async with self._txn() as s:
            row = await s.get(NodeRow, nid)
            if row is None:
                raise NotFound(nid)
            iface_ids = {i["id"] for i in (row.interfaces or [])}
            if iface_ids:
                await s.execute(
                    delete(LinkRow).where(
                        LinkRow.a_iface.in_(iface_ids) | LinkRow.b_iface.in_(iface_ids)
                    )
                )
            await s.delete(row)

    # --- links --------------------------------------------------------------
    async def add_link(self, link: Link) -> Link:
        async with self._txn() as s:
            # Mirror of MemoryRepository.add_link's per-interface guard: one
            # interface may only be an endpoint of one live link at a time.
            # Query-in-txn is enough here (no DB constraint; txn races are
            # tolerated for this app — see memory.py for the invariant note).
            ids = (link.a_iface, link.b_iface)
            rows = (await s.execute(
                select(LinkRow.a_iface, LinkRow.b_iface).where(
                    (LinkRow.project_id == link.project_id)
                    & (LinkRow.a_iface.in_(ids) | LinkRow.b_iface.in_(ids))
                )
            )).all()
            occupied = {i for row in rows for i in row}
            busy = [iid for iid in ids if iid in occupied]
            if busy:
                from app.exceptions.base import Conflict
                raise Conflict(
                    f"interface(s) already in use by another link: {', '.join(busy)}"
                )
            s.add(LinkRow(
                id=link.id, project_id=link.project_id, a_iface=link.a_iface,
                b_iface=link.b_iface, type=str(link.type), bandwidth=link.bandwidth,
                delay=link.delay, loss=link.loss, mtu=link.mtu,
            ))
        return link

    async def get_link(self, lid: str) -> Link:
        async with self._sf() as s:
            row = await s.get(LinkRow, lid)
            if row is None:
                raise NotFound(lid)
            return _link_out(row)

    async def update_link(self, lid: str, patch: dict) -> Link:
        async with self._txn() as s:
            row = await s.get(LinkRow, lid)
            if row is None:
                raise NotFound(lid)
            for key, val in patch.items():
                if val is not None:
                    setattr(row, key, val)
            await s.flush()
            return _link_out(row)

    async def delete_link(self, lid: str) -> None:
        async with self._txn() as s:
            row = await s.get(LinkRow, lid)
            if row is None:
                raise NotFound(lid)
            await s.delete(row)

    # --- scenarios ----------------------------------------------------------
    async def list_scenarios(self, pid: str) -> list[Scenario]:
        async with self._sf() as s:
            rows = (await s.execute(
                select(ScenarioRow).where(ScenarioRow.project_id == pid)
            )).scalars().all()
            return [
                Scenario.model_validate({
                    "id": r.id, "project_id": r.project_id, "name": r.name,
                    "steps": r.steps, "expected_outcomes": r.expected_outcomes,
                })
                for r in rows
            ]

    async def add_scenario(self, scenario: Scenario) -> Scenario:
        async with self._txn() as s:
            s.add(ScenarioRow(
                id=scenario.id, project_id=scenario.project_id, name=scenario.name,
                steps=[st.model_dump() for st in scenario.steps],
                expected_outcomes=list(scenario.expected_outcomes),
            ))
        return scenario

    # --- config artifacts (append-only) -------------------------------------
    async def add_config(self, artifact: ConfigArtifact) -> ConfigArtifact:
        async with self._txn() as s:
            s.add(ConfigRow(
                id=artifact.id, node_id=artifact.node_id, vendor=artifact.vendor,
                format=str(artifact.format), content=artifact.content,
                generated_at=artifact.generated_at,
            ))
        return artifact

    async def configs_for_node(self, nid: str) -> list[ConfigArtifact]:
        async with self._sf() as s:
            rows = (await s.execute(
                select(ConfigRow)
                .where(ConfigRow.node_id == nid)
                .order_by(ConfigRow.generated_at)
            )).scalars().all()
            return [_config_out(r) for r in rows]
