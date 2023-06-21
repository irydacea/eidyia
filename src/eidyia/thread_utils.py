#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

import threading


class ConcurrentFlag:
    '''
    Simple lockable flag variable wrapper.

    The flag is initially cleared after construction. set(), clear() and get()
    methods are provided to interact with the flag.
    '''
    def __init__(self):
        self._flag = False
        self._lock = threading.Lock()

    def set(self):
        '''
        Sets the flag.
        '''
        self._lock.acquire()
        self._flag = True
        self._lock.release()

    def clear(self):
        '''
        Clears the flag.
        '''
        self._lock.acquire()
        self._flag = False
        self._lock.release()

    def get(self):
        '''
        Retrieves the current flag value.
        '''
        self._lock.acquire()
        res = self._flag
        self._lock.release()
        return res
