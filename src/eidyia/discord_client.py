#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

import asyncio
import datetime
import discord
import logging
from typing import List, NoReturn, Optional, Tuple

from src.valen.V1Report import Report as V1Report
from src.valen.V1Report import StatusDiff as V1StatusDiff
from src.eidyia.config import EidyiaConfig, EidyiaReportMode
from src.eidyia.core import eidyia_core, eidyia_critical_section
from src.eidyia.subscriber_api import EidyiaAsyncClient
import src.eidyia.ui_utils as ui


# Discord status text used during the initial sync
_INITIAL_DISCORD_STATUS = '(connecting...)'

#
# Internal parameters - do NOT change
#

_MONITOR_LOOP_INTERVAL_SECS = 1

# Do NOT change this unless Discord changes their limits or formatting

_MAX_DISCORD_EMBED_FIELDS = 25
_DISCORD_EMBED_COLS = 3

EidyiaChannelList = List[Tuple[int, int]]

log = logging.getLogger('DiscordClient')


class EidyiaDiscordClient(discord.Client, EidyiaAsyncClient):
    '''
    Main Eidyia Discord client class.

    Use the Task() factory coroutine to construct an instance of this class
    that runs forever until interrupted by an external signal.
    '''
    class UnsupportedError(Exception):
        '''
        Exception type thrown if we run into an unsupported scenario.
        '''
        def __init__(self, message: str):
            self.message = message

    @staticmethod
    async def Task(config: EidyiaConfig) -> NoReturn:
        '''
        Static method used as the initial coroutine for EidyiaCore.
        '''
        async with EidyiaDiscordClient(config) as discord_client:
            await discord_client.start()

    def __init__(self, config: EidyiaConfig, *args, **kwargs):
        '''
        Constructor.

        Use the Task() factory instead.
        '''
        log.info('Initialising')

        self._task = None
        self._force_full_report_once = False
        self._report_channels: EidyiaChannelList = []
        self._config = config

        if not self.config.discord:
            raise EidyiaDiscordClient.UnsupportedError('No Discord configuration provided')

        for gid, channels in self.config.discord.guilds.items():
            self._report_channels += [(gid, cid) for cid in channels]

        # Use a temporary Discord presence while we are setting things up. The
        # routine that broadcasts the initial status report will take it from
        # here later.
        # NOTE: Discord doesn't allow bots to use 'custom' activities, sadly.
        initial_act = discord.Activity(type=self.config.discord.activity,
                                       name=_INITIAL_DISCORD_STATUS)
        super().__init__(*args,
                         **kwargs,
                         intents=discord.Intents(guilds=True),
                         activity=initial_act,
                         status=discord.Status.online)
        super(discord.Client, self).__init__()

    async def __aenter__(self):
        # Eidyia first because it
        await super(discord.Client, self).__aenter__()
        await super().__aenter__()
        return self

    async def __aexit__(self, *args, **kwargs):
        await super(discord.Client, self).__aexit__(*args, **kwargs)
        await super().__aexit__(*args, **kwargs)

    async def start(self, reconnect: bool = True) -> None:
        '''
        Starts the bot.
        '''
        await self.login(self.config.discord.token)
        await self.connect(reconnect=reconnect)

    @property
    def config(self) -> EidyiaConfig:
        '''
        Accesses configuration properties from EidyiaConfig.
        '''
        return self._config

    @property
    def report_channels(self) -> EidyiaChannelList:
        '''
        List of Discord channels where reports should be posted.

        The result is a list of pairs of ints where the first item is the
        guild id and the second item is the channel id.
        '''
        return self._report_channels

    async def _eidyia_subscription_task(self):
        '''
        Watches for and handles Eidyia subscription updates.
        '''
        await self.wait_until_ready()
        while not self.is_closed():
            verbose_post_next = False
            try:
                if self.eidyia_update.get():
                    log.info('Broadcasting new status report')
                    await self.broadcast_report()
            except discord.ConnectionClosed as err:
                # At some point we'll reconnect. Because we may have missed a
                # whole report update, next update should be a full report.
                # TODO: schedule a post for as soon as we reconnect again
                verbose_post_next = True
                log.error(f'Discord connection terminated during refresh: {err}')
            except (discord.Forbidden,
                    discord.NotFound,
                    discord.GatewayNotFound,
                    discord.DiscordServerError) as err:
                # Same as above, people may be missing out on important report
                # updates while we are unable to post.
                verbose_post_next = True
                log.error(f'Unable to post to Discord: {err}')
            finally:
                if verbose_post_next:
                    log.warning('Next broadcast will be a full report due to errors')
                    self._force_full_report_once = True
                self.eidyia_update.clear()
                await asyncio.sleep(_MONITOR_LOOP_INTERVAL_SECS)

    @eidyia_critical_section
    async def broadcast_report(self):
        # Got an error enqueued?
        if eidyia_core().report_error is not None:
            await self._do_broadcast_report_error()
            return
        # Handle a normal report
        await self._do_broadcast_report_update()

    async def setup_hook(self):
        '''
        Performs setup of the Eidyia subscription task.
        '''
        self._task = self.loop.create_task(self._eidyia_subscription_task())

    async def on_connect(self):
        '''
        Handles the on_connect event.
        '''
        log.info('Connected to Discord')

    @eidyia_critical_section
    async def on_ready(self):
        '''
        Handles the on_ready event.
        '''
        log.info(f'Joined Discord as {self.user}, broadcasting initial status report')
        self._force_full_report_once = True  # First report is always in full
        await self._do_broadcast_report_update()

    async def _do_broadcast_report_update(self):
        '''
        Sends a report update out to channels.
        '''
        detailed = use_diff = strict_changes_only = None
        if not self._force_full_report_once:
            if self.config.discord.report_mode == EidyiaReportMode.REPORT_MINIMAL_DIFF:
                detailed = False
                use_diff = True
                strict_changes_only = False
            elif self.config.discord.report_mode == EidyiaReportMode.REPORT_OPTIONAL_DIFF:
                detailed = False
                use_diff = True
                strict_changes_only = True
            else:  # REPORT_ALWAYS_FULL
                detailed = True
                use_diff = False
                strict_changes_only = False
        else:
            detailed = True
            use_diff = False
            strict_changes_only = False

        log.debug('Updating presence and preparing report')
        await self.update_presence()
        discord_report = self._format_report(show_greens=detailed,
                                             use_diff=use_diff,
                                             strict_changes_only=strict_changes_only)
        if discord_report is None:
            if strict_changes_only:
                log.info('No changes since last report, skipping')
            else:
                log.critical('No report generated despite skip_unchanged=False, '
                             'potentially invalid report or Eidyia bug!')
        else:
            for guild_id, channel_id in self.report_channels:
                guild = self.get_guild(guild_id)
                channel = self.get_channel(channel_id)
                log.info(f'Sending report to {ui.log_guild_channel(guild, channel)}')
                await channel.send(embed=discord_report)
        # Once everything is done without errors for the fifrst time, we are
        # ready to proceed with differential reports.
        self._force_full_report_once = False

    async def _do_broadcast_report_error(self):
        '''
        Sends an error notification to channels, if allowed by configuration.

        This is only to be used when an error occurred while retrieving or
        parsing a report.
        '''
        if self.config.discord.report_mode == EidyiaReportMode.REPORT_OPTIONAL_DIFF:
            # The users are probably not interested. Hoping someone watches
            # the console logs!
            return

        text = eidyia_core().report_error
        colour = ui.status_to_discord_colour(V1Report.FacilityStatus.STATUS_UNKNOWN)
        embed = discord.Embed(colour=colour,
                              description=text,
                              timestamp=datetime.datetime.now())
        embed.set_author(name=self.config.status_title,
                         icon_url=self.config.status_site_icon,
                         url=self.config.status_site_url)
        for guild_id, channel_id in self.report_channels:
            guild = self.get_guild(guild_id)
            channel = self.get_channel(channel_id)
            log.info(f'Sending error notification to {ui.log_guild_channel(guild, channel)}')
            await channel.send(embed=embed)

    def _format_report(self,
                       show_greens: bool = False,
                       use_diff: bool = True,
                       strict_changes_only: bool = False,
                       include_hidden: bool = False,
                       use_fields: bool = True) -> Optional[discord.Embed]:
        '''
        Generates a Discord embed from the current status report.

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
        summary_label = ui.status_to_caption(overall_status)
        summary_emoji = ui.status_to_discord_emoji(overall_status)
        summary_padding = '\u00a0' * 28  # Kinda arbitrary and desktop-centric
        embed_colour = ui.status_to_discord_colour(overall_status)
        post_ts = datetime.datetime.fromtimestamp(core.report.last_refresh())

        lines = [f'**Overall Status**{summary_padding}{summary_emoji} {summary_label}']
        fields = []

        # TODO: use data from hidden facilities in a DNS report like the one
        #       the web frontend produces as of this writing (June 2023).
        hidden_compromised = 0

        # Check if there are DNS-impacted instances. If we find any, include a
        # notice right after the overall status.
        for facility in core.report.facilities():
            if facility.status == V1Report.FacilityStatus.STATUS_DNS_IS_BAD \
               or [inst for inst in facility.instances if inst.status == V1Report.FacilityStatus.STATUS_DNS_IS_BAD]:
                lines += ['', self.config.discord.dns_notice]
                break

        #
        # Proceed with the report or report diff (common loop)
        #

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
                name = entry['name']
                status_text = ui.status_to_caption(entry['status'])
                emoji = ui.status_to_discord_emoji(entry['status'])
                if use_fields:
                    fields.append({
                        'name': name,
                        'value': f'{emoji} {status_text}'
                        })
                else:
                    lines.append(f'* {emoji} **{name}:** {status_text}')

        #
        # Finishing up the report embed
        #

        if len(fields) > _MAX_DISCORD_EMBED_FIELDS:
            # In case we run out of fields somehow (must be a catastrophic
            # situation if we do, huh)
            lines.append(f'({_MAX_DISCORD_EMBED_FIELDS - len(fields)} additional facilities not shown)')
            fields = fields[:_MAX_DISCORD_EMBED_FIELDS]
        elif len(fields) > _DISCORD_EMBED_COLS \
                and len(fields) % _DISCORD_EMBED_COLS != 0:
            # Discord will center-align rows of fields that have less than the
            # maximum number of columns, which means we end up with a wonky
            # last row... unless we add empty fields for padding
            # FIXME: This is a bad idea because mobile uses a single column...
            padding_count = _DISCORD_EMBED_COLS - len(fields) % _DISCORD_EMBED_COLS
            fields += [{'name': '', 'value': ''}] * padding_count

        if hidden_compromised > 0:
            # Not security through obscurity, they just tend to have dumb or
            # nonsensical names... (TODO: maybe allow manually requesting more
            # info through menus?)
            lines.append(f'({hidden_compromised} hidden facilities impacted)')

        embed = discord.Embed(colour=embed_colour,
                              description='\n'.join(lines),
                              timestamp=post_ts)
        embed.set_author(name=self.config.status_title,
                         icon_url=self.config.status_site_icon,
                         url=self.config.status_site_url)
        for field in fields:
            embed.add_field(name=field['name'], value=field['value'])

        return embed

    async def update_presence(self):
        '''
        Updates Discord activity status to reflect the Valen report.
        '''
        status = eidyia_core().report.status_summary()
        act = discord.Activity(type=self.config.discord.activity,
                               name=self.config.discord.status)
        discord_status = ui.status_to_discord_presence(status)

        await self.change_presence(activity=act, status=discord_status)
        log.info('Updated Discord presence according to report')

