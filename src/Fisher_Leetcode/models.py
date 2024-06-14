from __future__ import annotations

from datetime import datetime, timezone

from Fisher.db.models import TimestampMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = ["Base", "GuildConfig", "Member", "Question", "Submission"]


class Base(DeclarativeBase, AsyncAttrs):
    pass


class GuildConfig(Base, TimestampMixin):
    __tablename__ = "leetcode_guild_configs"

    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cookie: Mapped[str] = mapped_column(String, nullable=False)
    notification_channel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_challenge_on: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    remind_time: Mapped[str] = mapped_column(String, nullable=False, default="23:00:00")
    guild_timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")

    members: Mapped[list[Member]] = relationship()


class Member(Base):
    __tablename__ = "leetcode_members"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("leetcode_guild_configs.guild_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class Question(Base):
    __tablename__ = "leetcode_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title_slug: Mapped[str] = mapped_column(String, nullable=False, index=True)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_only: Mapped[bool] = mapped_column(Boolean, nullable=False)


class Submission(Base):
    __tablename__ = "leetcode_submissions"

    submission_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("leetcode_guild_configs.guild_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    question_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("leetcode_questions.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("leetcode_members.user_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc)
    )
