from datetime import datetime, timezone

from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import GuildConfig, Submission


async def get_leetcode_config(db: AsyncSession, guild_id: int) -> GuildConfig | None:
    res = await db.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id))
    res = res.scalar_one_or_none()
    return res


async def get_active_daily_challenge_channel_ids(
    db: AsyncSession,
) -> list[int | None]:
    res = await db.execute(
        select(GuildConfig.notification_channel_id).where(
            GuildConfig.daily_challenge_on == True
        )
    )
    res = res.scalars().all()
    return res


async def get_completed_user_ids(db: AsyncSession, guild_id: int) -> list[int]:
    today = datetime.now(timezone.utc)
    res = await db.execute(
        select(Submission.user_id).where(
            Submission.guild_id == guild_id,
            extract("year", Submission.created_at) == today.year,
            extract("month", Submission.created_at) == today.month,
            extract("day", Submission.created_at) == today.day,
        )
    )
    res = res.scalars().all()
    return res
