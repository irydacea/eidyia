#!/usr/bin/python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

import discord
from src.valen.V1Report import Report as V1Report


STATUS_COLOURS = {
    # Yucky grey because we really don't know what's going on.
    V1Report.FacilityStatus.STATUS_UNKNOWN:    (127, 127, 127),
    # Everyone loves pastel colours. Everyone worth listening to, anyhow.
    V1Report.FacilityStatus.STATUS_FAIL:       (255, 127, 127),
    V1Report.FacilityStatus.STATUS_GOOD:       (127, 255, 127),
    V1Report.FacilityStatus.STATUS_INCOMPLETE: (255, 200, 127),
    # Not pastel because this one is BAD.
    V1Report.FacilityStatus.STATUS_DNS_IS_BAD: (255, 150, 63),
}

STATUS_CAPTIONS = {
    V1Report.FacilityStatus.STATUS_UNKNOWN:    'Unknown',
    V1Report.FacilityStatus.STATUS_FAIL:       'Offline',
    V1Report.FacilityStatus.STATUS_GOOD:       'Online',
    V1Report.FacilityStatus.STATUS_INCOMPLETE: 'Issues',
    V1Report.FacilityStatus.STATUS_DNS_IS_BAD: 'DNS Error',
}

STATUS_EMOJI = {
    # Question mark because we really don't know what's going on.
    V1Report.FacilityStatus.STATUS_UNKNOWN:    ':grey_question:',
    # Squares look pretty.
    V1Report.FacilityStatus.STATUS_FAIL:       ':red_square:',
    V1Report.FacilityStatus.STATUS_GOOD:       ':green_square:',
    V1Report.FacilityStatus.STATUS_INCOMPLETE: ':yellow_square:',
    V1Report.FacilityStatus.STATUS_DNS_IS_BAD: ':orange_square:',
}


def status_to_discord_presence(status: V1Report.FacilityStatus) -> discord.Status:
    '''
    "Converts" a status code to a Discord presence status.
    '''
    if status in (V1Report.FacilityStatus.STATUS_INCOMPLETE, V1Report.FacilityStatus.STATUS_DNS_IS_BAD):
        return discord.Status.idle
    elif status in (V1Report.FacilityStatus.STATUS_FAIL, V1Report.FacilityStatus.STATUS_UNKNOWN):
        return discord.Status.do_not_disturb
    else:
        return discord.Status.online


def _status_ui_value(table, entry_key):
    '''
    Helper for table retrieval.
    '''
    # Yeah, we assume STATUS_UNKNOWN is in the table, that's the whole point.
    return table.get(entry_key, table[V1Report.FacilityStatus.STATUS_UNKNOWN])


def status_to_discord_colour(status: V1Report.FacilityStatus) -> discord.Colour:
    '''
    Returns a Discord colour value for formatting purposes to match a
    given Valen facility status value.
    '''
    colour = _status_ui_value(STATUS_COLOURS, status)
    return discord.Colour.from_rgb(colour[0], colour[1], colour[2])


def status_to_caption(status: V1Report.FacilityStatus) -> str:
    '''
    Returns a caption for a given Valen facility status value.
    '''
    return _status_ui_value(STATUS_CAPTIONS, status)


def status_to_discord_emoji(status: V1Report.FacilityStatus) -> str:
    '''
    Returns an emoji (actually an emoji shorthand) for a facility status value.
    '''
    return _status_ui_value(STATUS_EMOJI, status)


def log_guild_channel(guild: discord.Guild, channel) -> str:
    '''
    Formats a Discord guild/channel object pair (including their ids) for logging.
    '''
    return f'{guild} - #{channel} ({guild.id}/{channel.id})'
