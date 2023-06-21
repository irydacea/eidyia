#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

from abc import ABC, abstractmethod
import logging
from typing import List, Union
import watchdog.events
import watchdog.observers

log = logging.getLogger('eidyia.subscriber_api')


class Subscriber(ABC):
    '''
    Eidyia update events subscriber abstract class.
    '''
    @abstractmethod
    def on_eidyia_update(self):
        '''
        Handles update event reception.
        '''
        pass


SubscriberList = Union[Subscriber, List[Subscriber]]


class FSEventHandler(watchdog.events.FileSystemEventHandler):
    '''
    Eidyia file monitor class.

    It is a watchdog.events.FileSystemEventHandler that can signal its
    associated EidyiaReceiver to broadcast notifications when its monitored
    target(s) are modified on disk.
    '''
    def __init__(self, subscribers: SubscriberList = None):
        super().__init__()
        self.subscribers = []
        if subscribers is not None:
            self.subscribe(subscribers)

    def subscribe(self, subscribers: SubscriberList):
        if isinstance(subscribers, Subscriber):
            self.subscribers.append(subscribers)
        else:
            self.subscribers += subscribers.copy()
        log.debug('EidyiaFSEventHandler.subscribe(): subscribed')

    def on_any_event(self, event):
        log.debug('oh')

    def on_created(self, event):
        '''
        Handle file creation.
        '''
        # TODO: ?
        log.debug('EidyiaFSEventHandler.on_created(): STUB')

    def on_deleted(self, event):
        '''
        Handle file deletion.
        '''
        # TODO: ?
        log.debug('EidyiaFSEventHandler.on_deleted(): STUB')

    def on_modified(self, event):
        '''
        Handle file modification.
        '''
        if isinstance(event, watchdog.events.DirModifiedEvent):
            log.critical(f'EidyiaFSEventHandler.on_modified(): {event.src_path} is or became a directory somehow')
            return
        if not self.subscribers:
            log.critical('EidyiaFSEventHandler.on_modified(): no subscribers')
            return
        for subscriber in self.subscribers:
            log.debug('EidyiaFSEventHandler.on_modified(): notifying subscriber')
            subscriber.on_eidyia_update()
