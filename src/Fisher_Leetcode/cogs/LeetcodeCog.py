from discord import Interaction, Locale
from discord.app_commands import Group
from Fisher import Fisher, FisherCog


class LeetcodeCog(
    FisherCog, name="leetcode", description="A cog providing Leetcode commands."
):
    def __init__(self, bot: Fisher, requires_db: bool = True):
        super().__init__(bot, requires_db=requires_db)

    leetcode_group = Group(
        name="leetcode",
        description="Commands for Leetcode.",
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
        await interaction.response.send_message("Leetcode cog info", ephemeral=True)
