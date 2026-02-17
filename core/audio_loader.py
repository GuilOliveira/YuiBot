import discord
import yt_dlp
import asyncio
import functools
import typing
import os
import logging

logger = logging.getLogger(__name__)

# Suppress noise about console usage from errors
yt_dlp.utils.bug_reports_message = lambda *args, **kwargs: ''


class YTDLSource(discord.PCMVolumeTransformer):
    """Audio source using yt-dlp for extraction and FFmpeg for streaming."""

    YTDL_OPTIONS: dict = {
        'format': 'bestaudio/best',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'ytsearch',
        'source_address': '0.0.0.0',
    }

    # Only use cookies if the file exists
    if os.path.exists('cookies.txt'):
        YTDL_OPTIONS['cookiefile'] = 'cookies.txt'

    FFMPEG_OPTIONS: dict = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5) -> None:
        super().__init__(source, volume)
        self.data: dict = data
        self.title: typing.Optional[str] = data.get('title')
        self.url: typing.Optional[str] = data.get('webpage_url') or data.get('url')
        self.stream_url: typing.Optional[str] = data.get('url')
        self.duration: typing.Optional[int] = data.get('duration')
        self.thumbnail: typing.Optional[str] = data.get('thumbnail')
        self.requester: typing.Optional[discord.User] = data.get('requester')

    @property
    def duration_formatted(self) -> str:
        """Returns duration as MM:SS string."""
        if self.duration is None:
            return "N/A"
        minutes, seconds = divmod(self.duration, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @classmethod
    async def create_source(
        cls,
        search: str,
        *,
        requester: discord.User,
        loop: typing.Optional[asyncio.AbstractEventLoop] = None,
    ) -> 'YTDLSource':
        """Creates a YTDLSource from a search query or URL.
        
        Uses run_in_executor to avoid blocking the event loop.
        Does NOT download the file — streams directly via FFmpeg.
        """
        loop = loop or asyncio.get_event_loop()

        # Step 1: Search / extract metadata (no processing yet)
        partial_extract = functools.partial(
            cls.ytdl.extract_info, search, download=False, process=False
        )
        data = await loop.run_in_executor(None, partial_extract)

        if data is None:
            raise ValueError(f"Couldn't find anything matching `{search}`")

        # If it's a playlist/search result, take the first entry
        if 'entries' in data:
            entries = list(data['entries'])
            if not entries:
                raise ValueError(f"Couldn't find anything matching `{search}`")
            process_info = entries[0]
        else:
            process_info = data

        # Step 2: Get the actual stream URL
        webpage_url = process_info.get('url') or process_info.get('webpage_url')
        if not webpage_url:
            raise ValueError(f"Couldn't extract a valid URL for `{search}`")

        partial_process = functools.partial(
            cls.ytdl.extract_info, webpage_url, download=False
        )
        processed_data = await loop.run_in_executor(None, partial_process)

        if processed_data is None:
            raise ValueError(f"Couldn't process audio for `{search}`")

        if 'entries' in processed_data:
            processed_data = processed_data['entries'][0]

        # Add requester info to data
        processed_data['requester'] = requester

        stream_url = processed_data.get('url')
        if not stream_url:
            raise ValueError(f"Couldn't get stream URL for `{search}`")

        logger.info(f"Created source: {processed_data.get('title', 'Unknown')}")

        return cls(
            discord.FFmpegPCMAudio(stream_url, **cls.FFMPEG_OPTIONS),
            data=processed_data,
        )
