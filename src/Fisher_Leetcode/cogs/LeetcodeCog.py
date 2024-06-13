from base64 import urlsafe_b64decode
from datetime import datetime, time, timezone
from json import loads as json_loads

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
    TextChannel,
    app_commands,
)
from discord.app_commands import Group
from Fisher import Fisher, FisherCog, logger
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

        self.scheduler = AsyncIOScheduler()
        self.http_sessions: dict[int, ClientSession] = {}

    async def cog_load(self) -> None:
        await super().cog_load()
        await self.init_models(Base)
        job_defaults = {
            "jobstore": "default",
            "coalesce": True,
            "misfire_grace_time": 60,
            "replace_existing": True,
        }
        # Async engine is not supported by apscheduler 3.x yet
        # but according to https://github.com/agronholm/apscheduler/issues/729
        # it will be supported in apscheduler 4.x
        # Thus, the following code is left here for future use.
        # SQLAlchemyJobStore(
        #     engine=self.bot.get_db(self).db_engine,
        #     tablename=f"{self.qualified_name}_apscheduler_jobs",
        # ),
        jobstores = {
            "default": SQLAlchemyJobStore(
                url=self.bot.db_config.get_sync_url(
                    self.qualified_name
                ).get_secret_value(),
                tablename=f"{self.qualified_name}_apscheduler_jobs",
            )
        }

        self.scheduler.configure(jobstores=jobstores, job_defaults=job_defaults)
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

            self._add_remind_job(
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
            self._remove_remind_job(interaction.guild_id)
            await self._delete_role(interaction.guild, role_id=leetcode_config.role_id)
            await session.delete(leetcode_config)
            await session.commit()

        await interaction.followup.send("Leetcode plugin data cleaned.", ephemeral=True)

    @leetcode_group.command(
        name="start",
        description="Start the leetcode daily challenge in the current guild.",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "start",
                    Locale.american_english: "start",
                    Locale.chinese: "开始",
                },
                "description": {
                    Locale.british_english: "Start the leetcode daily challenge in the current guild.",
                    Locale.american_english: "Start the leetcode daily challenge in the current guild.",
                    Locale.chinese: "开始当前服务器的力扣每日挑战。",
                },
            }
        },
    )
    @is_guild_admin()
    async def leetcode_start(self, interaction: Interaction):
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

            if leetcode_config.daily_challenge_on:
                raise CommandArgumentError(
                    status_code=400,
                    detail="Daily challenge is already started.",
                )

            leetcode_config.daily_challenge_on = True
            await session.commit()

            remind_time = self._timestr_to_time(leetcode_config.remind_time)

            self._add_remind_job(
                guild_id=interaction.guild_id,
                remind_time=remind_time or time(hour=23, minute=0, second=0),
            )

        await interaction.followup.send("Daily challenge started.", ephemeral=True)

    @leetcode_group.command(
        name="stop",
        description="Stop the leetcode daily challenge in the current guild.",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "stop",
                    Locale.american_english: "stop",
                    Locale.chinese: "停止",
                },
                "description": {
                    Locale.british_english: "Stop the leetcode daily challenge in the current guild.",
                    Locale.american_english: "Stop the leetcode daily challenge in the current guild.",
                    Locale.chinese: "停止当前服务器的力扣每日挑战。",
                },
            }
        },
    )
    @is_guild_admin()
    async def leetcode_stop(self, interaction: Interaction):
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

            if not leetcode_config.daily_challenge_on:
                raise CommandArgumentError(
                    status_code=400,
                    detail="Daily challenge is already stopped.",
                )

            leetcode_config.daily_challenge_on = False
            await session.commit()

            self._remove_remind_job(interaction.guild_id)

        await interaction.followup.send("Daily challenge stopped.", ephemeral=True)

    async def _channel_autocomplete(self, interaction: Interaction, current: str):
        return [
            app_commands.Choice(
                name=f"{channel.name} ({channel.category})", value=str(channel.id)
            )
            for channel in interaction.guild.text_channels
            if current.lower() in channel.name.lower()
        ][:25]

    @leetcode_group.command(
        name="channel",
        description="Set the notification channel for the leetcode daily challenge in the current guild.",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "channel",
                    Locale.american_english: "channel",
                    Locale.chinese: "频道",
                },
                "description": {
                    Locale.british_english: "Set the notification channel for the leetcode daily challenge in the current guild.",
                    Locale.american_english: "Set the notification channel for the leetcode daily challenge in the current guild.",
                    Locale.chinese: "设置当前服务器的力扣每日挑战通知频道。",
                },
            }
        },
    )
    @app_commands.describe(
        channel_id="The id of the channel to set as the notification channel. Default to the current channel."
    )
    @app_commands.autocomplete(channel_id=_channel_autocomplete)
    @is_guild_admin()
    async def leetcode_channel(self, interaction: Interaction, channel_id: str = None):
        await interaction.response.defer(ephemeral=True)
        if not channel_id.isdigit():
            raise CommandArgumentError(
                status_code=400, detail="The channel id must be an integer."
            )
        channel = interaction.guild.get_channel(
            int(channel_id) or interaction.channel_id
        )
        if not isinstance(channel, TextChannel):
            raise CommandArgumentError(
                status_code=400, detail="The channel must be a text channel."
            )
        if not channel.permissions_for(interaction.guild.me).send_messages:
            raise CommandArgumentError(
                status_code=403,
                detail="The bot does not have permission to send messages in the channel.",
            )

        async with self.db_session() as session:
            leetcode_config = await crud.get_leetcode_config(
                session, guild_id=interaction.guild_id
            )
            if not leetcode_config:
                raise CommandArgumentError(
                    status_code=404,
                    detail="Leetcode plugin is not initialized in this guild.",
                )
            leetcode_config.notification_channel_id = channel.id
            await session.commit()

        await interaction.followup.send(
            f"Notification channel set to {channel.mention}.", ephemeral=True
        )

    @leetcode_group.command(
        name="timezone",
        description="Set the timezone for the current guild. Default to UTC.",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "timezone",
                    Locale.american_english: "timezone",
                    Locale.chinese: "时区",
                },
                "description": {
                    Locale.british_english: "Set the timezone for the current guild. Default to UTC.",
                    Locale.american_english: "Set the timezone for the current guild. Default to UTC.",
                    Locale.chinese: "设置当前服务器的时区。默认为UTC。",
                },
            }
        },
    )
    @app_commands.describe(
        guild_timezone="The timezone to set for the current guild. Default to UTC."
    )
    @app_commands.autocomplete(guild_timezone=_timezone_autocomplete)
    @is_guild_admin()
    async def leetcode_timezone(
        self, interaction: Interaction, guild_timezone: str = "UTC"
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
            if not leetcode_config:
                raise CommandArgumentError(
                    status_code=404,
                    detail="Leetcode plugin is not initialized in this guild.",
                )
            leetcode_config.guild_timezone = guild_timezone
            await session.commit()

        await interaction.followup.send(
            f"Timezone set to `{guild_timezone}`.", ephemeral=True
        )

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

    def _add_remind_job(self, guild_id: int, remind_time: time):
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
            args=(guild_id,),
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
            if not cookie_status:
                embed.add_field(
                    name="Cookie status",
                    value="Invalid",
                    inline=False,
                )
            else:
                new_cookie, cookie_expires = cookie_status
                if new_cookie:
                    config.cookie = new_cookie
                    await session.commit()
                embed.add_field(
                    name="Cookie status",
                    value=f"Valid (Expires: {cookie_expires.strftime('%Y-%m-%d %H:%M:%S %Z')})",
                    inline=False,
                )

            channel = self.bot.get_channel(config.notification_channel_id)
            embed.add_field(
                name="Notification channel",
                value=channel.mention
                if channel
                else f"Channel not found (previous channel id: {config.notification_channel_id})",
                inline=False,
            )
            embed.add_field(
                name="Daily challenge status",
                value="On" if config.daily_challenge_on else "Off",
                inline=False,
            )
            embed.add_field(name="Timezone", value=config.guild_timezone, inline=False)
            embed.add_field(
                name="Daily challenge start time",
                value=datetime.now(timezone.utc)
                .replace(
                    hour=self.START_TIME.hour,
                    minute=self.START_TIME.minute,
                    second=self.START_TIME.second,
                )
                .astimezone(pytz_timezone(config.guild_timezone))
                .time()
                .strftime("%H:%M:%S"),
                inline=False,
            )
            remind_time = self._timestr_to_time(
                config.remind_time, timezone_str=config.guild_timezone
            )
            embed.add_field(
                name="Daily challenge remind time",
                value=remind_time.strftime("%H:%M:%S")
                if remind_time
                else f"Invalid time (current stored time: {config.remind_time})",
                inline=False,
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
                .time()
                .strftime("%H:%M:%S"),
                inline=False,
            )
            return embed

    async def _get_cookie_status(
        self, guild_id: int
    ) -> tuple[str | None, datetime] | None:
        """
        Check if there is a new cookie. If there is, return the new cookie and its expiration date.
        Otherwise, return None with the expiration date of the existing cookie.
        If the existing cookie is invalid, return None.

        Args:
            guild_id (int): The guild id to get the cookie status.

        Returns:
            tuple[str | None, datetime] | None:
                - If there is a new cookie, return a tuple of the new cookie and its expiration date.
                - If there is no new cookie, return None with the expiration date of the existing cookie.
                - If the existing cookie is invalid, return None.
        """
        session = await self._get_http_session(guild_id)
        new_cookie = False
        async with session.get(
            "https://leetcode.com/api/problems/0", allow_redirects=True
        ) as response:
            if not response.ok:
                logger.debug(
                    f"Failed to receive response from leetcode.com when checking cookie status. Status code: {response.status}. Reason: {response.reason}."
                )
                return None
            if response.cookies.get("LEETCODE_SESSION"):
                new_cookie = True
        leetcode_session = session.cookie_jar.filter_cookies(
            "https://leetcode.com"
        ).get("LEETCODE_SESSION", None)
        if not leetcode_session:
            return None
        try:
            if leetcode_session.get("expires"):
                expires = leetcode_session.get("expires")
                expires_date = datetime.strptime(expires, "%a, %d %b %Y %H:%M:%S %Z")
            else:
                payload = leetcode_session.value.split(".")[1]
                payload = json_loads(urlsafe_b64decode(payload + "==").decode("utf-8"))
                expires_date = datetime.fromtimestamp(
                    payload["refreshed_at"] + payload["_session_expiry"],
                    tz=timezone.utc,
                )

            return (leetcode_session.value if new_cookie else None, expires_date)
        except Exception as e:
            logger.debug(
                f"Enable to parse session cookie `LEETCODE_SESSION`: {e}", exc_info=True
            )
            return None

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
            or notification_channel.permissions_for(
                notification_channel.guild.me
            ).send_messages
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
