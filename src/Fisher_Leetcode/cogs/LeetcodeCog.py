from datetime import datetime, time, timezone

from aiohttp import ClientSession, TCPConnector
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord import (
    Colour,
    Embed,
    Forbidden,
    Guild,
    HTTPException,
    Interaction,
    Locale,
    app_commands,
)
from discord.app_commands import Group
from Fisher import Fisher, FisherCog
from Fisher.core.exceptions import CommandArgumentError
from Fisher.utils.discord_utils import is_guild_admin
from pytz import common_timezones, common_timezones_set
from pytz import timezone as pytz_timezone
from yarl import URL

from .. import crud
from ..models import *


class LeetcodeCog(
    FisherCog, name="leetcode", description="A cog providing Leetcode commands."
):
    START_TIME = time(hour=0, minute=0, second=0, tzinfo=timezone.utc)
    END_TIME = time(hour=23, minute=59, second=59, tzinfo=timezone.utc)

    def __init__(self, bot: Fisher, requires_db: bool = True):
        super().__init__(bot, requires_db=requires_db)

        self._http_connector = TCPConnector(limit=50)
        self._job_defaults = {
            "jobstore": "default",
            "coalesce": True,
            "misfire_grace_time": 60,
            "replace_existing": True,
        }

        self.scheduler = AsyncIOScheduler(job_defaults=self._job_defaults)
        self.http_sessions: dict[int, ClientSession] = {}

    async def cog_load(self) -> None:
        await super().cog_load()
        await self.init_models(Base)
        self.scheduler.add_jobstore(
            SQLAlchemyJobStore(
                url=self.bot.db_config.get_sync_url(
                    self.qualified_name
                ).get_secret_value(),
                tablename=f"{self.qualified_name}_apscheduler_jobs",
            )
        )

        # Async engine is not supported by apscheduler 3.x yet
        # but according to https://github.com/agronholm/apscheduler/issues/729
        # it will be supported in apscheduler 4.x
        # Thus, the following code is left here for future use.
        # self.scheduler.add_jobstore(
        #     SQLAlchemyJobStore(
        #         engine=self.bot.get_db(self).db_engine,
        #         tablename=f"{self.qualified_name}_apscheduler_jobs",
        #     ),
        #     alias="default",
        # )

        self.scheduler.start()
        if self.scheduler.get_job("daily-challenge-start") is None:
            self.scheduler.add_job(
                _daily_challenge_start,
                CronTrigger(
                    hour=self.START_TIME.hour,
                    minute=self.START_TIME.minute,
                    second=self.START_TIME.second,
                    timezone=timezone.utc,
                ),
                id="daily-challenge-start",
            )

        if self.scheduler.get_job("daily-challenge-end") is None:
            self.scheduler.add_job(
                _daily_challenge_end,
                CronTrigger(
                    hour=self.END_TIME.hour,
                    minute=self.END_TIME.minute,
                    second=self.END_TIME.second,
                    timezone=timezone.utc,
                ),
                id="daily-challenge-end",
            )

    async def cog_unload(self) -> None:
        await super().cog_unload()
        self.scheduler.shutdown()
        for session in self.http_sessions.values():
            await session.close()
        await self._http_connector.close()

    leetcode_group = Group(
        name="leetcode",
        description="Commands for Leetcode.",
        guild_only=True,
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "leetcode",
                    Locale.american_english: "leetcode",
                    Locale.chinese: "力扣",
                },
                "description": {
                    Locale.british_english: "Commands for Leetcode.",
                    Locale.american_english: "Commands for Leetcode.",
                    Locale.chinese: "力扣指令。",
                },
            }
        },
    )

    async def _get_http_session(self, guild_id: int) -> ClientSession:
        if guild_id not in self.http_sessions:
            self.http_sessions[guild_id] = ClientSession(
                connector=self._http_connector, connector_owner=False
            )
            async with self.db_session() as session:
                config = await crud.get_leetcode_config(session, guild_id=guild_id)
                cookie = config.cookie if config else None
                if cookie:
                    self.http_sessions[guild_id].cookie_jar.update_cookies(
                        {"LEETCODE_SESSION": cookie}, URL("https://leetcode.com")
                    )
        return self.http_sessions[guild_id]

    async def _timezone_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice]:
        return [
            app_commands.Choice(name=tz, value=tz)
            for tz in common_timezones
            if current.lower().replace(" ", "_") in tz.lower().replace("_", " ")
        ][:25]

    @leetcode_group.command(
        name="init",
        description="Initialize the Leetcode plugin in the current guild",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "init",
                    Locale.american_english: "init",
                    Locale.chinese: "初始化",
                },
                "description": {
                    Locale.british_english: "Initialize the Leetcode plugin in the current guild",
                    Locale.american_english: "Initialize the Leetcode plugin in the current guild",
                    Locale.chinese: "初始化当前服务器的Leetcode插件",
                },
            }
        },
    )
    @app_commands.autocomplete(guild_timezone=_timezone_autocomplete)
    @is_guild_admin()
    async def leetcode_init(
        self,
        interaction: Interaction,
        *,
        role_name: str = "Leetcode",
        cookie: str,
        guild_timezone: str = "UTC",
    ):
        await interaction.response.defer(ephemeral=True)

        if guild_timezone not in common_timezones_set:
            raise CommandArgumentError(
                status_code=400,
                detail=f"Unknown timezone `{guild_timezone}`. Please choose a supported timezone.",
            )

        async with self.db_session() as session:
            leetcode_config = await crud.get_leetcode_config(
                session, guild_id=interaction.guild_id
            )
            if leetcode_config is not None:
                raise CommandArgumentError(
                    status_code=400,
                    detail="""
                    Leetcode plugin already initialized in this guild.
                    If you want to reinitialize, please delete the existing configuration with `/leetcode clean` first.
                    If you want to update the configuration, please use other config commands to update the configuration.
                    """,
                )

            guild_id = interaction.guild_id
            role = await self._create_role(guild=interaction.guild, name=role_name)
            leetcode_config = GuildConfig(
                guild_id=guild_id,
                role_id=role.id,
                cookie=cookie,
                notification_channel_id=interaction.channel_id,
                daily_challenge_on=True,
                guild_timezone=guild_timezone,
            )
            session.add(leetcode_config)
            await session.commit()

            await self._add_remind_job(
                guild_id=guild_id, remind_time=time(hour=23, minute=0, second=0)
            )

        await interaction.followup.send("Leetcode plugin initialized.", ephemeral=True)
        await interaction.channel.send(embed=await self._get_info_embed(guild_id))

    @leetcode_group.command(
        name="info",
        description="Show the `leetcode` cog information in the current guild",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "info",
                    Locale.american_english: "info",
                    Locale.chinese: "信息",
                },
                "description": {
                    Locale.british_english: "Show the `leetcode` cog information in the current guild",
                    Locale.american_english: "Show the `leetcode` cog information in the current guild",
                    Locale.chinese: "显示当前服务器的`leetcode`插件信息",
                },
            }
        },
    )
    async def leetcode_info(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = await self._get_info_embed(interaction.guild_id)
        if not embed:
            raise CommandArgumentError(
                status_code=404,
                detail="Leetcode plugin is not initialized in this guild.",
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @leetcode_group.command(
        name="clean",
        description="Clean the leetcode plugin data in the current guild. Please use with caution.",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "clean",
                    Locale.american_english: "clean",
                    Locale.chinese: "清理",
                },
                "description": {
                    Locale.british_english: "Clean the leetcode plugin data in the current guild. Please use with caution.",
                    Locale.american_english: "Clean the leetcode plugin data in the current guild. Please use with caution.",
                    Locale.chinese: "清理当前服务器的leetcode插件数据。请谨慎使用。",
                },
            }
        },
    )
    @is_guild_admin()
    async def leetcode_clean(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        async with self.db_session() as session:
            leetcode_config = await crud.get_leetcode_config(
                session, guild_id=interaction.guild_id
            )
            if not leetcode_config:
                raise CommandArgumentError(
                    status_code=404,
                    detail="Leetcode plugin is not initialized in this guild.",
                )
            await self._remove_remind_job(interaction.guild_id)
            await self._delete_role(interaction.guild, role_id=leetcode_config.role_id)
            await session.delete(leetcode_config)
            await session.commit()

        await interaction.followup.send("Leetcode plugin data cleaned.", ephemeral=True)

    async def _create_role(
        self,
        guild: Guild,
        *,
        name: str = "Leetcode",
        color: int | Colour = Colour.orange(),
        hoist: bool = False,
        mentionable: bool = False,
        reason: str = "Leetcode role created and managered by Fisher-Leetcode cog.",
    ):
        for role in guild.roles:
            if role.name == name:
                raise CommandArgumentError(
                    status_code=400,
                    detail=f"Role {name} already exists. Please choose another name or delete the existing role.",
                )
        return await guild.create_role(
            name=name, color=color, hoist=hoist, mentionable=mentionable, reason=reason
        )

    async def _delete_role(
        self,
        guild: Guild,
        *,
        role_id: int,
        reason: str = "Leetcode role deleted by Fisher-Leetcode cog.",
    ) -> None:
        role = guild.get_role(role_id)
        if role is None:
            return
        try:
            await role.delete(reason=reason)
        except Forbidden:
            raise CommandArgumentError(
                status_code=403,
                detail="You do not have permission to delete this role.",
            )
        except HTTPException as e:
            raise CommandArgumentError(
                status_code=500, detail=f"Failed to delete role: {e}"
            )

    def _get_remind_job_id(self, guild_id: int) -> str:
        return f"remind-{guild_id}"

    async def _add_remind_job(self, guild_id: int, remind_time: time):
        remind_job_id = self._get_remind_job_id(guild_id)
        if self.scheduler.get_job(remind_job_id):
            self._remove_remind_job(guild_id)
        self.scheduler.add_job(
            _daily_challenge_remind,
            CronTrigger(
                hour=remind_time.hour,
                minute=remind_time.minute,
                second=remind_time.second,
                timezone=timezone.utc,
            ),
            args=(guild_id),
            id=remind_job_id,
        )

    def _remove_remind_job(self, guild_id: int):
        remind_job_id = self._get_remind_job_id(guild_id)
        if self.scheduler.get_job(remind_job_id):
            self.scheduler.remove_job(remind_job_id)

    async def _get_info_embed(self, guild_id: int) -> Embed | None:
        """Get the info embed based on thhe guild with the given guild_id.

        Args:
            guild_id (int): The guild id to get the info embed.

        Returns:
            Embed | None: return the embed if the guild is found, otherwise return None.
        """
        async with self.db_session() as session:
            config = await crud.get_leetcode_config(session, guild_id=guild_id)
            if not config:
                return None
            embed = Embed(
                title="Leetcode Configuration",
                description="Leetcode plugin configuration in this guild.",
                colour=Colour.orange(),
            )
            role = self.bot.get_guild(guild_id).get_role(config.role_id)
            embed.add_field(
                name="Role",
                value=role.mention
                if role
                else f"Role not found (previous role id: {config.role_id})",
            )
            cookie_status = await self._get_cookie_status(guild_id=guild_id)
            embed.add_field(
                name="Cookie status",
                value=f"Valid (Expires: {cookie_status.strftime('%Y-%m-%d %H:%M:%S %Z')})"
                if cookie_status
                else "Invalid",
                inline=False,
            )
            channel = self.bot.get_channel(config.notification_channel_id)
            embed.add_field(
                name="Notification channel",
                value=channel.mention
                if channel
                else f"Channel not found (previous channel id: {config.notification_channel_id})",
            )
            embed.add_field(
                name="Daily challenge status",
                value="On" if config.daily_challenge_on else "Off",
            )
            embed.add_field(name="Timezone", value=config.guild_timezone)
            embed.add_field(
                name="Daily challenge start time",
                value=datetime.now(timezone.utc)
                .replace(
                    hour=self.START_TIME.hour,
                    minute=self.START_TIME.minute,
                    second=self.START_TIME.second,
                )
                .astimezone(pytz_timezone(config.guild_timezone))
                .strftime("%H:%M:%S %Z"),
            )
            remind_time = self._timestr_to_time(
                config.remind_time, timezone_str=config.guild_timezone
            )
            embed.add_field(
                name="Daily challenge remind time",
                value=remind_time.strftime("%H:%M:%S %Z")
                if remind_time
                else f"Invalid time (current stored time: {config.remind_time})",
            )
            embed.add_field(
                name="Daily challenge end time",
                value=datetime.now(timezone.utc)
                .replace(
                    hour=self.END_TIME.hour,
                    minute=self.END_TIME.minute,
                    second=self.END_TIME.second,
                )
                .astimezone(pytz_timezone(config.guild_timezone))
                .strftime("%H:%M:%S %Z"),
            )
            return embed

    async def _get_cookie_status(self, guild_id: int) -> datetime | None:
        session = await self._get_http_session(guild_id)
        async with session.get(
            "https://leetcode.com/api/problems/0", allow_redirects=True
        ) as response:
            if not response.ok:
                return None
            try:
                leetcode_session = response.cookies.get("LEETCODE_SESSION")
                expires = leetcode_session.get("expires")
                expires_date = datetime.strptime(expires, "%a, %d %b %Y %H:%M:%S %Z")
            except AttributeError or ValueError:
                return None
            return expires_date

    def _timestr_to_time(self, timestr: str, timezone_str: str = "UTC") -> time | None:
        try:
            timestr = timestr.split(":")
            return (
                datetime.now(timezone.utc)
                .replace(
                    hour=int(timestr[0]), minute=int(timestr[1]), second=int(timestr[2])
                )
                .astimezone(pytz_timezone(timezone_str))
                .time()
            )
        except IndexError or ValueError:
            return None


async def _daily_challenge_start():
    cog = LeetcodeCog.get_instance()
    if not cog:
        raise Exception("LeetcodeCog instance not found.")

    async with cog.db_session() as session:
        channel_ids = await crud.get_active_daily_challenge_channel_ids(session)
        for channel_id in channel_ids:
            channel = cog.bot.get_channel(channel_id)
            if not channel:
                continue
            await channel.send("Daily challenge started.")


async def _daily_challenge_remind(guild_id: int):
    cog = LeetcodeCog.get_instance()
    if not cog:
        raise Exception("LeetcodeCog instance not found.")

    async with cog.db_session() as session:
        guild = cog.bot.get_guild(guild_id)

        leetcode_config = await crud.get_leetcode_config(session, guild_id=guild_id)
        if not leetcode_config:
            cog._remove_remind_job(guild_id)
            raise CommandArgumentError(
                status_code=404,
                detail=f"Leetcode module is either not initialized or not found for guild ({guild_id}) but remind job is still running.",
            )

        if not guild:
            cog._remove_remind_job(guild_id)
            await session.delete(leetcode_config)
            await session.commit()
            raise CommandArgumentError(
                status_code=404,
                detail=f"Guild ({guild_id}) has removed the bot but remind job is still running.",
            )

        role = guild.get_role(leetcode_config.role_id)

        if not role:
            cog._remove_remind_job(guild_id)
            leetcode_config.daily_challenge_on = False
            await session.commit()
            raise CommandArgumentError(
                status_code=404,
                detail=f"Remind job [remind-{guild_id}] failed due to missing role ({leetcode_config.role_id}) in guild ({guild_id}).",
            )

        notification_channel = cog.bot.get_channel(
            leetcode_config.notification_channel_id
        )

        if (
            not notification_channel
            or notification_channel.permissions_for(cog.bot.user).send_messages
        ):
            cog._remove_remind_job(guild_id)
            leetcode_config.daily_challenge_on = False
            await session.commit()
            raise CommandArgumentError(
                status_code=400,
                detail=f"Remind job [remind-{guild_id}] failed due to missing or no permission to send messages in notification channel ({leetcode_config.notification_channel_id}) in guild ({guild_id}).",
            )

        completed_user_ids = await crud.get_completed_user_ids(session, guild_id)

        remind_content = "Today's leetcode daily coding challenge will be end soon."

        unfinished_content = ""

        for member in role.members:
            if member.id not in completed_user_ids:
                if unfinished_content:
                    unfinished_content += f"{member.mention}"
                else:
                    unfinished_content = (
                        f" You still have some time to complete it.\n{member.mention}"
                    )

        await notification_channel.send(remind_content + unfinished_content)


async def _daily_challenge_end():
    cog = LeetcodeCog.get_instance()
    if not cog:
        raise Exception("LeetcodeCog instance not found.")
    async with cog.db_session() as session:
        channel_ids = await crud.get_active_daily_challenge_channel_ids(session)
        for channel_id in channel_ids:
            channel = cog.bot.get_channel(channel_id)
            if not channel:
                continue
            await channel.send("Daily challenge ended.")
