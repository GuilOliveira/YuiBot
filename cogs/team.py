import discord
from discord import app_commands
from discord.ext import commands
import random
import typing
import logging

logger = logging.getLogger(__name__)


def build_teams_embed(team1: list[discord.Member], team2: list[discord.Member]) -> discord.Embed:
    """Creates a rich embed showing the two randomised teams."""
    embed = discord.Embed(
        title="🎲 Sorteio de Times",
        color=discord.Color.green(),
    )

    t1_text = "\n".join(f"• {m.display_name}" for m in team1) or "_(vazio)_"
    t2_text = "\n".join(f"• {m.display_name}" for m in team2) or "_(vazio)_"

    embed.add_field(name="🔵 Time 1", value=t1_text, inline=True)
    embed.add_field(name="🔴 Time 2", value=t2_text, inline=True)
    embed.set_footer(text="Boa sorte a todos! 🍀")
    return embed


def split_into_teams(members: list[discord.Member]) -> tuple[list[discord.Member], list[discord.Member]]:
    """Shuffles a list of members and splits them into two teams."""
    shuffled = members.copy()
    random.shuffle(shuffled)
    mid = len(shuffled) // 2
    return shuffled[:mid], shuffled[mid:]


# ---------------------------------------------------------------------------
# View for Reaction-based draw
# ---------------------------------------------------------------------------
class SortearView(discord.ui.View):
    """A persistent view with a ✅ reaction prompt and a 'Sortear' button."""

    def __init__(self) -> None:
        super().__init__(timeout=18000)  # 5 hours timeout

    @discord.ui.button(label="Sortear", style=discord.ButtonStyle.success, emoji="🎲")
    async def sortear_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.message is None or interaction.channel is None:
            await interaction.response.send_message("❌ Não foi possível acessar a mensagem.", ephemeral=True)
            return

        # Re-fetch the message to get the latest reaction data
        try:
            message = await interaction.channel.fetch_message(interaction.message.id)
        except discord.NotFound:
            await interaction.response.send_message("❌ Mensagem original não encontrada.", ephemeral=True)
            return

        # Find the ✅ reaction on this message
        target_reaction: typing.Optional[discord.Reaction] = None
        for reaction in message.reactions:
            if str(reaction.emoji) == "✅":
                target_reaction = reaction
                break

        if target_reaction is None or target_reaction.count <= 1:
            await interaction.response.send_message("❌ Ninguém reagiu com ✅ ainda!", ephemeral=True)
            return

        # Collect users who reacted (excluding bots)
        users: list[discord.Member] = []
        async for user in target_reaction.users():
            if user.bot:
                continue
            if isinstance(user, discord.Member):
                users.append(user)
            elif interaction.guild:
                try:
                    member = interaction.guild.get_member(user.id) or await interaction.guild.fetch_member(user.id)
                    users.append(member)
                except discord.NotFound:
                    pass

        if len(users) < 2:
            await interaction.response.send_message(
                "❌ É necessário pelo menos **2 pessoas** reagindo com ✅ para sortear.",
                ephemeral=True,
            )
            return

        team1, team2 = split_into_teams(users)
        embed = build_teams_embed(team1, team2)

        await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Team Cog
# ---------------------------------------------------------------------------
class TeamCog(commands.Cog):
    """Slash commands for random team generation."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- /sortear_voz --------------------------------------------------------
    @app_commands.command(
        name="sortear_voz",
        description="🎲 Sorteia dois times a partir dos membros no seu canal de voz.",
    )
    async def sortear_voz(self, interaction: discord.Interaction) -> None:
        # Check if user is in a voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ Você precisa estar em um canal de voz para usar este comando.",
                ephemeral=True,
            )
            return

        channel = interaction.user.voice.channel

        # Get all non-bot members
        members = [m for m in channel.members if not m.bot]

        if len(members) < 2:
            await interaction.response.send_message(
                "❌ É necessário pelo menos **2 pessoas** (não-bots) no canal de voz.",
                ephemeral=True,
            )
            return

        team1, team2 = split_into_teams(members)
        embed = build_teams_embed(team1, team2)
        embed.description = f"Canal de voz: **{channel.name}** ({len(members)} jogadores)"

        await interaction.response.send_message(embed=embed)

    # -- /sortear_reacao -----------------------------------------------------
    @app_commands.command(
        name="sortear_reacao",
        description="🎲 Envia uma mensagem para reagir e depois sorteia os times.",
    )
    async def sortear_reacao(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🎲 Sorteio de Times",
            description=(
                "Reaja com ✅ para participar do sorteio!\n\n"
                "Quando todos estiverem prontos, clique no botão **Sortear**."
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Iniciado por {interaction.user.display_name}")

        view = SortearView()
        await interaction.response.send_message(embed=embed, view=view)

        # Add the ✅ reaction to the message
        msg = await interaction.original_response()
        await msg.add_reaction("✅")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeamCog(bot))
