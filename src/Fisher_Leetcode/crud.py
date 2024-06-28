from datetime import datetime, timezone

from sqlalchemy import String, cast, extract, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import GuildConfig, Member, Question, Submission


async def get_leetcode_config(db: AsyncSession, guild_id: int) -> GuildConfig | None:
    res = await db.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id))
    res = res.scalar_one_or_none()
    return res


async def get_member(db: AsyncSession, member_id: int) -> Member | None:
    res = await db.execute(select(Member).where(Member.id == member_id))
    res = res.scalar_one_or_none()
    return res


async def get_member_by_guild_user(
    db: AsyncSession, guild_id: int, user_id: int
) -> Member | None:
    res = await db.execute(
        select(Member).where(Member.guild_id == guild_id, Member.user_id == user_id)
    )
    res = res.scalar_one_or_none()
    return res


async def get_guild_members(db: AsyncSession, guild_id: int) -> list[Member]:
    res = await db.execute(select(Member).where(Member.guild_id == guild_id))
    res = res.scalars().all()
    return res


async def get_leetcode_configs_with_active_daily_challenge(
    db: AsyncSession,
) -> list[GuildConfig]:
    res = await db.execute(
        select(GuildConfig).where(GuildConfig.daily_challenge_on == True)
    )
    res = res.scalars().all()
    return res


async def get_completed_user_ids(
    db: AsyncSession, guild_id: int, today: datetime | None = None
) -> list[int]:
    today = today or datetime.now(timezone.utc)
    res = await db.execute(
        select(Member.user_id)
        .select_from(Submission)
        .join(Member, Submission.member_id == Member.id)
        .where(
            Member.guild_id == guild_id,
            extract("year", Submission.created_at) == today.year,
            extract("month", Submission.created_at) == today.month,
            extract("day", Submission.created_at) == today.day,
        )
    )
    res = res.scalars().all()
    return res


async def get_uncompleted_user_ids(
    db: AsyncSession, guild_id: int, today: datetime | None = None
) -> list[int]:
    today = today or datetime.now(timezone.utc)
    subquery = (
        select(Submission.submission_id, Submission.member_id)
        .where(
            extract("year", Submission.created_at) == today.year,
            extract("month", Submission.created_at) == today.month,
            extract("day", Submission.created_at) == today.day,
        )
        .subquery()
    )
    res = await db.execute(
        select(Member.user_id)
        .select_from(Member)
        .join(subquery, Member.id == subquery.c.member_id, isouter=True)
        .where(Member.guild_id == guild_id, subquery.c.submission_id == None)
    )
    res = res.scalars().all()
    return res


async def get_question_count(db: AsyncSession) -> int:
    res = await db.execute(select(func.count(Question.id)))
    res = res.scalar()
    return res


async def get_question_by_id(db: AsyncSession, question_id: int) -> Question | None:
    res = await db.execute(select(Question).where(Question.id == question_id))
    res = res.scalar_one_or_none()
    return res


async def get_questions_with_id_number(
    db: AsyncSession, question_id: str, limit: int = 25
) -> list[Question]:
    res = await db.execute(
        select(Question)
        .where(cast(Question.id, String).like(f"%{question_id}%"))
        .limit(limit)
    )
    res = res.scalars().all()
    return res


async def get_guild_members_score(
    db: AsyncSession, guild_id: int
) -> list[tuple[int, int]]:
    res = await db.execute(
        select(Member.user_id, func.count(Submission.submission_id).label("score"))
        .select_from(Submission)
        .join(Member, Submission.member_id == Member.id)
        .where(Member.guild_id == guild_id)
        .group_by(Member.id)
        .order_by(text("score DESC"))
    )
    res = res.all()
    return res


async def get_submission(
    db: AsyncSession, member_id: int, submission_id: int
) -> Submission | None:
    res = await db.execute(
        select(Submission).where(
            Submission.member_id == member_id, Submission.submission_id == submission_id
        )
    )
    res = res.scalar_one_or_none()
    return res
