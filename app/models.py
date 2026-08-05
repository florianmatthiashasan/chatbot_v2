import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from db import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    site_url = Column(String(500), nullable=False, unique=True)
    sitemap_url = Column(String(500), nullable=True)

    primary_color = Column(String(32), nullable=False, server_default="#2563eb")
    bg_color = Column(String(32), nullable=False, server_default="#f5f5f7")
    header_color = Column(String(32), nullable=False, server_default="#111827")
    header_text = Column(String(255), nullable=False, server_default="Website-Chatbot")

    last_index_built_at = Column(DateTime, nullable=True)
    last_index_chunks = Column(Integer, nullable=True)
    index_status = Column(String(64), nullable=True)  # e.g. pending/running/ok/error

    created_at = Column(DateTime, nullable=False, server_default=func.now())
