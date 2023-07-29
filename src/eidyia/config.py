#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

from dataclasses import dataclass, field
import discord
import getpass
from enum import IntEnum
import jsonc_parser.errors
import jsonc_parser.parser
import logging
from typing import Any, List, Optional, Self, Union


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

# Default IRC nick
IRC_NICK = 'eidyia'

# Default IRC username
IRC_USERNAME = 'eidyia'

# Default IRC real name/gecos
IRC_REALNAME = 'Eidyia IRC Client - https://status.wesnoth.org/'

# Text used in IRC when a DNS issue has been found
IRC_DNS_NOTICE = '\x02\x0307WARNING:\x0f DNS issues reported for some facilities. This warrants \x02immediate\x02 attention.'

# IRC bot command prefix
IRC_BOT_COMMAND_PREFIX = '%'

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

    @dataclass
    class IrcConfig:
        '''
        Values stored by the EidyiaConfig.irc property.
        '''
        nick: Union[str, List[str]] = IRC_NICK
        username: str = IRC_USERNAME
        realname: str = IRC_REALNAME

        server_addr: str = ''
        server_port: int = 6667
        server_tls: bool = False
        server_password: str = ''

        use_sasl: bool = False
        sasl_username: Optional[str] = None
        sasl_password: Optional[str] = None

        login_commands: list[list[str]] = field(default_factory=list)
        autojoin_delay_secs: float = 0.0
        channels: list[str] = field(default_factory=list)
        admins: list[str] = field(default_factory=list)

        command_prefix: str = IRC_BOT_COMMAND_PREFIX
        privmsg_channels: bool = True
        dns_notice: str = IRC_DNS_NOTICE
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
            if not isinstance(self.discord.token, str) or not self.discord.token:
                raise EidyiaConfig.ConfigError('discord.token must be a non-empty string value')

            guilds = self._get('discord.guilds')
            if not isinstance(guilds, dict) or not guilds:
                raise EidyiaConfig.ConfigError('discord.guilds must be a non-empty object')

            for gid, channels in guilds.items():
                if not isinstance(channels, (tuple, list)) or not channels:
                    raise EidyiaConfig.ConfigError(f'Guild configuration for {gid} must be a non-empty list of channels')
                self.discord.guilds[int(gid)] = [int(cid) for cid in channels]
                log.info(f'* Configured guild {gid}')

        # IRC client configuration items

        if 'irc' not in self._data \
           or not isinstance(self._data['irc'], dict):
            log.warning('Missing or invalid "irc" configuration block')
            self._irc = None
        else:
            self._irc = EidyiaConfig.IrcConfig()
            nick = self._get('irc.nick', None)
            if nick is None:
                log.warning('irc.nick is not set, this is not recommended')
                nick = self._default_username()
            elif not isinstance(nick, (str, list, tuple)):
                raise EidyiaConfig.ConfigError('irc.nick must be a string or list of strings')
            elif isinstance(nick, (list, tuple)) and not all(isinstance(n, str) for n in nick):
                raise EidyiaConfig.ConfigError('irc.nick must contain strings only if it is a list')
            self.irc.nick = nick
            self.irc.username = self._get('irc.username', IRC_USERNAME)
            self.irc.realname = self._get('irc.realname', IRC_REALNAME)

            self.irc.server_addr = self._get('irc.server_address')
            self.irc.server_port = self._get('irc.server_port', self.irc.server_port)
            if not self.irc.server_addr or not self.irc.server_port:
                raise EidyiaConfig.ConfigError('Invalid irc.server_address or irc.server_port')
            server_tls = self._get('irc.server_tls', None)
            if server_tls is None:  # Educated guess from port number
                server_tls = True if self.irc.server_port == 6697 else self.irc.server_tls
            self.irc.server_tls = server_tls
            self.irc.server_password = self._get('irc.server_password')

            self.irc.use_sasl = self._get('irc.use_sasl', self.irc.use_sasl)
            self.irc.sasl_username = self._get('irc.sasl_username', self.irc.sasl_username)
            self.irc.sasl_password = self._get('irc.sasl_password', self.irc.sasl_password)

            self.irc.autojoin_delay_secs = self._get('irc.autojoin_delay', self.irc.autojoin_delay_secs)
            self.irc.login_commands = self._get('irc.login_commands', self.irc.login_commands)
            if not isinstance(self.irc.login_commands, list) or (
               self.irc.login_commands and not all(isinstance(cmd, list) for cmd in self.irc.login_commands)):
                raise EidyiaConfig.ConfigError('irc.login_commands must be a list of lists of strings')
            for cmd in self.irc.login_commands:
                if not all(isinstance(param, str) for param in cmd):
                    raise EidyiaConfig.ConfigError('irc.login_commands must be a list of lists of strings')

            channels = self._get('irc.channels', self.irc.channels)
            if isinstance(channels, str):
                channels = [channels]
            elif not isinstance(channels, (list, tuple)) or not all(isinstance(c, str) for c in channels):
                raise EidyiaConfig.ConfigError('irc.channels must be a string or list of strings')
            self.irc.channels = channels

            admins = self._get('irc.admins', self.irc.admins)
            if isinstance(admins, str):
                admins = [admins]
            elif not isinstance(admins, (list, tuple)) or not all(isinstance(n, str) for n in admins):
                raise EidyiaConfig.ConfigError('irc.admins must be a string or list of strings')
            self.irc.admins = admins

            self.irc.command_prefix = self._get('irc.command_prefix', self.irc.command_prefix)
            if not isinstance(self.irc.command_prefix, str) or not self.irc.command_prefix:
                raise EidyiaConfig.ConfigError('irc.command_prefix must be a non-empty string if specified')
            self.irc.privmsg_channels = self._get('irc.privmsg_channels', self.irc.privmsg_channels)
            self.irc.dns_notice = self._get('irc.dns_notice', self.irc.dns_notice)
            self.irc.report_mode = EidyiaReportMode.from_json(self._get('irc.changes_only', True))

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

    def _default_username(self) -> str:
        '''
        Obtains a default user/nickname for use on platforms such as IRC.

        The result is obtained from the executing environment (the name of the
        user running the process). If that information is somehow unavailable,
        a default placeholder is returned instead.
        '''
        try:
            return getpass.getuser()
        except Exception:  # Whoops?!
            return 'Eidyia01'

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

    @property
    def irc(self) -> Optional['EidyiaConfig.IrcConfig']:
        '''
        Accesses IRC configuration properties if IRC was configured.
        '''
        return self._irc
