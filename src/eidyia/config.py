#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

from dataclasses import dataclass, field
import discord
from enum import IntEnum
import jsonc_parser.errors
import jsonc_parser.parser
import logging
from typing import Any, Optional, Self


# Default Site Status site
STATUS_SITE_URL = 'https://status.wesnoth.org/'

# Site Status Site icon, used for Discord embeds
STATUS_SITE_ICON = 'https://status.wesnoth.org/wesmere/logo-minimal-64@2x.png'

# Title used for Discord embeds
STATUS_TITLE = 'Wesnoth.org Site Status Survey'

# Default Discord activity type - must be one of 'playing', 'streaming',
# 'listening' or 'watching'
DISCORD_ACTIVITY = 'watching'

# Discord status text
DISCORD_STATUS = 'status.wesnoth.org'

# Markdown text used in Discord embeds when a DNS issue has been found
DISCORD_DNS_NOTICE = ':warning: **WARNING:** One or more facilities or instances report DNS issues. While in the best case this could simply be the result of an Eidyia host misconfiguration, it could also be a consequence of a Wesnoth.org DNS provider issue, which warrants **immediate** attention.'

#
# Internal
#

log = logging.getLogger('config')


#
# Class definitions
#

class EidyiaReportMode(IntEnum):
    '''
    Update report modes.
    '''
    REPORT_MINIMAL_DIFF = 0
    REPORT_OPTIONAL_DIFF = 1
    REPORT_ALWAYS_FULL = 2

    @staticmethod
    def from_json(value: Any) -> Self:
        if isinstance(value, bool):
            if value is False:
                return EidyiaReportMode.REPORT_ALWAYS_FULL
            if value is True:
                return EidyiaReportMode.REPORT_MINIMAL_DIFF
        if isinstance(value, str):
            if value == 'strict':
                return EidyiaReportMode.REPORT_OPTIONAL_DIFF
        raise EidyiaConfig.ConfigError(f'Bad "changes_only" value {value}')


class EidyiaConfig:
    '''
    Common Eidyia configuration class.

    This is used to encapsulate shared configuration functionality between
    all Eidyia front-ends (Discord, IRC etc.).
    '''
    class ConfigError(Exception):
        '''
        Exception type thrown when an invalid configuration value is found.
        '''
        def __init__(self, message):
            self.message = message

    class FileError(Exception):
        '''
        Exception type thrown when the configuration file cannot be read.
        '''
        def __init__(self, message):
            self.message = message

    @dataclass
    class DiscordConfig:
        '''
        Values stored by the EidyiaConfig.discord property.
        '''
        token: Optional[str] = None
        guilds: dict[int, list] = field(default_factory=dict)

        activity: discord.ActivityType = discord.ActivityType.playing
        status: str = DISCORD_STATUS

        dns_notice: str = DISCORD_DNS_NOTICE
        report_mode: EidyiaReportMode = EidyiaReportMode.REPORT_MINIMAL_DIFF

    class _PlaceholderValue:
        '''
        Implementation detail used to signal a missing value.
        '''

    def __init__(self, config_path: str):
        '''
        Constructor.

        Reads Eidyia's configuration from disk.

        Arguments:
            config_path: Path to a config file in JSON or JSON-with-comments
                         format.
        '''
        try:
            self._data = jsonc_parser.parser.JsoncParser.parse_file(config_path)
        except (jsonc_parser.errors.FileError, jsonc_parser.errors.ParserError) as err:
            log.error(f'Could not read configuration from {config_path}: {err}')
            raise EidyiaConfig.FileError(config_path)
        if not isinstance(self._data, dict):
            raise RuntimeError('JSON data source is not a dict')

        self._config_path = config_path

        # Common configuration items

        self._status_title = self._get('status_title', STATUS_TITLE)
        self._status_site_url = self._get('status_site_url', STATUS_SITE_URL)
        self._status_site_icon = self._get('status_site_icon', STATUS_SITE_ICON)

        # Discord client configuration items

        if 'discord' not in self._data \
           or not isinstance(self._data['discord'], dict):
            log.warning('Missing or invalid "discord" configuration block')
            self._discord = None
        else:
            self._discord = EidyiaConfig.DiscordConfig()
            activity_types = {
                'playing':   discord.ActivityType.playing,
                'streaming': discord.ActivityType.streaming,
                'listening': discord.ActivityType.listening,
                'watching':  discord.ActivityType.watching,
                'custom':    discord.ActivityType.custom,
                'competing': discord.ActivityType.competing,
            }
            discord_activity = self._get('discord.activity', DISCORD_ACTIVITY)
            self.discord.activity = activity_types[discord_activity] \
                if discord_activity in activity_types else self.discord.activity
            self.discord.status = self._get('discord.status', self.discord.status)
            self.discord.dns_notice = self._get('discord.dns_notice', self.discord.dns_notice)
            self.discord.report_mode = EidyiaReportMode.from_json(self._get('discord.changes_only', True))
            self.discord.token = self._get('discord.token')

            guilds = self._get('discord.guilds')
            if not isinstance(guilds, dict) or not guilds:
                raise EidyiaConfig.ConfigError('discord.guilds must be a non-empty object')

            for gid, channels in guilds.items():
                if not isinstance(channels, (tuple, list)) or not channels:
                    raise EidyiaConfig.ConfigError(f'Guild configuration for {gid} must be a non-empty list of channels')
                self.discord.guilds[int(gid)] = [int(cid) for cid in channels]
                log.info(f'* Configured guild {gid}')

    def _get(self, key: str, default_value: Any = None) -> Any:
        '''
        Constructor helper.
        '''
        if not key:
            raise RuntimeError('EidyiaConfig._get(): bad key')
        parts = key.split('.')
        if len(parts) == 1:
            return self._data.get(key, default_value)
        placeholder = EidyiaConfig._PlaceholderValue()
        value, value_key = self._data, '<root>'
        for sub in parts:
            if value is placeholder:
                raise EidyiaConfig.ConfigError(f'Section {value_key} not found while fetching config value for {key}')
            value = value.get(sub, placeholder)
            value_key = sub
        return value if value is not placeholder else default_value

    @property
    def config_path(self):
        '''
        Returns the path used to load the config file.
        '''
        return self._config_path

    @property
    def status_title(self):
        '''
        Returns the long-form title used for status posts.
        '''
        return self._status_title

    @property
    def status_site_url(self):
        '''
        Returns the URL of the status report website.
        '''
        return self._status_site_url

    @property
    def status_site_icon(self):
        '''
        Returns the URL of an icon associated with the status report website.
        '''
        return self._status_site_icon

    @property
    def discord(self) -> Optional['EidyiaConfig.DiscordConfig']:
        '''
        Accesses Discord configuration properties if Discord was configured.
        '''
        return self._discord
