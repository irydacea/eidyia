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
from typing import List, Optional, Tuple

from src.valen.V1Report import Report as V1Report
from src.valen.V1Report import StatusDiff as V1StatusDiff
from src.eidyia.config import EidyiaConfig, EidyiaReportMode
from src.eidyia.subscriber_api import Beholder as EidyiaBeholder
from src.eidyia.subscriber_api import Subscriber as EidyiaSubscriber
from src.eidyia.thread_utils import ConcurrentFlag
import src.eidyia.ui_utils as eidyia_ui_utils


# Discord status text used during the initial sync
INITIAL_DISCORD_STATUS = '(connecting...)'

#
# Internal parameters - do NOT change
#

MONITOR_LOOP_INTERVAL_SECS = 1  # 5

# Do NOT change this unless Discord changes their limits or formatting

MAX_DISCORD_EMBED_FIELDS = 25
DISCORD_EMBED_COLS = 3

EidyiaChannelList = List[Tuple[int, int]]

log = logging.getLogger('DiscordClient')


class EidyiaDiscordClient(discord.Client, EidyiaSubscriber):
    '''
    Main Eidyia application class.
    '''
    class UnsupportedError(Exception):
        '''
        Exception type thrown if we run into an unsupported scenario.
        '''
        def __init__(self, message):
            self.message = message

    def __init__(self, config: EidyiaConfig, *args, **kwargs):
        '''
        Constructor.

        It sets the bot's intents and initial Discord status and activity.
        Note that setting the filesystem event handler needs to be done
        separately after construction (see the connect_observer() method).

        Arguments:
            config: A dict (from JSON) containing Eidyia's user configuration.
        '''
        self._report = None
        self._old_report = None
        self._force_full_report_once = False
        self._beholder = None
        self._task = None
        self._should_refresh = ConcurrentFlag()
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
                                       name=INITIAL_DISCORD_STATUS)
        super().__init__(*args,
                         **kwargs,
                         intents=discord.Intents(guilds=True),
                         activity=initial_act,
                         status=discord.Status.online)

    def __enter__(self):
        '''
        Allocates resources (currently unused).
        '''
        return self

    def __exit__(self, *args, **kwargs):
        '''
        Releases resources such as filesystem monitoring objects.
        '''
        if self._beholder is not None:
            self._beholder.stop()

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

    def attach_report(self, report: V1Report):
        '''
        Connects a monitoreable Valen report into this instance.

        This needs to be called before running the bot.
        '''
        if self._report is not None:
            raise EidyiaDiscordClient.UnsupportedError('double attach attempt')
        self._report = report
        self._beholder = EidyiaBeholder(report.filename(), self)
        self._beholder.attach()
        log.debug('eidyia is subscribed to valen report')

    async def eidyia_monitoring_task(self):
        '''
        Performs monitoring tasks and reacts to their results.
        '''
        # In reality, monitoring is done on a separate thread by a watchdog
        # observer, and the event handler notifies the main thread back if
        # there is anything we need to do, by using a locking ConcurrentFlag
        # object.
        if self._beholder is None:
            # Somehow we ran before monitoring was fully set up. This should
            # never happen.
            raise EidyiaDiscordClient.UnsupportedError('invalid task configuration')
        await self.wait_until_ready()

        # Ready to go!
        self._beholder.start()
        log.debug('Report file monitoring started')

        unhandled_exc_count = 0

        while not self.is_closed() and self._beholder.active():
            if self._should_refresh.get():
                try:
                    await self.do_refresh()
                except Exception:
                    extra = ''
                    if unhandled_exc_count == 0:
                        extra = ' (first chance)'
                    elif unhandled_exc_count == 1:
                        extra = ' (second chance)'
                    else:
                        extra = ' (unbound)'
                    log.critical(f'Unhandled exception in Eidyia monitoring task{extra}')
                    log.exception(
                        '\n'
                        '***\n'
                        '*** Unhandled exception\n'
                        '***\n\n')
                    if unhandled_exc_count >= 2:
                        raise
                    else:
                        unhandled_exc_count += 1
                finally:
                    # Ensure we don't try to reload again until next
                    # event-mandated refresh even if we got here after an
                    # exception raised by the reload or chat post process.
                    self._should_refresh.clear()
            await asyncio.sleep(MONITOR_LOOP_INTERVAL_SECS)

    async def do_refresh(self):
        '''
        Performs a report reload and broadcast, or broadcasts an error message
        if something failed.

        Only exceptions reload to the report file or posting to Discord are
        handled and cancelled here. Other exceptions will be propagated to the
        caller to handle as desired.
        '''
        log.debug('Reloading report file from monitor trigger')
        self._old_report = self._report.clone()
        verbose_post_next = False
        try:
            # except clauses in this block may raise exceptions too due to
            # attempting to interact with Discord to inform errors.
            try:
                self._report.reload()
                log.info('Broadcasting new status report')
                await self.do_broadcast_report_update()
            except V1Report.FileError as report_err:
                log.error(f'Could not reload report file. {report_err}')
                # While Report.reload() does attempt to ensure
                # consistency in this situation, it probably makes
                # more sense to start fresh on the next update without
                # a diff.
                verbose_post_next = True
                log.info('Broadcasting report error status')
                await self.do_broadcast_report_error()
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
                self._old_report = None
                self._force_full_report_once = True

    async def setup_hook(self):
        '''
        Performs setup of the monitoring task.

        This does NOT launch the monitoring task. Monitoring commences only
        once the client runs the couroutine for the first time.
        '''
        self._task = self.loop.create_task(self.eidyia_monitoring_task())

    async def on_connect(self):
        '''
        Handles the on_connect event.
        '''
        log.info('Connected to Discord')

    async def on_ready(self):
        '''
        Handles the on_ready event.
        '''
        log.info(f'Joined Discord as {self.user}, broadcasting initial status report')
        self._force_full_report_once = True  # First report is always in full
        await self.do_broadcast_report_update()

    def on_eidyia_update(self):
        '''
        Handles the on_eidyia_update event.
        '''
        # The eidyia_monitoring_task coroutine (running in a different thread)
        # regularly checks this flag and triggers a report reload and repost
        # as needed. We don't need to do anything else here.
        self._should_refresh.set()

    async def do_broadcast_report_update(self):
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
        discord_report = self.format_report(show_greens=detailed,
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
                log.info(f'Sending report to {eidyia_ui_utils.log_guild_channel(guild, channel)}')
                await channel.send(embed=discord_report)
        # Once everything is done without errors for the fifrst time, we are
        # ready to proceed with differential reports.
        self._force_full_report_once = False

    async def do_broadcast_report_error(self):
        '''
        Sends an error notification to channels, if allowed by configuration.

        This is only to be used when an error occurred while retrieving or
        parsing a report.
        '''
        if self.config.discord.report_mode == EidyiaReportMode.REPORT_OPTIONAL_DIFF:
            # The users are probably not interested. Hoping someone watches
            # the console logs!
            return

        text = 'An error occurred while reading the status report. ' \
               'Check the console logs for details.'
        colour = eidyia_ui_utils.status_to_discord_colour(V1Report.FacilityStatus.STATUS_UNKNOWN)
        embed = discord.Embed(colour=colour,
                              description=text,
                              timestamp=datetime.datetime.now())
        embed.set_author(name=self.config.status_title,
                         icon_url=self.config.status_site_icon,
                         url=self.config.status_site_url)
        for guild_id, channel_id in self.report_channels:
            guild = self.get_guild(guild_id)
            channel = self.get_channel(channel_id)
            log.info(f'Sending error notification to {eidyia_ui_utils.log_guild_channel(guild, channel)}')
            await channel.send(embed=embed)

    def format_report(self,
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
        diff = None
        # Diff magic coming straight from the source
        if use_diff and self._old_report is not None:
            diff = V1StatusDiff(self._old_report, self._report)
            if strict_changes_only and not diff.has_changes():
                return None
        else:
            # HACK to obtain a "diff" for use with the common loop below
            diff = V1StatusDiff(self._report, None)

        overall_status = self._report.status_summary()
        summary_label = eidyia_ui_utils.status_to_caption(overall_status)
        summary_emoji = eidyia_ui_utils.status_to_discord_emoji(overall_status)
        summary_padding = '\u00a0' * 28  # Kinda arbitrary and desktop-centric
        embed_colour = eidyia_ui_utils.status_to_discord_colour(overall_status)
        post_ts = datetime.datetime.fromtimestamp(self._report.last_refresh())

        lines = [f'**Overall Status**{summary_padding}{summary_emoji} {summary_label}']
        fields = []

        # TODO: use data from hidden facilities in a DNS report like the one
        #       the web frontend produces as of this writing (June 2023).
        hidden_compromised = 0

        # Check if there are DNS-impacted instances. If we find any, include a
        # notice right after the overall status.
        for facility in self._report.facilities():
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
                status_text = eidyia_ui_utils.status_to_caption(entry['status'])
                emoji = eidyia_ui_utils.status_to_discord_emoji(entry['status'])
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

        if len(fields) > MAX_DISCORD_EMBED_FIELDS:
            # In case we run out of fields somehow (must be a catastrophic
            # situation if we do, huh)
            lines.append(f'({MAX_DISCORD_EMBED_FIELDS - len(fields)} additional facilities not shown)')
            fields = fields[:MAX_DISCORD_EMBED_FIELDS]
        elif len(fields) > DISCORD_EMBED_COLS \
                and len(fields) % DISCORD_EMBED_COLS != 0:
            # Discord will center-align rows of fields that have less than the
            # maximum number of columns, which means we end up with a wonky
            # last row... unless we add empty fields for padding
            # FIXME: This is a bad idea because mobile uses a single column...
            padding_count = DISCORD_EMBED_COLS - len(fields) % DISCORD_EMBED_COLS
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
        status = self._report.status_summary()
        act = discord.Activity(type=self.config.discord.activity,
                               name=self.config.discord.status)
        discord_status = eidyia_ui_utils.status_to_discord_presence(status)

        await self.change_presence(activity=act, status=discord_status)
        log.info('Updated Discord presence according to report')
