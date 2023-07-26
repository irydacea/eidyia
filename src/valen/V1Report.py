#!/usr/bin/python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

from dataclasses import dataclass
from enum import IntEnum
import json
import time
from typing import Dict, List, Optional, Self, Union

# Default report refresh interval. This should be a value greater than zero
# in general just to avoid any shenanigans if somehow Valen forgets to give us
# a valid value (but in that case you could argue we are going to be in
# massive trouble regardless, so...)
DEFAULT_REFRESH_INTERVAL = 900

# A value for a bad/default IP address. It should be invalid.
NULL_IP = '0.0.0.0'

# A value for a bad/default hostname. It should be unresolvable.
NULL_HOSTNAME = NULL_IP


class Report:
    '''
    Valen V1 (actually 0.x) report class.

    This is used to interpret a Valen V1 JSON report file.

    Valen's architecture allows a lot of flexibility for front-ends, which do
    not need to know the exact facility definitions used by the back-end as
    those will be provided to them inline in the report. Not the most optimal
    thing in terms of data, but it's not a big deal for us since Wesnoth.org
    consists of only a couple of hosts and only a dozen services.
    '''

    class FormatError(Exception):
        '''
        Exception type thrown if a report format error is found.
        '''
        def __init__(self, message):
            self.message = message

    class FileError(Exception):
        '''
        Exception type thrown if a report file cannot be read.
        '''
        def __init__(self, message):
            self.message = message

    class FacilityStatus(IntEnum):
        '''
        Represents possible Valen V1 facility status codes.

        Codes:
            STATUS_UNKNOWN:             Unknown facility status (possible local configuration error)
            STATUS_FAIL:                Facility is not running properly
            STATUS_GOOD:                Facility is running properly
            STATUS_INCOMPLETE:          Facility is online but not all instances are running properly
            STATUS_DNS_IS_BAD:          Facility DNS has been compromised or misconfigured
        '''
        STATUS_UNKNOWN = -1
        STATUS_FAIL = 0
        STATUS_GOOD = 1
        STATUS_INCOMPLETE = 2
        STATUS_DNS_IS_BAD = 3

        def good(self) -> bool:
            '''
            Returns True if this is STATUS_GOOD, False otherwise.
            '''
            return self == self.STATUS_GOOD

    @dataclass
    class FacilityInfoLink:
        '''
        Represents an informational facility front-end link.

        Attributes:
            title:                      User-friendly link title
            url:                        Target URL
        '''
        title:              str
        url:                str

        def __init__(self, from_dict: Dict = {}):
            if not isinstance(from_dict, dict):
                raise Report.FormatError('FacilityInfoLink input is not a dict')

            self.title = from_dict.get('title', '')
            self.url = from_dict.get('url', '#')

        def clone(self) -> Self:
            '''
            Returns a copy of this FacilityInfoLink.
            '''
            res = Report.FacilityInfoLink()
            res.title = self.title
            res.url = self.url
            return res

    @dataclass
    class FacilityInstance:
        '''
        Represents a facility instance.

        A facility instance is usually part of the same physical facility, and
        therefore Valen does not provide IP addresses or hostnames for them.
        Instances are differentiated in V1 by their port number and 'id'
        (actually a user-friendly name).

        Attributes:
            id:                         Instance user-friendly name
            port:                       Instance port number
            status:                     Instance status code
            response_time:              Last probe response time in milliseconds
        '''
        id:                 str
        port:               int
        status:             int
        response_time:      float

        def __init__(self, from_dict: Dict = {}):
            if not isinstance(from_dict, dict):
                raise Report.FormatError('FacilityInstance input is not a dict')

            self.id = from_dict.get('id', '')
            self.port = from_dict.get('port', 0)
            status = int(from_dict.get('status', Report.FacilityStatus.STATUS_UNKNOWN))
            if status not in iter(Report.FacilityStatus):
                raise Report.FormatError('Instance status is not a valid FacilityStatus')
            self.status = Report.FacilityStatus(status)
            self.response_time = from_dict.get('response_time', 0.0)

        def clone(self) -> Self:
            '''
            Returns a copy o fthis facility instance.
            '''
            res = Report.FacilityInstance()
            res.id = self.id
            res.port = self.port
            res.status = self.status
            res.response_time = self.response_time
            return res

    @dataclass
    class Facility:
        '''
        Represents a facility report.

        Attributes:
            name:                       Facility user-friendly name
            desc:                       Facility user-friendly description
            hidden:                     Whether to hide the facility in front-ends
            status:                     Facility status code
            hostname:                   Canonical hostname for the facility
            expected_ip:                Expected facility host IP address
            dns_ip:                     Resolved facility host IP address
            response_time:              Last probe response time in milliseconds
            links:                      Front-end links for informational purposes
            instances:                  List of instances which are part of this facility
        '''

        name:               str
        desc:               str
        hidden:             bool

        status:             'Report.FacilityStatus'
        response_time:      float
        hostname:           str
        expected_ip:        str
        dns_ip:             str

        instances:          List['Report.FacilityInstance']
        links:              List[Dict[str, str]]

        def __init__(self, from_dict: Dict = {}):
            if not isinstance(from_dict, dict):
                raise Report.FormatError('Facility input is not a dict')

            self.name = from_dict.get('name', '')
            self.desc = from_dict.get('desc', '')
            # Valen V1 is Perl, so it has no concept of boolean values. We
            # read ints instead. :(
            hidden = from_dict.get('hidden', 0)
            self.hidden = True if hidden else False

            # Process instances first so we know their statuses
            instances_list = from_dict.get('instances', [])
            if not isinstance(instances_list, (list, tuple)):
                raise Report.FormatError('Facility input has an "instances" value that is not a list or null')
            self.instances = []
            for instance_entry in instances_list:
                self.instances.append(Report.FacilityInstance(instance_entry))

            # Now process our own status (or instance statuses)
            if self.instances and 'status' not in from_dict:
                # Status for this parent facility is computed from instances.
                # See Report.summarize_status() for more information on
                # the logic used here.
                self.status = Report.FacilityStatus.STATUS_GOOD
                bad_instances = 0
                unknown_instances = 0
                for instance in self.instances:
                    if instance.status == Report.FacilityStatus.STATUS_DNS_IS_BAD:
                        self.status = Report.FacilityStatus.STATUS_DNS_IS_BAD
                    elif instance.status == Report.FacilityStatus.STATUS_FAIL:
                        self.status = Report.FacilityStatus.STATUS_INCOMPLETE
                        bad_instances = bad_instances + 1
                    elif instance.status == Report.FacilityStatus.STATUS_UNKNOWN:
                        self.status = Report.FacilityStatus.STATUS_INCOMPLETE
                        unknown_instances = unknown_instances + 1
                if bad_instances == len(self.instances):
                    self.status = Report.FacilityStatus.STATUS_FAIL
                if unknown_instances == len(self.instances):
                    self.status = Report.FacilityStatus.STATUS_UNKNOWN
            else:
                status = int(from_dict.get('status', Report.FacilityStatus.STATUS_UNKNOWN))
                if status not in iter(Report.FacilityStatus):
                    raise Report.FormatError('Facility status is not a valid FacilityStatus')
                self.status = Report.FacilityStatus(status)

            self.response_time = from_dict.get('response_time', 0.0)

            self.hostname = from_dict.get('hostname', NULL_HOSTNAME)
            self.expected_ip = from_dict.get('expected_ip', NULL_IP)
            self.dns_ip = from_dict.get('dns_ip', NULL_IP)

            # Process front-end links
            links_list = from_dict.get('links', [])
            if not isinstance(links_list, (list, tuple)):
                raise Report.FormatError('Facility input has a "links" value that is not a list or null')
            self.links = []
            for link_entry in links_list:
                self.links.append(Report.FacilityInfoLink(link_entry))

        def clone(self) -> Self:
            '''
            Returns a copy of this facility.
            '''
            res = Report.Facility()
            res.name = self.name
            res.desc = self.desc
            res.hidden = self.hidden
            res.response_time = self.response_time
            res.hostname = self.hostname
            res.expected_ip = self.expected_ip
            res.dns_ip = self.dns_ip
            # Explicit object copies next
            res.status = Report.FacilityStatus(self.status)
            for instance in self.instances:
                res.instances.append(instance.clone())
            for link in self.links:
                res.links.append(link.clone())
            return res

    def __init__(self, filename):
        '''
        Constructor.
        '''
        self._filename = filename
        self._facilities = []
        # NOTE: these are for reference purposes in case we want a poll-based
        #       implementation at some point.
        self._timestamp = 0
        self._refresh_interval = DEFAULT_REFRESH_INTERVAL
        # NOTE: this will be kept around for debugging only
        self._data = {}
        self.reload()

    def filename(self) -> str:
        '''
        Returns the filename associated with this report.
        '''
        return self._filename

    def facilities(self) -> List['Report.Facility']:
        '''
        Returns a list of facilities (as in Facility objects).

        Pls promise not to modify this.
        '''
        return self._facilities

    def last_refresh(self) -> int:
        '''
        Returns the timestamp of the last refresh.
        '''
        return self._timestamp

    def next_refresh(self) -> int:
        '''
        Returns the timestamp of the next expected refresh according to the
        timestamp and interval recorded in the Valen report.
        '''
        return self._timestamp + self._refresh_interval

    def maybe_outdated(self) -> bool:
        '''
        Returns if it has been at least 'refresh_interval' seconds since the
        timestamp recorded in the Valen report and therefore the caller should
        consider performing an explicit reload.
        '''
        return self.next_refresh() <= time.time()

    def reload(self, filename=None) -> bool:
        '''
        Reads the report from disk.

        If a new filename is passed, that will permanently repalce the
        filename provided during construction.

        Returns True or False depending on whether the file read and parse was
        successful.
        '''
        if filename is not None:
            self._filename = filename
        if not self._filename:
            return False
        try:
            with open(self._filename, mode='r', encoding='utf-8') as file:
                self._data = json.load(file)
                if not self._data or not isinstance(self._data, dict):
                    raise Report.FormatError('Empty or invalid report file')

                #
                # Basic parameters
                #
                self._refresh_interval = self._data.get('refresh_interval', DEFAULT_REFRESH_INTERVAL)
                self._timestamp = self._data.get('ts', 0)

                #
                # Process facilities from JSON
                #
                facilities = []
                facilities_json = self._data.get('facilities', [])
                if not facilities_json or not isinstance(facilities_json, (list, tuple)):
                    raise Report.FormatError('Facility list ("facilities") is invalid, empty, or not a list')
                for facility_json in facilities_json:
                    # Magic!
                    facilities.append(Report.Facility(facility_json))

                # No more validation done here. We assume good faith from Valen at
                # all times because we kinda got told she's a good character and
                # not evil and we just assume that to be true for some reason...?
                self._facilities = facilities
                return True
        except (OSError, json.JSONDecodeError) as err:
            # Oopsies we did a boo-boo (or maybe Valen did, who knows)
            raise Report.FileError(f'Cannot read report file: {err}')
        return False

    def status_summary(self) -> 'Report.FacilityStatus':
        '''
        Returns a "summary" status value for all facilities of this report.
        '''
        return self.summarize_status(self._facilities)

    @staticmethod
    def summarize_status(facilities: Union['Report.Facility', List['Report.Facility']]) -> 'Report.FacilityStatus':
        '''
        Returns a "summary" status value for one or more facilities.
        '''

        # If you don't give me facilities I guess you want STATUS_UNKNOWN. (?)
        if not facilities:
            return Report.FacilityStatus.STATUS_UNKNOWN

        # The facility's summary status was computed while reading its report.
        if isinstance(facilities, Report.Facility):
            return facilities.status

        # "Summary" status logic for a plural set (for Valen V1 only):
        #
        #  * We start with STATUS_GOOD by default.
        #
        #  * STATUS_DNS_IS_BAD taints the whole thing until we find something
        #    worse.
        #
        #  * The first STATUS_FAIL or STATUS_UNKNOWN turns the whole set's
        #    status into STATUS_INCOMPLETE. If we can confirm that the whole
        #    set is afflicted by the same status, that becomes the set's
        #    general status as well.
        status_summary = Report.FacilityStatus.STATUS_GOOD

        bad_facilities = 0
        unknown_facilities = 0

        for facility in facilities:
            if facility.status == Report.FacilityStatus.STATUS_DNS_IS_BAD:
                status_summary = Report.FacilityStatus.STATUS_DNS_IS_BAD
            elif facility.status == Report.FacilityStatus.STATUS_FAIL:
                status_summary = Report.FacilityStatus.STATUS_INCOMPLETE
                bad_facilities = bad_facilities + 1
            elif facility.status == Report.FacilityStatus.STATUS_UNKNOWN:
                status_summary = Report.FacilityStatus.STATUS_INCOMPLETE
                unknown_facilities = unknown_facilities + 1
        if bad_facilities == len(facilities):
            status_summary = Report.FacilityStatus.STATUS_FAIL
        if unknown_facilities == len(facilities):
            status_summary = Report.FacilityStatus.STATUS_UNKNOWN

        return status_summary

    def clone(self) -> Self:
        '''
        Returns a copy of this report.

        The returned copy does not have a filename attached even if the
        original does. The rationale is that the copy becomes an object of its
        own and should not be able to be reloaded from its source. Clones
        have free will!

        Additionally, the copy does not have its original JSON data attached
        either. I could call it an optimisation measure (no data bloat yay)
        but the truth is simply that I'm too lazy to write a deep copy routine
        for this. We REALLY do not use the data outside of the reload() method
        at the moment anyway.
        '''
        res = Report(None)

        res._timestamp = self._timestamp
        res._refresh_interval = self._refresh_interval

        # Clone facilities
        for facility in self._facilities:
            # This is a deep copy so we're good
            res._facilities.append(facility.clone())

        return res


class StatusDiff:
    '''
    Valen V1 report diff class.

    Represents a 'diff' between two Valen V1 reports, which can be used to
    determine what changed between two reports.

    Note that you won't find fancy algorithms here. If the structure appears
    to have changed between two reports (different number of facilities or
    their names or the number and names of their respective instances changed)
    you'll just be told that everything changed and that's it.

    Otherwise, this will normally list facilities and instances whose
    respective statuses changed between both reports.
    '''

    @dataclass
    class FacilityInstanceDiff:
        id:                     str
        status_before:          Report.FacilityStatus
        status_after:           Report.FacilityStatus
        response_time_before:   float
        response_time_after:    float

    @dataclass
    class FacilityDiff:
        name:                   str
        hidden:                 bool
        status_before:          Report.FacilityStatus
        status_after:           Report.FacilityStatus
        response_time_before:   float
        response_time_after:    float
        instances_diff:         Optional[List['StatusDiff.FacilityInstanceDiff']]

    @dataclass
    class _FacilityInstanceSummary:
        '''
        Internal type used for generating diffs of facility instance lists.
        '''
        id:                     str
        status:                 Report.FacilityStatus
        response_time:          float

        def __init__(self, instance: Report.FacilityInstance):
            '''
            Constructs a summary out of a FacilityInstance.
            '''
            self.id = instance.id
            self.status = instance.status
            self.response_time = instance.response_time

    @dataclass
    class _FacilitySummary:
        '''
        Internal type used for generating diffs of facility lists.
        '''
        name:                   str
        hidden:                 bool
        status:                 Report.FacilityStatus
        response_time:          float
        dns_ip:                 str
        instances:              Optional[List['StatusDiff._FacilityInstanceSummary']]

        def __init__(self, facility: Report.Facility):
            '''
            Constructs a summary out of a Facility.
            '''
            self.name = facility.name
            self.hidden = facility.hidden
            self.status = facility.status
            self.response_time = facility.response_time
            self.dns_ip = facility.dns_ip
            self.instances = None
            if facility.instances:
                self.instances = [StatusDiff._FacilityInstanceSummary(instance) for instance in facility.instances]

        def has_same_instances(self, other: Self) -> bool:
            if type(self.instances) is not type(other.instances):
                return False
            return self.instances is None or (
                len(self.instances) == len(other.instances) and
                [a_inst.id for a_inst in self.instances] == [b_inst.id for b_inst in other.instances])

        def is_same_facility(self, other: Self) -> bool:
            return self.name == other.name and self.has_same_instances(other)

        def diff_to(self, other: Self) -> Optional[Union['StatusDiff.FacilityDiff', int]]:
            '''
            Generates a diff from this summary.

            If the facilities are incomparable, None is returned.
            '''
            if not self.is_same_facility(other):
                return None
            instances_diff = None
            if self.instances is not None:
                instances_diff = []
                for inst_num, instance_a in enumerate(self.instances):
                    instance_b = other.instances[inst_num]
                    if instance_a.status == instance_b.status:
                        continue
                    instances_diff.append(StatusDiff.FacilityInstanceDiff(
                        id=instance_a.id,
                        status_before=instance_a.status,
                        status_after=instance_b.status,
                        response_time_before=instance_a.response_time,
                        response_time_after=instance_b.response_time,
                    ))
            if self.status == other.status and (
               instances_diff is None or len(instances_diff) == 0):
                # FIXME: we need a clearer way to signal this
                return 0
            # We use other.hidden in case the hidden attribute of a facility
            # changed between reports
            return StatusDiff.FacilityDiff(
                name=self.name,
                hidden=other.hidden,
                status_before=self.status,
                status_after=other.status,
                response_time_before=self.response_time,
                response_time_after=other.response_time,
                instances_diff=instances_diff,
            )

    def __init__(self, before: Report, after: Optional[Report]):
        self._chaos = False
        self._facility_diff = []
        if after is None:
            # If we have nothing to do diff with then we just fill values and
            # call it a day (there's probably a good reason why we're doing
            # this, caller can deal with it)
            self._make_chaos(before)
            return
        if len(before.facilities()) != len(after.facilities()):
            # Everything's different maybe probably oops (sorry!)
            self._make_chaos(after)
            return
        if len(before.facilities()) == 0:
            # No facilities? :c
            return
        entries_a = [StatusDiff._FacilitySummary(fcl) for fcl in before.facilities()]
        entries_b = [StatusDiff._FacilitySummary(fcl) for fcl in after.facilities()]
        # Temporary until we confirm it's not chaos
        facility_diff = []
        for num, entry_a in enumerate(entries_a):
            # We know by now A and B have the same length
            entry_b = entries_b[num]
            diff = entry_a.diff_to(entry_b)
            if diff is None:
                # Things changed too much again, bye
                self._make_chaos(after)
                return
            if isinstance(diff, int):
                # It's probably a 0 (FIXME lol)
                continue
            facility_diff.append(diff)
        self._facility_diff = facility_diff

    def good(self) -> bool:
        '''
        Returns True if the diff was possible, False otherwise.

        A diff is considered impossible if essential attributes changed
        between both reports (e.g. facility or instance names).
        '''
        return not self._chaos

    def has_changes(self) -> bool:
        '''
        Returns True if there are any reported changes.
        '''
        return len(self._facility_diff) > 0 or self._chaos

    def facilities(self) -> List['StatusDiff.FacilityDiff']:
        '''
        Returns a list of per-facility differences.

        If the diff was not possible then an empty list is returned instead.
        '''
        return self._facility_diff

    def _make_chaos(self, report: Report):
        '''
        Makes a chaos diff.
        '''
        for facility in report.facilities():
            inst_fakediff = None
            if facility.instances:
                inst_fakediff = []
                for inst in facility.instances:
                    inst_fakediff.append(StatusDiff.FacilityInstanceDiff(
                        id=inst.id,
                        status_before=inst.status,
                        status_after=inst.status,
                        response_time_before=inst.response_time,
                        response_time_after=inst.response_time))
            fakediff = StatusDiff.FacilityDiff(
                name=facility.name,
                hidden=facility.hidden,
                status_before=facility.status,
                status_after=facility.status,
                response_time_before=facility.response_time,
                response_time_after=facility.response_time,
                instances_diff=inst_fakediff)
            self._facility_diff.append(fakediff)
        self._chaos = True
