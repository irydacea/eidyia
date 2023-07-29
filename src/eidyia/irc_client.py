#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

import asyncio
import datetime
from irctokens import build as ircbuild
from irctokens import Hostmask as IrcHostmask
from irctokens import Line as IrcLine
from ircrobots import Bot as IrcBot
from ircrobots import ConnectionParams as IrcConnectionParams
from ircrobots import SASLUserPass as IrcSASLUserPass
from ircrobots import Server as IrcServer
import logging
from typing import Dict, List, Optional, Union

from src.valen.V1Report import Report as V1Report
from src.valen.V1Report import StatusDiff as V1StatusDiff
from src.eidyia.config import EidyiaConfig, EidyiaReportMode
from src.eidyia.core import eidyia_core, eidyia_critical_section
from src.eidyia.subscriber_api import EidyiaSubscriber
import src.eidyia.ui_utils as ui


# Split long status lines past this byte count
_IRC_SPLIT_THRESHOLD = 190

# Maximum columns for table displays on IRC
_IRC_SPLIT_TABLE_COLUMNS = 3


#
# Internal parameters - do NOT change
#

_MONITOR_LOOP_INTERVAL_SECS = 1

HostmaskType = Union[IrcHostmask, str]

log = logging.getLogger('IrcClient')
ircraw = logging.getLogger('IrcClient.raw')


def u8bytecount(u8string: str) -> int:
    '''
    Returns the byte count of a UTF-8 string.
    '''
    return len(u8string.encode('utf-8'))


class EidyiaIrcController(IrcServer):
    '''
    Implementation of the IRC aspect of the Eidyia IRC Client.
    '''
    def __init__(self,
                 bot: 'EidyiaIrcClient',
                 name: str):
        '''
        Constructor.

        Arguments:
            bot             Parent client object.
            name            Server identifier.
        '''
        super().__init__(bot, name)
        # TODO initialise command registry here

    def is_admin(self, account_name: str) -> bool:
        if self.bot.admins and account_name:
            for admin in self.bot.admins:
                if self.casefold_equals(admin, account_name):
                    return True
        return False

    async def line_read(self, line: IrcLine):
        '''
        Handles IRC input from server.
        '''
        ircraw.debug(f'{self.name} < {line.format()}')
        if line.command == '001':
            await self.handle_registration_complete()
            return
        if line.command == 'JOIN':
            await self.handle_join(line.hostmask, line.params[0])
            return
        if line.command == 'PRIVMSG':
            await self.handle_privmsg(line.tags, line.hostmask, line.params[0], line.params[1])
            return

    async def line_send(self, line: IrcLine):
        '''
        Handles IRC output to server.
        '''
        if line.command == 'AUTHENTICATE':
            # Exclude SASL authentication stuff from logs
            redacted = IrcLine(line.tags, line.source, line.command, [])
            ircraw.debug(f'{self.name} > {redacted.format()} {"*" * 16}')
        else:
            ircraw.debug(f'{self.name} > {line.format()}')

    async def do_send_response(self, target: str, message: str):
        '''
        Sends a bot response message to a target.
        '''
        await self.send(ircbuild('NOTICE', [target, message]))

    async def handle_registration_complete(self):
        '''
        Handles completion of IRC registration (001 numeric).
        '''
        log.info(f'Connected to {self.name}')
        for cmd in self.bot.config.irc.login_commands:
            if not len(cmd):
                continue
            log.debug(f'Sending login_commands item: {" ".join(cmd)}')
            await self.send(ircbuild(cmd[0], cmd[1:]))
        if isinstance(self.bot.config.irc.autojoin_delay_secs, (float, int)) \
           and self.bot.config.irc.autojoin_delay_secs > 0:
            log.debug(f'autojoin_delay set to {self.bot.config.irc.autojoin_delay_secs}, sleeping')
            await asyncio.sleep(self.bot.config.irc.autojoin_delay_secs)
        # Rejoin channels
        for channel in self.bot.report_channels:
            await self.send(ircbuild('JOIN', [channel]))

    async def handle_join(self,
                          source: IrcHostmask,
                          channel: str):
        '''
        Handles JOIN.
        '''
        if channel not in self.bot.report_channels:
            return
        if not self.casefold_equals(source.nickname, self.nickname):
            return
        # Send a full report to the joined channel. Because we are just
        # joining now, we will always do a full report here if it's the first
        # time in our lifetime we joined the channel.
        await self.bot.broadcast_report(single_channel=channel,
                                        first_time_only=True)
        # This is a good place to do this for the first time. (The method will
        # guarantee that the subscription is setup exactly once.)
        await self.bot.setup_subscription()

    async def handle_privmsg(self,
                             tags: Optional[Dict[str, str]],
                             source: IrcHostmask,
                             target: str,
                             message: str):
        '''
        Handles PRIVMSG.
        '''
        if not self.bot.admins:
            # No admins configured, nothing we can ever do with PRIVMSGs in
            # this case... :c
            return
        account = tags.get('account') if tags else None
        if not self.is_admin(account):
            return
        user_label = f'{source} ({account})'
        cmd = message.lower()
        channel = target
        if self.casefold_equals(target, self.nickname):
            # Private message
            if message.startswith(self.bot.config.irc.command_prefix):
                cmd = cmd[1:]
            channel = source.nickname
            req_type = 'private message from {channel}'
        else:
            # Channel message
            if not cmd.startswith(self.bot.config.irc.command_prefix):
                return
            cmd = cmd[1:]
            req_type = f'channel message in {target}'
        if cmd == 'report':
            log.debug(f'{user_label} requested full report via {req_type}')
            await self.bot.broadcast_report(
                single_channel=channel)
        elif cmd == 'diff':
            log.debug(f'{user_label} requested differential report via {req_type}')
            await self.bot.broadcast_report(
                single_channel=channel, force_diff=True)
        elif cmd == 'version':
            log.debug(f'{user_label} requested version number')
            await self.do_send_response(
                channel, f'codename "Eidyia" version {eidyia_core().version}')
        elif cmd == 'ping':
            log.debug(f'{user_label} pinged us')
            await self.do_send_response(
                channel, 'Pong!')
        else:
            await self.do_send_response(
                channel, f'Unrecognised command: {cmd}')


class EidyiaIrcClient(IrcBot, EidyiaSubscriber):
    '''
    Main Eidyia IRC client class.
    '''
    class UnsupportedError(Exception):
        '''
        Exception type thrown if we run into an unsupported scenario.
        '''
        def __init__(self, message: str):
            self.message = message

    def __init__(self, config: EidyiaConfig):
        '''
        Constructor.
        '''
        log.debug('Initialising')

        self._task = None
        self._force_full_report_once = False
        self._first_time_channels = set()
        self._config = config

        if not self.config.irc:
            raise EidyiaIrcClient.UnsupportedError('No IRC configuration provided')

        fallbacks = []
        nick = self.config.irc.nick
        if isinstance(nick, (list, tuple)):
            if len(nick) > 1:
                fallbacks = nick[1:]
            nick = nick[0]

        server_pass = self.config.irc.server_password \
            if self.config.irc.server_password else None
        sasl = IrcSASLUserPass(self.config.irc.sasl_username,
                               self.config.irc.sasl_password) \
            if self.config.irc.use_sasl else None

        self._conn_params = IrcConnectionParams(
            self.config.irc.nick,
            alt_nicknames=fallbacks,
            username=self.config.irc.username,
            realname=self.config.irc.realname,
            host=self.config.irc.server_addr,
            port=self.config.irc.server_port,
            password=server_pass,
            sasl=sasl)

        super().__init__()
        super(IrcBot, self).__init__()

    @property
    def config(self) -> EidyiaConfig:
        '''
        Accesses configuration properties from EidyiaConfig.
        '''
        return self._config

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        '''
        Returns the current async event loop.
        '''
        return asyncio.get_running_loop()

    @property
    def report_channels(self) -> List[str]:
        '''
        Returns a list of IRC channels to report to.
        '''
        return self._config.irc.channels

    @property
    def admins(self) -> List[str]:
        '''
        Returns a list of admin account names on IRC.
        '''
        return self._config.irc.admins

    @property
    def server(self) -> EidyiaIrcController:
        '''
        Returns the IRC server controller instance associated to this client.

        While the base class DOES support multiple servers, we do not use
        more than one server at a time in practice. This property always
        retrieves the very last server created ever.
        '''
        return self._eidyia_irc_controller

    def create_server(self, name: str) -> IrcServer:
        '''
        Implementation detail.
        '''
        self._eidyia_irc_controller = EidyiaIrcController(self, name)
        return self._eidyia_irc_controller

    async def setup_connections(self):
        # Use hostname as a label for lack of a better option without making
        # client configuration more complicated.
        await self.add_server(self.config.irc.server_addr, self._conn_params)

    async def setup_subscription(self):
        '''
        Sets up the Eidyia subscription.
        '''
        if not self._task:
            asyncio.get_running_loop()
            self._task = self.loop.create_task(self._eidyia_subscription_task())

    async def _eidyia_subscription_task(self):
        '''
        Watches for and handles Eidyia subscription updates.
        '''
        while True:
            verbose_post_next = False
            try:
                if self.eidyia_update.get():
                    await self._check_irc_connection()
                    log.info('Broadcasting new status report')
                    await self.broadcast_report()
            except Exception as err:
                # Same as above, people may be missing out on important report
                # updates while we are unable to post.
                verbose_post_next = True
                log.error(f'Unable to post to IRC: {err}')
            finally:
                if verbose_post_next:
                    log.warning('Next broadcast will be a full report due to errors')
                    self._force_full_report_once = True
                self.eidyia_update.clear()
                await asyncio.sleep(_MONITOR_LOOP_INTERVAL_SECS)

    async def _check_irc_connection(self) -> bool:
        '''
        Checks (actually stalls) for an active IRC connection.

        Currently always returns True.
        '''
        # If we are disconnected from IRC, stall here until we reconnect.
        # We don't want things to get stupid. We do this here instead of in
        # the Eidyia critical section because we don't want to force other
        # clients to stall waiting for us to reconnect.
        # (FIXME: maybe we should though?)
        am_disconnected_warning = False
        while self.server.disconnected:
            if not am_disconnected_warning:
                log.warning('Stalling broadcast until we reconnect to IRC')
                am_disconnected_warning = True
            await asyncio.sleep(1)
        return True

    @eidyia_critical_section
    async def broadcast_report(
            self,
            single_channel: Optional[str] = None,
            force_diff: bool = False,
            first_time_only: bool = False
            ):
        '''
        Posts a report update, or an error message if the last report update
        failed for some reason.

        NOTE: This executes in a critical section. If we are already executing
        in one this will deadlock. Use _do_broadcast_report_update() and
        _do_broadcast_report_error() directly if calling from a critical
        section.

        Arguments:
            single_channel  Specifies a single channel to send the report to,
                            instead of the usual set of channels. If this is
                            specified, the report will be forcefully sent in
                            its full form instead of using report diffing.
                            (Note: strictly speaking this doesn't NEED to be
                            an actual channel, it can also be a nickname to
                            send a private message to.)
            force_diff      Forces a differential report to be posted if True.
            first_time_only If True, generates and posts a report to the
                            specified channel only once ever in the lifetime
                            of this EidyiaIrcClient.
        '''
        # Got an error enqueued?
        if eidyia_core().report_error is not None:
            await self._do_broadcast_report_error(
                single_channel=single_channel)
            return
        # Handle a normal report
        await self._do_broadcast_report_update(
            single_channel=single_channel,
            force_diff=force_diff,
            first_time_only=first_time_only)

    async def _do_broadcast_report_update(
            self,
            single_channel: Optional[str] = None,
            force_diff: bool = False,
            first_time_only: bool = False
            ):
        '''
        Implementation detail of broadcast_report
        '''
        if first_time_only:
            if single_channel in self._first_time_channels:
                return
            else:
                self._first_time_channels.add(single_channel)
        detailed = use_diff = strict_changes_only = None
        channels = self.config.irc.channels if not single_channel else [single_channel]
        if not single_channel and not self._force_full_report_once:
            if self.config.irc.report_mode == EidyiaReportMode.REPORT_MINIMAL_DIFF:
                detailed = False
                use_diff = True
                strict_changes_only = False
            elif self.config.irc.report_mode == EidyiaReportMode.REPORT_OPTIONAL_DIFF:
                detailed = False
                use_diff = True
                strict_changes_only = True
            else:  # REPORT_ALWAYS_FULL
                detailed = True
                use_diff = False
                strict_changes_only = False
        else:
            detailed = True
            use_diff = force_diff
            strict_changes_only = False

        log.debug('Preparing report')
        report_lines = self._format_report(show_greens=detailed,
                                           use_diff=use_diff,
                                           strict_changes_only=strict_changes_only)
        if report_lines is None:
            if strict_changes_only:
                log.info('No changes since last report, skipping')
            else:
                log.critical('No report generated despite skip_unchanged=False, '
                             'potentially invalid report or Eidyia bug!')
        else:
            for channel in channels:
                log.info(f'Sending report to {channel}')
                for line in report_lines:
                    await self.server.do_send_response(channel, line)
        # Once everything is done without errors for the fifrst time, we are
        # ready to proceed with differential reports.
        self._force_full_report_once = False

    def _format_report(
            self,
            show_greens: bool = False,
            use_diff: bool = True,
            strict_changes_only: bool = False,
            include_hidden: bool = False
            ) -> List[str]:
        '''
        Generates a list of IRC lines from the current status report.

        If use_diff is False or there isn't a previous report available, the
        full set of facilities is reported according to the other options.
        Otherwise, if use_diff is True and there is a previous report
        available, a report will only be produced if there have been changes
        since said report, unless detailed is True as well.

        If use_diff is False and detailed is True, the report will include
        facilities with a good status code, instead of only listing
        facilities with problems).

        If include_hidden is True, hidden facilities are included in the
        listing. This is not advisable because their names tend to suck and
        use DNS-only probes instead of something more meaningful.
        '''
        core = eidyia_core()
        diff = None
        # Diff magic coming straight from the source
        if use_diff and core.previous_report is not None:
            diff = V1StatusDiff(core.previous_report, core.report)
            if strict_changes_only and not diff.has_changes():
                return None
        else:
            # HACK to obtain a "diff" for use with the common loop below
            diff = V1StatusDiff(core.report, None)

        overall_status = core.report.status_summary()
        summary_colour = ui.status_to_irc_colour(overall_status)
        summary_label = summary_colour.apply(ui.status_to_caption(overall_status))
        summary_icon = ui.status_to_irc_icon(overall_status)
        post_ts = datetime.datetime.fromtimestamp(core.report.last_refresh())

        lines = [f'{ui.IrcFormat.BOLD.apply("Overall Status:")} '
                 f'{summary_icon} {summary_label} '
                 f'{ui.IrcColour.GREY.apply(f"(last update: {post_ts})")}']

        # TODO: use data from hidden facilities in a DNS report like the one
        #       the web frontend produces as of this writing (June 2023).
        hidden_compromised = 0

        # Check if there are DNS-impacted instances. If we find any, include a
        # notice right after the overall status.
        for facility in core.report.facilities():
            if facility.status == V1Report.FacilityStatus.STATUS_DNS_IS_BAD \
               or [inst for inst in facility.instances if inst.status == V1Report.FacilityStatus.STATUS_DNS_IS_BAD]:
                lines.append(self.config.irc.dns_notice)
                break

        #
        # Proceed with the report or report diff (common loop)
        #
        fields = []

        for facility in diff.facilities():
            # Regular per-facility reporting
            am_green = facility.status_after == V1Report.FacilityStatus.STATUS_GOOD \
                       and facility.status_after == facility.status_before
            if am_green and not show_greens:
                continue
            if facility.hidden and not include_hidden:
                if not am_green:
                    hidden_compromised += 1
                continue
            # Break facility down into component instances if possible so that
            # they can be featured separately in the report.
            facility_parts = []
            if not am_green and facility.instances_diff:
                # Break facility down into its component instances
                for inst in facility.instances_diff:
                    green_inst = inst.status_after == V1Report.FacilityStatus.STATUS_GOOD \
                                 and inst.status_after == inst.status_before
                    if green_inst and not show_greens:
                        continue
                    facility_parts.append({
                        'name': f'{facility.name}/{inst.id}',
                        'status': inst.status_after,
                        })
            else:
                facility_parts.append({
                    'name': facility.name,
                    'status': facility.status_after,
                    })
            for entry in facility_parts:
                name = f'{entry["name"]}:'
                status_colour = ui.status_to_irc_colour(entry['status'])
                status_text = status_colour.apply(ui.status_to_caption(entry['status']))
                icon = ui.status_to_irc_icon(entry['status'])
                fields.append(f'{ui.IrcFormat.BOLD.apply(name)} {icon} {status_text}')

        #
        # Format for IRC, splitting into separate lines if needed
        #
        message_line = ''
        field_count = 0
        for field in fields:
            field_count += 1
            if field_count > _IRC_SPLIT_TABLE_COLUMNS \
               or u8bytecount(message_line) + u8bytecount(field) + 1 >= _IRC_SPLIT_THRESHOLD:
                lines.append(message_line)
                message_line = f' {field}'
                field_count = 1
            else:
                message_line += f' {field}'
        if message_line:
            lines.append(message_line)

        if hidden_compromised > 0:
            # Not security through obscurity, they just tend to have dumb or
            # nonsensical names... (TODO: maybe allow manually requesting more
            # info through bot commands?)
            lines.append(f'({hidden_compromised} hidden facilities impacted)')

        return lines

    async def _do_broadcast_report_error(
            self,
            single_channel: Optional[str] = None
            ):
        '''
        Sends an error notification to channels, if allowed by configuration.

        This is only to be used when an error occurred while retrieving or
        parsing a report.
        '''
        if self.config.irc.report_mode == EidyiaReportMode.REPORT_OPTIONAL_DIFF \
           and not single_channel:
            # The users are probably not interested. Hoping someone watches
            # the console logs!
            return
        channels = self.config.irc.channels if not single_channel else [single_channel]
        report = eidyia_core().report_error
        text = f'{ui.IrcFormat.COLOUR}{ui.IrcColour.RED}{report}'
        for channel in channels:
            log.info(f'Sending error notification to {channel}')
            await self.server.do_send_response(channel, text)
