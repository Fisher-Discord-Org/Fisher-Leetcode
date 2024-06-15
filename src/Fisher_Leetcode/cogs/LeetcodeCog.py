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
from graphql_query import Argument, Field, Operation, Query, Variable
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
                "parameters": {
                    "role_name": {
                        "name": {
                            Locale.british_english: "role_name",
                            Locale.american_english: "role_name",
                            Locale.chinese: "身份组名称",
                        },
                        "description": {
                            Locale.british_english: "The name of the role to create. Default to `Leetcode`.",
                            Locale.american_english: "The name of the role to create. Default to `Leetcode`.",
                            Locale.chinese: "要创建的身份组的名称。默认为`Leetcode`。",
                        },
                    },
                    "cookie": {
                        "name": {
                            Locale.british_english: "cookie",
                            Locale.american_english: "cookie",
                            Locale.chinese: "cookie",
                        },
                        "description": {
                            Locale.british_english: "The cookie for leetcode.com.",
                            Locale.american_english: "The cookie for leetcode.com.",
                            Locale.chinese: "力扣官网的Cookie.",
                        },
                    },
                },
            }
        },
    )
    @app_commands.describe(
        role_name="The name of the role to create. Default to `Leetcode`.",
        cookie="The cookie for leetcode.com.",
        guild_timezone="The timezone to set for the current guild. Default to UTC.",
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
                "parameters": {
                    "channel_id": {
                        "name": {
                            Locale.british_english: "channel_id",
                            Locale.american_english: "channel_id",
                            Locale.chinese: "频道id",
                        },
                        "description": {
                            Locale.british_english: "The id of the channel to set as the notification channel. Default to the current channel.",
                            Locale.american_english: "The id of the channel to set as the notification channel. Default to the current channel.",
                            Locale.chinese: "要设置为通知频道的频道ID。默认为当前频道。",
                        },
                    }
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
                "parameters": {
                    "guild_timezone": {
                        "name": {
                            Locale.british_english: "guild_timezone",
                            Locale.american_english: "guild_timezone",
                            Locale.chinese: "服务器时区",
                        },
                        "description": {
                            Locale.british_english: "The timezone to set for the current guild. Default to UTC.",
                            Locale.american_english: "The timezone to set for the current guild. Default to UTC.",
                            Locale.chinese: "当前服务器的时区。默认为UTC。",
                        },
                    }
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

    @leetcode_group.command(
        name="remind_time",
        description="Set the remind time (in UTC) for the daily challenge in the current guild",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "remind_time",
                    Locale.american_english: "remind_time",
                    Locale.chinese: "提醒时间",
                },
                "description": {
                    Locale.british_english: "Set the remind time (in UTC) for the daily challenge in the current guild",
                    Locale.american_english: "Set the remind time (in UTC) for the daily challenge in the current guild",
                    Locale.chinese: "设置当前服务器的每日挑战提醒时间（UTC时间）",
                },
                "parameters": {
                    "hour": {
                        "name": {
                            Locale.british_english: "hour",
                            Locale.american_english: "hour",
                            Locale.chinese: "小时",
                        },
                        "description": {
                            Locale.british_english: "The hour of the remind time. Default to 23. Range from 0 to 23.",
                            Locale.american_english: "The hour of the remind time. Default to 23. Range from 0 to 23.",
                            Locale.chinese: "提醒时间的小时位。默认为23。范围从0到23。",
                        },
                    },
                    "minute": {
                        "name": {
                            Locale.british_english: "minute",
                            Locale.american_english: "minute",
                            Locale.chinese: "分钟",
                        },
                        "description": {
                            Locale.british_english: "The minute of the remind time. Default to 0. Range from 0 to 59.",
                            Locale.american_english: "The minute of the remind time. Default to 0. Range from 0 to 59.",
                            Locale.chinese: "提醒时间的分钟位。默认为0。范围从0到59。",
                        },
                    },
                    "second": {
                        "name": {
                            Locale.british_english: "second",
                            Locale.american_english: "second",
                            Locale.chinese: "秒",
                        },
                        "description": {
                            Locale.british_english: "The second of the remind time. Default to 0. Range from 0 to 59.",
                            Locale.american_english: "The second of the remind time. Default to 0. Range from 0 to 59.",
                            Locale.chinese: "提醒时间的秒位。默认为0。范围从0到59。",
                        },
                    },
                },
            }
        },
    )
    @app_commands.describe(
        hour="The hour of the remind time. Default to 23. Range from 0 to 23.",
        minute="The minute of the remind time. Default to 0. Range from 0 to 59.",
        second="The second of the remind time. Default to 0. Range from 0 to 59.",
    )
    @is_guild_admin()
    async def leetcode_remind_time(
        self, interaction: Interaction, hour: int = 23, minute: int = 0, second: int = 0
    ):
        await interaction.response.defer(ephemeral=True)

        if not 0 <= hour <= 23:
            raise CommandArgumentError(
                status_code=400, detail="The hour must be between 0 and 23."
            )
        if not 0 <= minute <= 59 or not 0 <= second <= 59:
            raise CommandArgumentError(
                status_code=400,
                detail="The minute and second must be between 0 and 59.",
            )

        async with self.db_session() as session:
            leeetcode_config = await crud.get_leetcode_config(
                session, guild_id=interaction.guild_id
            )
            if not leeetcode_config:
                raise CommandArgumentError(
                    status_code=404,
                    detail="Leetcode plugin is not initialized in this guild.",
                )
            leeetcode_config.remind_time = f"{hour:02d}:{minute:02d}:{second:02d}"
            await session.commit()
            self._remove_remind_job(interaction.guild_id)
            self._add_remind_job(
                interaction.guild_id, time(hour=hour, minute=minute, second=second)
            )

        await interaction.followup.send(
            f"Remind time set to {hour:02d}:{minute:02d}:{second:02d}.", ephemeral=True
        )

    @leetcode_group.command()
    async def leetcode_join(self, interaction: Interaction):
        async with self.db_session() as session:
            leetcode_config = await crud.get_leetcode_config(
                session, guild_id=interaction.guild_id
            )
            if not leetcode_config:
                raise CommandArgumentError(
                    status_code=404,
                    detail="Leetcode plugin is not initialized in this guild.",
                )

            member = await crud.get_member(
                session, guild_id=interaction.guild_id, member_id=interaction.user.id
            )
            if member:
                raise CommandArgumentError(
                    status_code=400,
                    detail="You have already joined the daily challenge.",
                )
            role = interaction.guild.get_role(leetcode_config.role_id)
            if not role:
                raise CommandArgumentError(
                    status_code=404,
                    detail=f"Inconsistent role configuration. Please contact the administrator to reconfigure the role. Previous role id: {leetcode_config.role_id}",
                )
            await interaction.user.add_roles(role, reason="Joined the daily challenge.")
            member = Member(user_id=interaction.user.id, guild_id=interaction.guild_id)
            session.add(member)
            await session.commit()

        await interaction.followup.send(
            """
            Successfully joined the leetcode daily challenge.
            Use `/leetcode info` to check the daily challenge information.
            Use `/leetcode today` to get today's challenge.
            Use `/leetcode quit` to quit the daily challenge.
            """,
            ephemeral=True,
        )

    @leetcode_group.command(
        name="quit",
        description="Quit the leetcode daily challenge in the current guild.",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "quit",
                    Locale.american_english: "quit",
                    Locale.chinese: "退出",
                },
                "description": {
                    Locale.british_english: "Quit the leetcode daily challenge in the current guild.",
                    Locale.american_english: "Quit the leetcode daily challenge in the current guild.",
                    Locale.chinese: "退出当前服务器的力扣每日挑战。",
                },
            }
        },
    )
    async def leetcode_quit(self, interaction: Interaction):
        async with self.db_session() as session:
            leetcode_config = await crud.get_leetcode_config(
                session, guild_id=interaction.guild_id
            )
            if not leetcode_config:
                raise CommandArgumentError(
                    status_code=404,
                    detail="Leetcode plugin is not initialized in this guild.",
                )

            member = await crud.get_member(
                session, guild_id=interaction.guild_id, member_id=interaction.user.id
            )
            if not member:
                raise CommandArgumentError(
                    status_code=400,
                    detail="You have not joined the daily challenge.",
                )

            role = interaction.guild.get_role(leetcode_config.role_id)
            if not role:
                raise CommandArgumentError(
                    status_code=404,
                    detail=f"Inconsistent role configuration. Please contact the administrator to reconfigure the role. Previous role id: {leetcode_config.role_id}",
                )

            await interaction.user.remove_roles(
                role, reason="Quit the daily challenge."
            )
            await session.delete(member)
            await session.commit()

        await interaction.followup.send(
            "Successfully quit the leetcode daily challenge.", ephemeral=True
        )

    @leetcode_group.command(
        name="update",
        description="Fetch and update all leetcode problems. (This is a heavy operation. Please use with caution.)",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "update",
                    Locale.american_english: "update",
                    Locale.chinese: "更新",
                },
                "description": {
                    Locale.british_english: "Fetch and update all leetcode problems. (This is a heavy operation. Please use with caution.)",
                    Locale.american_english: "Fetch and update all leetcode problems. (This is a heavy operation. Please use with caution.)",
                    Locale.chinese: "获取并更新所有力扣问题。（这是一个耗时操作，请谨慎使用。）",
                },
            }
        },
    )
    @is_guild_admin()
    async def leetcode_update(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        async with self.db_session() as db_session:
            leetcode_config = await crud.get_leetcode_config(
                db_session, guild_id=interaction.guild_id
            )
            if not leetcode_config:
                raise CommandArgumentError(
                    status_code=404,
                    detail="Leetcode plugin is not initialized in this guild.",
                )

            http_session = await self._get_http_session(interaction.guild_id)
            async with http_session.get(
                "https://leetcode.com/api/problems/all"
            ) as response:
                if not response.ok:
                    if response.status == 403:
                        raise CommandArgumentError(
                            status_code=403,
                            detail="Failed to get leetcode questions: Please check if the cookie status is valid and try again.",
                        )
                    raise CommandArgumentError(
                        status_code=400,
                        detail=f"Failed to get leetcode questions: {response.status} {response.reason}",
                    )
                data = await response.text()
                data = json_loads(data)

            for question in data["stat_status_pairs"]:
                question_id = question["stat"]["frontend_question_id"]
                db_question = await crud.get_question_by_id(
                    db_session, question_id=question_id
                )
                if db_question:
                    db_question.title = question["stat"]["question__title"]
                    db_question.title_slug = question["stat"]["question__title_slug"]
                    db_question.difficulty = question["difficulty"]["level"]
                    db_question.paid_only = question["paid_only"]
                else:
                    db_question = Question(
                        id=question_id,
                        title=question["stat"]["question__title"],
                        title_slug=question["stat"]["question__title_slug"],
                        difficulty=question["difficulty"]["level"],
                        paid_only=question["paid_only"],
                    )
                    db_session.add(db_question)
            await db_session.commit()

        await interaction.followup.send(
            f"Successfully updated {len(data['stat_status_pairs'])} leetcode questions.",
            ephemeral=True,
        )

    async def _question_autocomplete(self, interaction: Interaction, current: str):
        async with self.db_session() as session:
            questions = await crud.get_question_with_id_number(session, current)

        return [
            app_commands.Choice(
                name=f"{question.id}. {question.title}", value=question.id
            )
            for question in questions[:25]
        ]

    @leetcode_group.command(
        name="question",
        description="Get the leetcode question with the given question id.",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "question",
                    Locale.american_english: "question",
                    Locale.chinese: "问题",
                },
                "description": {
                    Locale.british_english: "Get the leetcode question with the given question id.",
                    Locale.american_english: "Get the leetcode question with the given question id.",
                    Locale.chinese: "显示对应编号的力扣问题.",
                },
                "parameters": {
                    "question_id": {
                        "name": {
                            Locale.british_english: "question_id",
                            Locale.american_english: "question_id",
                            Locale.chinese: "问题编号",
                        },
                        "description": {
                            Locale.british_english: "The id of the leetcode question.",
                            Locale.american_english: "The id of the leetcode question.",
                            Locale.chinese: "要获取的问题编号。",
                        },
                    },
                },
            }
        },
    )
    @app_commands.describe(question_id="The id of the leetcode question.")
    @app_commands.autocomplete(question_id=_question_autocomplete)
    async def leetcode_question(self, interaction: Interaction, question_id: int):
        await interaction.response.defer(ephemeral=True)
        async with self.db_session() as session:
            question = await crud.get_question_by_id(session, question_id=question_id)
            if not question:
                raise CommandArgumentError(
                    status_code=404,
                    detail=f"Question {question_id} not found. Please check the question id or update the questions with `/leetcode update` and try again.",
                )
        session = await self._get_http_session(interaction.guild_id)
        async with session.post(
            "https://leetcode.com/graphql",
            json={
                "operationName": "questionData",
                "variables": {"titleSlug": question.title_slug},
                "query": _get_question_graphql_query(),
            },
        ) as response:
            if not response.ok:
                raise CommandArgumentError(
                    status_code=400,
                    detail=f"Failed to get question {question_id}: {response.status} {response.reason}",
                )
            data = await response.json()
            data = data["data"]["question"]

        question_id = data["questionFrontendId"]
        title = data["title"]
        question_link = f"https://leetcode.com/problems/{data['titleSlug']}"
        ac_rate = data["acRate"]
        difficulty = data["difficulty"]
        likes = data["likes"]
        dislikes = data["dislikes"]
        is_paid_only = data["isPaidOnly"]
        has_solution = data["hasSolution"]
        solution_link = f"{question_link}/solution"
        topics_tags = data["topicTags"]
        similar_questions = json_loads(data["similarQuestions"])

        embed = Embed()
        embed.title = f"Leetcode Problem {question_id}{' 💰' if is_paid_only else ''}"
        embed.description = f"[{title}]({question_link}) ({difficulty}){f' [Solution]({solution_link})' if has_solution else ''}"
        embed.add_field(name="Acceptance", value=f"{round(ac_rate, 2)}%", inline=True)
        embed.add_field(name="👍 Like", value=likes, inline=True)
        embed.add_field(name="👎 Dislike", value=dislikes, inline=True)

        topic_field_value = ""
        for i in range(len(topics_tags)):
            value = f"[{topics_tags[i]['name']}](https://leetcode.com/tag/{topics_tags[i]['slug']})"
            if len(topic_field_value) + len(value) > 1024:
                break
            topic_field_value += value
            if i < len(topics_tags) - 1:
                topic_field_value += ", "
        embed.add_field(name="Related topics", value=topic_field_value, inline=False)

        similar_questions_field_value = ""
        for i in range(len(similar_questions)):
            value = f"[{similar_questions[i]['title']}](https://leetcode.com/problems/{similar_questions[i]['titleSlug']}) ({similar_questions[i]['difficulty']})"
            if len(similar_questions_field_value) + len(value) > 1024:
                break
            similar_questions_field_value += value
            if i < len(similar_questions) - 1:
                similar_questions_field_value += "\n"

        if len(similar_questions) > 0:
            embed.add_field(
                name="Similar questions",
                value=similar_questions_field_value,
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @leetcode_group.command(
        name="today",
        description="Get the leetcode daily challenge question of today.",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "today",
                    Locale.american_english: "today",
                    Locale.chinese: "每日挑战",
                },
                "description": {
                    Locale.british_english: "Get the leetcode daily challenge question of today.",
                    Locale.american_english: "Get the leetcode daily challenge question of today.",
                    Locale.chinese: "获取今天的力扣每日挑战问题。",
                },
            }
        },
    )
    async def leetcode_today(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        session = await self._get_http_session(interaction.guild_id)

        async with session.post(
            "https://leetcode.com/graphql",
            json={
                "operationName": "questionOfToday",
                "variables": {},
                "query": _get_daily_challenge_graphql_query(),
            },
        ) as response:
            if not response.ok:
                raise CommandArgumentError(
                    status_code=400,
                    detail=f"Failed to get daily challenge question: {response.status} {response.reason}",
                )
            data = await response.json()
            data = data["data"]["activeDailyCodingChallengeQuestion"]

        date = data["date"]
        question = data["question"]
        question_id = question["questionFrontendId"]
        title = question["title"]
        question_link = f"https://leetcode.com{data['link']}"
        ac_rate = question["acRate"]
        difficulty = question["difficulty"]
        likes = question["likes"]
        dislikes = question["dislikes"]
        is_paid_only = question["isPaidOnly"]
        has_solution = question["hasSolution"]
        solution_link = f"{question_link}/solution"
        topics_tags = question["topicTags"]
        similar_questions = json_loads(question["similarQuestions"])

        embed = Embed()
        embed.title = f"🏆 Leetcode Daily Coding Challenge ({date})"
        embed.add_field(
            name=f"Problem {question_id}{' 💰' if is_paid_only else ''}",
            value=f"[{title}]({question_link}) ({difficulty}){f' [Solution]({solution_link})' if has_solution else ''}",
            inline=False,
        )

        embed.add_field(name="Acceptance", value=f"{round(ac_rate, 2)}%", inline=True)
        embed.add_field(name="👍 Like", value=likes, inline=True)
        embed.add_field(name="👎 Dislike", value=dislikes, inline=True)

        topic_field_value = ""
        for i in range(len(topics_tags)):
            value = f"[{topics_tags[i]['name']}](https://leetcode.com/tag/{topics_tags[i]['slug']})"
            if len(topic_field_value) + len(value) > 1024:
                break
            topic_field_value += value
            if i < len(topics_tags) - 1:
                topic_field_value += ", "
        embed.add_field(name="Related topics", value=topic_field_value, inline=False)

        similar_questions_field_value = ""
        for i in range(len(similar_questions)):
            value = f"[{similar_questions[i]['title']}](https://leetcode.com/problems/{similar_questions[i]['titleSlug']}) ({similar_questions[i]['difficulty']})"
            if len(similar_questions_field_value) + len(value) > 1024:
                break
            similar_questions_field_value += value
            if i < len(similar_questions) - 1:
                similar_questions_field_value += "\n"

        if len(similar_questions) > 0:
            embed.add_field(
                name="Similar questions",
                value=similar_questions_field_value,
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @leetcode_group.command(
        name="get_submission",
        description="Get the leetcode submission with the given submission id.",
        extras={
            "locale": {
                "name": {
                    Locale.british_english: "get_submission",
                    Locale.american_english: "get_submission",
                    Locale.chinese: "获取提交",
                },
                "description": {
                    Locale.british_english: "Get the leetcode submission with the given submission id.",
                    Locale.american_english: "Get the leetcode submission with the given submission id.",
                    Locale.chinese: "获取指定ID的提交。",
                },
                "parameters": {
                    "submission_id": {
                        "name": {
                            Locale.british_english: "submission_id",
                            Locale.american_english: "submission_id",
                            Locale.chinese: "提交id",
                        },
                        "description": {
                            Locale.british_english: "The id of the leetcode submission.",
                            Locale.american_english: "The id of the leetcode submission.",
                            Locale.chinese: "要获取提交的ID。",
                        },
                    },
                },
            }
        },
    )
    @app_commands.describe(submission_id="The id of the leetcode submission.")
    async def leetcode_get_submission(
        self, interaction: Interaction, submission_id: int
    ):
        await interaction.response.defer(ephemeral=True)

        session = await self._get_http_session(interaction.guild_id)
        async with session.post(
            "https://leetcode.com/graphql",
            json={
                "operationName": "submissionDetails",
                "variables": {
                    "submissionIntId": submission_id,
                    "submissionId": str(submission_id),
                },
                "query": _get_submission_graph_query(),
            },
        ) as response:
            if not response.ok:
                raise CommandArgumentError(
                    status_code=400,
                    detail=f"Failed to get submission {submission_id}: {response.status} {response.reason}",
                )
            data = await response.json()
            submission_details = data["data"]["submissionDetails"]
            submission_complexity = data["data"]["submissionComplexity"]

        if not submission_details:
            raise CommandArgumentError(
                status_code=404,
                detail=f"Submission `{submission_id}` not found or not accessible. Please check the input submission id and cookie status and try again.",
            )

        async with self.db_session() as session:
            leetcode_config = await crud.get_leetcode_config(
                session, interaction.guild_id
            )
            guild_timezone = (
                leetcode_config.guild_timezone if leetcode_config else "UTC"
            )

        question_id = submission_details["question"]["questionFrontendId"]
        question_title = submission_details["question"]["title"]
        question_link = f"https://leetcode.com/problems/{submission_details['question']['titleSlug']}"
        question_difficulty = submission_details["question"]["difficulty"]
        question_is_paid_only = submission_details["question"]["isPaidOnly"]
        submission_status = _get_status_display(submission_details["statusCode"])
        submission_runtime_display = submission_details["runtimeDisplay"]
        submission_runtime_percentile = submission_details["runtimePercentile"]
        submission_runtime_complexity = (
            submission_complexity["timeComplexity"]["complexity"]
            if submission_status == "Accepted"
            else None
        )
        submission_memory_display = submission_details["memoryDisplay"]
        submission_memory_percentile = submission_details["memoryPercentile"]
        submission_memory_complexity = (
            submission_complexity["memoryComplexity"]["complexity"]
            if submission_status == "Accepted"
            else None
        )
        submission_author = submission_details["user"]["username"]
        submission_author_icon = submission_details["user"]["profile"]["userAvatar"]
        submission_language = submission_details["lang"]["verboseName"]
        submission_datetime = (
            datetime.fromtimestamp(submission_details["timestamp"], timezone.utc)
            .astimezone(pytz_timezone(guild_timezone))
            .strftime("%Y-%m-%d %H:%M:%S %Z")
        )
        submission_code = submission_details["code"]
        submission_notes = submission_details["notes"]
        submission_topic_tags = submission_details["topicTags"]

        embed = Embed()
        embed.color = (
            Colour.green() if submission_status == "Accepted" else Colour.red()
        )
        embed.set_author(name=submission_author, icon_url=submission_author_icon)
        embed.title = f"✍️ Leetcode Daily Coding Challenge Submission"
        embed.url = f"https://leetcode.com/submissions/detail/{submission_id}"

        embed.add_field(
            name=f"Problem {question_id}{f' 💰' if question_is_paid_only else ''}",
            value=f"[{question_title}]({question_link}) ({question_difficulty})",
            inline=False,
        )

        embed.add_field(name="Status", value=submission_status, inline=False)

        embed.add_field(
            name="Runtime",
            value=f"{submission_runtime_display}{f' (Beats: {submission_runtime_percentile:.2f}%)' if submission_runtime_percentile else ''}{f'\n{submission_runtime_complexity}' if submission_runtime_complexity else ''}",
            inline=True,
        )
        embed.add_field(
            name="Memory",
            value=f"{submission_memory_display}{f' (Beats: {submission_memory_percentile:.2f}%)' if submission_memory_percentile else ''}{f'\n{submission_memory_complexity}' if submission_memory_complexity else ''}",
            inline=True,
        )

        highlight_type = _get_highlight_type(submission_language)

        code_value = _generate_embed_text_value(
            f"{highlight_type}\n{submission_code}", render_type="markdown"
        )
        embed.add_field(
            name=f"Submission code ({submission_language})",
            value=code_value,
            inline=False,
        )

        if submission_notes:
            notes_value = _generate_embed_text_value(
                submission_notes, render_type="markdown"
            )
            embed.add_field(name="Notes", value=notes_value, inline=False)

        if submission_topic_tags:
            topic_field_value = ""
            for i in range(len(submission_topic_tags)):
                value = f"[{submission_topic_tags[i]['name']}](https://leetcode.com/tag/{submission_topic_tags[i]['slug']})"
                if len(topic_field_value) + len(value) > 1024:
                    break
                topic_field_value += value
                if i < len(submission_topic_tags) - 1:
                    topic_field_value += ", "
            embed.add_field(name="Related tags", value=topic_field_value, inline=False)

        embed.set_footer(text=f"{submission_id} | {submission_datetime}")

        await interaction.followup.send(embed=embed, ephemeral=True)

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


def _get_question_graphql_query() -> str:
    titleSlug = Variable(name="titleSlug", type="String!")
    query = Query(
        name="question",
        arguments=[Argument(name="titleSlug", value=titleSlug)],
        fields=[
            Field(name="questionId"),
            Field(name="questionFrontendId"),
            Field(name="title"),
            Field(name="titleSlug"),
            Field(name="acRate"),
            Field(name="difficulty"),
            Field(name="freqBar"),
            Field(name="likes"),
            Field(name="dislikes"),
            Field(name="content"),
            Field(name="similarQuestions"),
            Field(name="isFavor"),
            Field(name="isPaidOnly"),
            Field(name="status"),
            Field(name="hasVideoSolution"),
            Field(name="hasSolution"),
            Field(
                name="topicTags",
                fields=[
                    Field(name="name"),
                    Field(name="id"),
                    Field(name="slug"),
                ],
            ),
        ],
    )
    operation = Operation(
        type="query", name="questionData", variables=[titleSlug], queries=[query]
    )
    return operation.render()


def _get_daily_challenge_graphql_query() -> str:
    question = Field(
        name="question",
        fields=[
            Field(name="questionId"),
            Field(name="questionFrontendId"),
            Field(name="title"),
            Field(name="titleSlug"),
            Field(name="acRate"),
            Field(name="difficulty"),
            Field(name="freqBar"),
            Field(name="likes"),
            Field(name="dislikes"),
            Field(name="content"),
            Field(name="similarQuestions"),
            Field(name="isFavor"),
            Field(name="isPaidOnly"),
            Field(name="status"),
            Field(name="hasVideoSolution"),
            Field(name="hasSolution"),
            Field(
                name="topicTags",
                fields=[
                    Field(name="name"),
                    Field(name="id"),
                    Field(name="slug"),
                ],
            ),
        ],
    )
    query = Query(
        name="activeDailyCodingChallengeQuestion",
        fields=[
            Field(name="date"),
            Field(name="userStatus"),
            Field(name="link"),
            question,
        ],
    )
    operation = Operation(type="query", name="questionOfToday", queries=[query])
    return operation.render()


def _get_submission_graph_query() -> str:
    submission_int_id = Variable(name="submissionIntId", type="Int!")
    submission_id = Variable(name="submissionId", type="ID!")
    user = Field(
        name="user",
        fields=[
            Field(name="username"),
            Field(
                name="profile",
                fields=[Field(name="realName"), Field(name="userAvatar")],
            ),
        ],
    )
    lang = Field(name="lang", fields=[Field(name="name"), Field(name="verboseName")])
    question = Field(
        name="question",
        fields=[
            Field(name="questionFrontendId"),
            Field(name="title"),
            Field(name="titleSlug"),
            Field(name="difficulty"),
            Field(name="isPaidOnly"),
        ],
    )
    topic_tags = Field(
        name="topicTags",
        fields=[Field(name="tagId"), Field(name="slug"), Field(name="name")],
    )
    submission_query = Query(
        name="submissionDetails",
        arguments=[Argument(name="submissionId", value=submission_int_id)],
        fields=[
            Field(name="runtime"),
            Field(name="runtimeDisplay"),
            Field(name="runtimePercentile"),
            Field(name="memory"),
            Field(name="memoryDisplay"),
            Field(name="memoryPercentile"),
            Field(name="memoryDistribution"),
            Field(name="code"),
            Field(name="timestamp"),
            Field(name="statusCode"),
            user,
            lang,
            question,
            Field(name="notes"),
            topic_tags,
            Field(name="runtimeError"),
            Field(name="compileError"),
            Field(name="lastTestcase"),
        ],
    )
    complexity_query = Query(
        name="submissionComplexity",
        arguments=[Argument(name="submissionId", value=submission_id)],
        fields=[
            Field(
                name="timeComplexity",
                fields=[
                    Field(name="complexity"),
                    Field(name="displayName"),
                    Field(name="funcStr"),
                    Field(name="vote"),
                ],
            ),
            Field(
                name="memoryComplexity",
                fields=[
                    Field(name="complexity"),
                    Field(name="displayName"),
                    Field(name="funcStr"),
                    Field(name="vote"),
                ],
            ),
            Field(name="isLimited"),
        ],
    )
    operation = Operation(
        type="query",
        name="submissionDetails",
        variables=[submission_int_id, submission_id],
        queries=[submission_query, complexity_query],
    )
    return operation.render()


def _get_highlight_type(language: str) -> str:
    support_highlight = {
        "python3": "python",
        "python": "python",
        "javascript": "javascript",
        "java": "java",
        "c++": "cpp",
        "c": "c",
        "c#": "csharp",
        "sql": "sql",
        "mysql": "sql",
        "go": "go",
        "ruby": "ruby",
        "swift": "swift",
        "scala": "scala",
        "kotlin": "kotlin",
        "rust": "rust",
        "php": "php",
        "typescript": "typescript",
        "r": "r",
        "bash": "bash",
        "shell": "bash",
        "html": "html",
        "css": "css",
        "scala": "scala",
    }
    return support_highlight.get(language.lower(), language.lower())


def _generate_embed_text_value(text: str, render_type: str = "plain_text"):
    length = len(text)
    if render_type == "markdown":
        if length + 6 > 1024:
            return f"```{text[:1024 - 9]}...```"
        return f"```{text}```"

    if length > 1024:
        return f"{text[:1024 - 3]}..."

    return text


def _get_status_display(status_code: int):
    if status_code == 10:
        return "Accepted"
    if status_code == 11:
        return "Wrong Answer"
    if status_code == 12:
        return "Memory Limit Exceeded"
    if status_code == 13:
        return "Output Limit Exceeded"
    if status_code == 14:
        return "Time Limit Exceeded"
    if status_code == 15:
        return "Runtime Error"
    if status_code == 16:
        return "Internal Error"
    if status_code == 17:
        return "Compile Error"
    if status_code == 18:
        return "Timeout"
    return "Unknown Status"
