#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

from abc import ABC, abstractmethod
import logging
from pathlib import Path
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


class EidyiaEventHandler(watchdog.events.FileSystemEventHandler):
    '''
    Eidyia file monitor class.

    It is a watchdog.events.FileSystemEventHandler that can signal its
    associated EidyiaReceiver to broadcast notifications when its monitored
    target is modified on disk.

    Because Valen writes report to a temporary .new file which then replaces
    the old file by taking its name, the ordinary FileSystemEventHandler loses
    track of the original file since its inode gets deleted. This handler
    circumvents this by receiving events for the directory that owns the file
    name and only dispatching to Eidyia subscribers if an object with the same
    filename passed in the constructor gets created or modified (deletions are
    ignored beyond warning about them).

    (Additionally, because of Valen's implementation we are guaranteed that
    whenever a creation event is dispatched, we are notifying subscribers of a
    completely coherent file and not one that is not fully written to disk.)
    '''
    def __init__(self, path: Path, subscribers: SubscriberList = None):
        super().__init__()
        self.path = path
        self.subscribers = []
        if subscribers is not None:
            self.subscribe(subscribers)

    def subscribe(self, subscribers: SubscriberList):
        if isinstance(subscribers, Subscriber):
            self.subscribers.append(subscribers)
        else:
            self.subscribers += subscribers.copy()
        log.debug('EidyiaFSEventHandler.subscribe(): subscribed')

    def on_any_event(self, event: watchdog.events.FileSystemEvent):
        '''
        Handle filesystem events and notify subscribers.
        '''
        event_path = None
        if isinstance(event, (watchdog.events.FileCreatedEvent,
                              watchdog.events.FileModifiedEvent)):
            event_path = Path(event.src_path).resolve()
        elif isinstance(event, watchdog.events.FileMovedEvent):
            event_path = Path(event.dest_path).resolve()

        if event_path is not None and event_path == self.path:
            if not self.subscribers:
                log.critical('EidyiaEventHandler.on_any_event(): no subscribers')
                return
            log.debug(f'EidyiaEventHandler.on_any_event(): ACK {event}')
            for subscriber in self.subscribers:
                log.debug('EidyiaEventHandler.on_any_event(): notifying subscriber')
                subscriber.on_eidyia_update()


class Beholder:
    '''
    Eidyia file monitor manager.

    Manages an FSEventHandler and watchdog Observer pair as well as abstracts
    the specifics of the file monitoring process; in particular, it hides the
    complexity behind watching a "file" that changes inode number because of
    Valen's coherency guarantee.
    '''
    def __init__(self,
                 filename: str,
                 controlling_sub: Subscriber):
        '''
        Constructor.

        Parameters:
            filename                Path to a file to monitor.
            controlling_sub         Controlling object (which acts as the
                                    single subscriber of the event handler).
        '''
        self._controlling_sub = controlling_sub
        self._event_handler = None
        self._observer = None
        self._path = Path(filename).resolve()

    def attach(self):
        '''
        Attaches (but does not start) a new observer and event handler pair.
        '''
        self._observer = watchdog.observers.Observer()
        self._event_handler = EidyiaEventHandler(self._path,
                                                 self._controlling_sub)
        # Observe the parent dir, not the singular file!
        self._observer.schedule(self._event_handler, self._path.parent, recursive=False)

    def start(self):
        '''
        Starts observing.
        '''
        if self._observer is None:
            log.critical('Attempted to start observing without an observer')
        else:
            self._observer.start()

    def active(self) -> bool:
        '''
        Returns True if an observer is running.
        '''
        return self._observer is not None and self._observer.is_alive()

    def stop(self):
        '''
        Stops observing.
        '''
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
