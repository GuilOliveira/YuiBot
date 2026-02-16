import discord
from discord import app_commands
from discord.ext import commands
from core.audio_loader import YTDLSource
import asyncio
import typing
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Guild State — one per server
# ---------------------------------------------------------------------------
class GuildState:
    """Holds per-guild playback state: queue, current track, and timers."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[YTDLSource] = asyncio.Queue()
        self.current_track: typing.Optional[YTDLSource] = None
        self.now_playing_message: typing.Optional[discord.Message] = None
        self.disconnect_timer: typing.Optional[asyncio.Task] = None

    def clear(self) -> None:
        """Reset the queue and cancel any pending timers."""
        self.queue = asyncio.Queue()
        self.current_track = None
        self.now_playing_message = None
        if self.disconnect_timer and not self.disconnect_timer.done():
            self.disconnect_timer.cancel()
            self.disconnect_timer = None


# ---------------------------------------------------------------------------
# Interactive Buttons View
# ---------------------------------------------------------------------------
class MusicControlView(discord.ui.View):
    """Buttons attached to the "Now Playing" embed."""

    def __init__(self, cog: 'MusicCog', guild_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="Pause/Resume", style=discord.ButtonStyle.primary, emoji="⏯️")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = interaction.guild.voice_client
        if vc:
            if vc.is_paused():
                vc.resume()
                await interaction.response.send_message("▶️ Resumed.", ephemeral=True)
            elif vc.is_playing():
                vc.pause()
                await interaction.response.send_message("⏸️ Paused.", ephemeral=True)
            else:
                await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        else:
            await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()  # triggers after_playing → plays next
            await interaction.response.send_message("⏭️ Skipped.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to skip.", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state = self.cog.get_guild_state(self.guild_id)
        await self.cog.full_stop(interaction.guild, state)
        await interaction.response.send_message("⏹️ Stopped and disconnected.", ephemeral=True)


# ---------------------------------------------------------------------------
# Music Cog
# ---------------------------------------------------------------------------
class MusicCog(commands.Cog):
    """Slash-command based music player."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.guild_states: typing.Dict[int, GuildState] = {}

    # -- helpers -------------------------------------------------------------

    def get_guild_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState()
        return self.guild_states[guild_id]

    async def full_stop(self, guild: discord.Guild, state: GuildState) -> None:
        """Stop playback, clear queue, disconnect, and garbage-collect state."""
        state.clear()
        vc = guild.voice_client
        if vc:
            vc.stop()
            await vc.disconnect(force=True)
        if guild.id in self.guild_states:
            del self.guild_states[guild.id]

    async def ensure_voice(self, interaction: discord.Interaction) -> typing.Optional[discord.VoiceClient]:
        """
        Make sure the bot is connected to the user's voice channel.
        Returns the VoiceClient or None (and sends an error message).
        """
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You need to be in a voice channel first.")
            return None

        channel = interaction.user.voice.channel
        vc: typing.Optional[discord.VoiceClient] = interaction.guild.voice_client

        try:
            if vc is None:
                # Not connected at all — join
                vc = await channel.connect(self_deaf=True)
            elif vc.channel.id != channel.id:
                # Connected to a different channel — move
                await vc.move_to(channel)
            elif not vc.is_connected():
                # Stale client — disconnect and reconnect
                await vc.disconnect(force=True)
                vc = await channel.connect(self_deaf=True)
        except discord.ClientException:
            # "Already connected" edge case — reuse existing vc
            vc = interaction.guild.voice_client
            if vc and vc.channel.id != channel.id:
                await vc.move_to(channel)

        return vc

    # -- playback engine -----------------------------------------------------

    async def play_next(self, guild: discord.Guild, text_channel: discord.abc.Messageable) -> None:
        """Plays the next track from the queue, or starts the inactivity timer."""
        state = self.get_guild_state(guild.id)
        vc: typing.Optional[discord.VoiceClient] = guild.voice_client

        if not vc or not vc.is_connected():
            return

        if state.queue.empty():
            state.current_track = None
            # Start the 5-minute inactivity timer
            if state.disconnect_timer and not state.disconnect_timer.done():
                state.disconnect_timer.cancel()
            state.disconnect_timer = asyncio.create_task(
                self.disconnect_after_timeout(guild.id)
            )
            return

        # Cancel any pending disconnect
        if state.disconnect_timer and not state.disconnect_timer.done():
            state.disconnect_timer.cancel()
            state.disconnect_timer = None

        source = await state.queue.get()
        state.current_track = source

        def after_playing(error: typing.Optional[Exception]) -> None:
            if error:
                logger.error(f"Playback error in guild {guild.id}: {error}")
            coro = self.play_next(guild, text_channel)
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

        vc.play(source, after=after_playing)

        # Build "Now Playing" embed
        embed = discord.Embed(
            title="🎶 Now Playing",
            description=f"[{source.title}]({source.url})",
            color=discord.Color.blurple(),
        )
        if source.thumbnail:
            embed.set_thumbnail(url=source.thumbnail)
        embed.add_field(name="Duration", value=source.duration_formatted, inline=True)
        if source.requester:
            embed.add_field(name="Requested by", value=source.requester.mention, inline=True)

        view = MusicControlView(self, guild.id)
        try:
            await text_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to send now-playing embed: {e}")

    async def disconnect_after_timeout(self, guild_id: int) -> None:
        """Wait 5 minutes then auto-disconnect if nothing is playing."""
        await asyncio.sleep(300)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        state = self.guild_states.get(guild_id)
        if state is None:
            return
        vc = guild.voice_client
        if vc and vc.is_connected() and not vc.is_playing():
            await self.full_stop(guild, state)
            logger.info(f"Auto-disconnected from guild {guild_id} after 5min inactivity.")

    # -- slash commands ------------------------------------------------------

    @app_commands.command(name="play", description="🎵 Play a song from YouTube (URL or search).")
    @app_commands.describe(search="YouTube URL or search query")
    async def play(self, interaction: discord.Interaction, search: str) -> None:
        await interaction.response.defer()

        vc = await self.ensure_voice(interaction)
        if vc is None:
            return

        state = self.get_guild_state(interaction.guild_id)
        # Sync our state's reference
        state.voice_client = vc

        try:
            source = await YTDLSource.create_source(
                search, requester=interaction.user, loop=self.bot.loop,
            )
        except Exception as e:
            logger.error(f"Error creating source: {e}")
            await interaction.followup.send(f"❌ Could not load track: {e}")
            return

        await state.queue.put(source)

        if not vc.is_playing() and not vc.is_paused():
            await self.play_next(interaction.guild, interaction.channel)
            await interaction.followup.send(
                f"▶️ Now playing: **{source.title}**"
            )
        else:
            pos = state.queue.qsize()
            await interaction.followup.send(
                f"📥 Added to queue (#{pos + 1}): **{source.title}**"
            )

    @app_commands.command(name="skip", description="⏭️ Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped.")
        else:
            await interaction.response.send_message("Nothing is playing to skip.")

    @app_commands.command(name="stop", description="⏹️ Stop playback, clear queue, and disconnect.")
    async def stop(self, interaction: discord.Interaction) -> None:
        state = self.get_guild_state(interaction.guild_id)
        vc = interaction.guild.voice_client
        if vc:
            await self.full_stop(interaction.guild, state)
            await interaction.response.send_message("⏹️ Stopped and disconnected.")
        else:
            await interaction.response.send_message("I'm not connected to any voice channel.")

    @app_commands.command(name="queue", description="📋 Show the current music queue.")
    async def queue(self, interaction: discord.Interaction) -> None:
        state = self.get_guild_state(interaction.guild_id)

        # Build description
        lines: list[str] = []

        # Show current track
        if state.current_track:
            lines.append(
                f"**Now:** [{state.current_track.title}]({state.current_track.url}) "
                f"— {state.current_track.duration_formatted}"
            )

        # Show upcoming (peek into internal deque — read-only, safe)
        upcoming = list(state.queue._queue)  # type: ignore[attr-defined]
        if upcoming:
            lines.append("\n**Up next:**")
            for i, src in enumerate(upcoming[:10], 1):
                lines.append(f"`{i}.` [{src.title}]({src.url}) — {src.duration_formatted}")
            if len(upcoming) > 10:
                lines.append(f"… and **{len(upcoming) - 10}** more.")
        elif not state.current_track:
            await interaction.response.send_message("The queue is empty.")
            return

        embed = discord.Embed(
            title="📋 Music Queue",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
