#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

import asyncio
import functools
import logging
from typing import List, Optional

from src.eidyia.subscriber_api import EidyiaBeholder, EidyiaSubscriber, EidyiaSystemListener
from src.eidyia.thread_utils import ConcurrentFlag
from src.valen.V1Report import Report as ValenReport


#
# Internal parameters - do NOT change
#

log = logging.getLogger('eidyia.core')
_instance = None
_corelock = asyncio.Lock()

_EIDYIA_VERSION = '0.0.2'
_MONITOR_LOOP_INTERVAL_SECS = 1


def eidyia_critical_section(func):
    '''
    Function decorator used to guarantee only one coroutine can access the
    global EidyiaCore instance at any given time.
    '''
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        global _corelock
        async with _corelock:
            return await func(*args, **kwargs)
    return wrapper


class EidyiaResourceSharingViolation(Exception):
    pass


class EidyiaNoTasksError(Exception):
    pass


class EidyiaCore(EidyiaSystemListener):
    '''
    Eidyia's monitoring and asynchronous I/O infrastructure core.

    This is a single object shared between all of Eidyia's clients (as of
    this writing, that is Discord and IRC) and encapsulates access to the
    underlying monitoring mechanism and the Valen report processing API.

    Owners are expected to use the subscriber API to be notified of relevant
    updates.
    '''
    version = _EIDYIA_VERSION

    def __init__(self, report_filename: str):
        '''
        Constructor
        '''
        global _instance
        if _instance is not None:
            log.critical('Only one (1) EidyiaCore allowed, like, ever')
            raise EidyiaResourceSharingViolation
        _instance = self
        super().__init__()

        self._debug: bool = False
        self._report: ValenReport = ValenReport(report_filename)
        self._old_report: Optional[ValenReport] = None
        self._report_error: Optional[str] = None

        self._valen_refreshed: ConcurrentFlag = ConcurrentFlag()
        self._async_items: dict = {}

        self._beholder: EidyiaBeholder = EidyiaBeholder(self, self.filename)
        self._beholder.attach()
        log.debug(f'subscribed to valen report from {self.filename}')

    @property
    def debug_core(self) -> bool:
        '''
        Whether core debugging functionality (including asyncio debug mode)
        should be enabled.
        '''
        return self._debug

    @debug_core.setter
    def debug_core(self, value: bool):
        '''
        Whether core debugging functionality (including asyncio debug mode)
        should be enabled.
        '''
        self._debug = value

    @property
    def beholder(self) -> EidyiaBeholder:
        '''
        Accesses the underlying Beholder.

        The Beholder will be automatically set up the first time this property
        is used.
        '''
        return self._beholder

    @property
    def filename(self) -> str:
        '''
        Returns the filename of the underlying Valen report.
        '''
        return self._report.filename()

    @property
    def report(self) -> ValenReport:
        '''
        Accesses the underlying Valen report.
        '''
        return self._report

    @property
    def previous_report(self) -> ValenReport:
        '''
        Accesses the previous Valen report.
        '''
        return self._old_report

    @property
    def report_error(self) -> str:
        '''
        Accesses the last Valen report refresh error if one occurred.
        '''
        return self._report_error

    def add_task(self, task_name: str, coro):
        '''
        Adds a coroutine whose execution should be managed by EidyiaCore.

        This is really generic enough that it can be any task, not just an
        actual Eidyia client.
        '''
        log.debug(f'Registered child task {task_name} as {coro.__qualname__}()')
        self._async_items[coro] = task_name

    def _notify_from_external_thread(self):
        '''
        Used from the EidyiaEventHandler thread to notify the async loop of
        a file update.
        '''
        self._valen_refreshed.set()

    @eidyia_critical_section
    async def _refresh_report(self):
        '''
        Refreshes the Valen report prior to submission to subscribers.
        '''
        log.debug('Reloading report file from monitor trigger')
        try:
            self._report_error = None
            self._old_report = self.report.clone()
            self.report.reload()
        except ValenReport.FileError as report_err:
            log.error(f'Could not reload report file. {report_err}')
            # While Report.reload() does attempt to ensure consistency in
            # this situation, it probably makes more sense to start fresh on
            # the next update without a diff.
            self._old_report = None
            # TODO: Maybe transmit more detailed information for clients to
            # transmit to privileged users?
            self._report_error = 'An error occurred while reading the status ' \
                                 'report. Check the console logs for details.'

    async def _async_monitor_loop(self):
        '''
        Main asynchronous monitoring task.

        In reality, monitoring is done on a separate thread by a watchdog
        observer, and the event handler notifies the main thread back if there
        is anything we need to do, by using a ConcurrentFlag object.
        '''
        if self.beholder is None:
            # We ran before monitoring was set up. This should never happen.
            log.critical('Invalid async task configuration')
            raise EidyiaResourceSharingViolation
        # TODO need to make sure ALL subscribers are ready first
        # await self.wait_until_ready()

        # Ready to go!
        self.beholder.start()
        log.debug('Report file monitoring started')

        unhandled_exc = 0
        while self.beholder.active():
            # TODO wait for subscribers if they become unready
            if self._valen_refreshed.get():
                try:
                    await self._refresh_report()
                    # Notify subscribers for whenever they next have the chance
                    # to look at the new report.
                    EidyiaSubscriber.eidyia_notify_all()
                except Exception:
                    unhandled_exc += 1
                    if unhandled_exc == 1:
                        info = 'first chance'
                    elif unhandled_exc == 2:
                        info = 'second chance'
                    else:
                        info = 'unbound'
                    log.critical(f'Unhandled exception in Eidyia monitoring task ({info})')
                    log.exception('\n***\n*** Unhandled exception\n***\n\n')
                    if unhandled_exc > 2:
                        raise
                finally:
                    # Ensure we don't try to reload again until the next
                    # event-mandated refresh even if we got here following an
                    # exception being raised by the reload or client chat
                    # submission process.
                    self._valen_refreshed.clear()
            await asyncio.sleep(_MONITOR_LOOP_INTERVAL_SECS)

    def run(self):
        '''
        Performs asynchronous execution of all assigned tasks, including the
        internal report monitor task.
        '''
        if not self._async_items:
            raise EidyiaNoTasksError
        try:
            asyncio.run(self._async_run(), debug=self._debug)
            return False
        except KeyboardInterrupt:
            log.info('Quitting after receiving signal (keyboard interrupt)')
            return True

    async def _async_run(self):
        '''
        Central coroutine.
        '''
        def taskid(task: asyncio.Task) -> str:
            return self._async_items.get(task.get_coro(), 'MonitorLoop')

        am_ok = True
        taskset = set([asyncio.Task(self._async_monitor_loop())] +
                      [asyncio.Task(coro) for coro in self._async_items.keys()])
        while taskset:
            await asyncio.sleep(0)
            # First to end means stops everything
            completed, pending = await asyncio.wait(
                taskset, return_when=asyncio.FIRST_COMPLETED)
            for task in completed:
                exc = task.exception()
                if exc is not None:
                    log.critical(f'*** Eidyia child fatal exception: {type(exc).__name__} (in {taskid(task)})')
                    log.critical(f'*** > {exc}')
                    am_ok = False
                taskset.remove(task)
            if completed or not am_ok:
                for task in pending:
                    log.debug(f'Cancelling pending task {taskid(task)}')
                    task.cancel()
                    taskset.remove(task)
        return am_ok


def eidyia_core() -> EidyiaCore:
    '''
    Returns the current Eidyia core instance.

    If a coroutine needs access to the core instance, it should use the
    eidyia_critical_section() decorator to guarantee that no other coroutines
    can access the core instance at the same time.
    '''
    global _instance
    if _instance is None:
        log.critical('eidyia_core() call before core initialisation ')
        raise EidyiaResourceSharingViolation
    return _instance
