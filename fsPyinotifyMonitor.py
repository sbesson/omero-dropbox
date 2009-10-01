"""
    OMERO.fs Monitor module for Linux.


"""
import logging
log = logging.getLogger("fsserver."+__name__)
        
import copy
import threading
import sys, traceback
import uuid
import socket

# Third party path package. It provides much of the 
# functionality of os.path but without the complexity.
# Imported as pathModule to avoid potential clashes.
import path as pathModule

from omero.grid import monitors
from fsAbstractPlatformMonitor import AbstractPlatformMonitor

import pyinotify
try:
    # pyinotify 0.8
    log.info("Imported pyinotify version %s", pyinotify.__version__)
    EventsCodes = pyinotify
except:
    # pyinotify 0.7 or below
    log.info("Imported pyinotify version <= 0.7.x")
    try:
        from pyinotify import EventsCodes
    except:
        from pyinotify import pyinotify
        from pyinotify.pyinotify import EventsCodes

class PlatformMonitor(AbstractPlatformMonitor):
    """
        A Thread to monitor a path.
        
        :group Constructor: __init__
        :group Other methods: run, stop

    """

    def __init__(self, eventTypes, pathMode, pathString, whitelist, blacklist, ignoreSysFiles, ignoreDirEvents, proxy):
        """
            Set-up Monitor thread.
            
            After initialising the superclass and some instance variables
            try to create an FSEventStream. Throw an exeption if this fails.
            
            :Parameters:
                eventTypes : 
                    A list of the event types to be monitored.          
                    
                pathMode : 
                    The mode of directory monitoring: flat, recursive or following.

                pathString : string
                    A string representing a path to be monitored.
                  
                whitelist : list<string>
                    A list of files and extensions of interest.
                    
                blacklist : list<string>
                    A list of subdirectories to be excluded.

                ignoreSysFiles :
                    If true platform dependent sys files should be ignored.
                    
                monitorId :
                    Unique id for the monitor included in callbacks.
                    
                proxy :
                    A proxy to be informed of events
                    
        """
        AbstractPlatformMonitor.__init__(self, eventTypes, pathMode, pathString, whitelist, blacklist, ignoreSysFiles, ignoreDirEvents, proxy)

        recurse = False
        follow = False
        if self.pathMode == 'Follow':
            recurse = True
            follow = True
        elif self.pathMode == 'Recurse':
            recurse = True
        
        self.wm = MyWatchManager()
        self.notifier = pyinotify.ThreadedNotifier(self.wm, ProcessEvent(wm=self.wm, cb=self.callback, et=self.eTypes,  ignoreDirEvents=self.ignoreDirEvents))      
        self.wm.addBaseWatch(self.pathsToMonitor, (EventsCodes.ALL_EVENTS), rec=recurse, auto_add=follow)
        log.info('Monitor set-up on =' + str(self.pathsToMonitor))

    def callback(self, eventList):
        """
            Callback.
        
            :Parameters:
                    
                id : string
                    watch id.
                    
                eventPath : string
                    File paths of the event.
                    
            :return: No explicit return value.
            
        """
        try:          
            log.info('Event notification => ' + str(eventList))
            self.proxy.callback(eventList)
        except:
            self.exception("Notification failed : ")

                
    def start(self):
        """
            Start monitoring an FSEventStream.
   
            This method, overridden from Thread, is run by 
            calling the inherited method start(). The method attempts
            to schedule an FSEventStream and then run its CFRunLoop.
            The method then blocks until stop() is called.
            
            :return: No explicit return value.
            
        """

        # Blocks
        self.notifier.start()
        
    def stop(self):        
        """
            Stop monitoring an FSEventStream.
   
            This method attempts to stop the CFRunLoop. It then
            stops, invalidates and releases the FSEventStream.
            
            There should be a more robust approach in here that 
            still kils the thread even if the first call fails.
            
            :return: No explicit return value.
            
        """
        self.notifier.stop()
                
class MyWatchManager(pyinotify.WatchManager):
    """
       Essentially a limited-function wrapper to WatchManager
       
       At present only one watch is allowed on each path. This 
       may need to be fixed for applications outside of DropBox.
       
    """
    watchPaths = {}
    watchParams = {}
    
    def isPathWatched(self, pathString):
        return pathString in self.watchPaths.keys()

    def addBaseWatch(self, path, mask, rec=False, auto_add=False):
        res = pyinotify.WatchManager.add_watch(self, path, mask, rec=False, auto_add=False)
        self.watchPaths.update(res)
        self.watchParams[path] = WatchParameters(mask, rec=rec, auto_add=auto_add)
        if rec:
            for d in pathModule.path(path).dirs():
                self.addWatch(str(d), mask)
        log.info('Base watch created on: %s', path)

    def addWatch(self, path, mask):
        if not self.isPathWatched(path):
            res = pyinotify.WatchManager.add_watch(self, path, mask, rec=False, auto_add=False)
            self.watchPaths.update(res)
            self.watchParams[path] = copy.copy(self.watchParams[pathModule.path(path).parent])
            if self.watchParams[path].getRec():
                for d in pathModule.path(path).dirs():
                    self.addWatch(str(d), mask)
            log.info('Watch added on: %s', path)

    def removeWatch(self, path):
        if self.isPathWatched(path):
            removeDict = {}
            log.info('Trying to remove : %s', path)
            removeDict[self.watchPaths[path]] = path
            for d in self.watchPaths.keys():
                if d.find(path+'/') == 0:
                    log.info('    ... and : %s', d)
                    removeDict[self.watchPaths[d]] = d
            res = pyinotify.WatchManager.rm_watch(self, removeDict.keys())
            for wd in res.keys():
                if res[wd]: 
                    self.watchPaths.pop(removeDict[wd], True)
                    self.watchParams.pop(removeDict[wd], True)
                    log.info('Watch removed on: %s', removeDict[wd])
                else:
                    log.info('Watch remove failed, wd=%s, on: %s', wd, removeDict[wd]) 

    def getWatchPaths(self):
        for (path,wd) in self.watchPaths.items():
            yield (path, wd)
  
class WatchParameters(object):
    def __init__(self, mask, rec=False, auto_add=False):
        self.mask = mask
        self.rec = rec
        self.auto_add = auto_add

    def getMask(self):
        return self.mask

    def getRec(self):
        return self.rec

    def getAutoAdd(self):
        return self.auto_add

class ProcessEvent(pyinotify.ProcessEvent):
    def __init__(self, **kwargs):
        pyinotify.ProcessEvent.__init__(self)
        self.wm = kwargs['wm']
        self.cb = kwargs['cb']
        self.et = kwargs['et']
        self.ignoreDirEvents = kwargs['ignoreDirEvents'] 
        self.waitingCreates = set([])
        
    def process_default(self, event):
        
        try:
            # pyinotify 0.8
            name = event.pathname
            maskname = event.maskname
        except:
            # pyinotify 0.7 or below
            name = pathModule.path(event.path) / pathModule.path(event.name)
            maskname = event.event_name
            
        el = []
        
        # This is a tricky one. I'm not sure why these events arise exactly.
        # It seems to happen when inotify isn't sure of the path. Yet all the
        # events seem to have otherwise good paths. So, remove the suffix and 
        # allow the event to be dealt with as normal. 
        if name.find('-unknown-path') > 0:
            log.debug('Event with "-unknown-path" of type %s : %s', maskname, name)
            name = name.replace('-unknown-path','')
            
        # New directory within watch area, either created or moved into.
        if event.mask == (EventsCodes.IN_CREATE | EventsCodes.IN_ISDIR) \
                or event.mask ==  (EventsCodes.IN_MOVED_TO | EventsCodes.IN_ISDIR):
            log.info('New directory event of type %s at: %s', maskname, name)
            if "Creation" in self.et:
                if name.find('untitled folder') == -1:
                    if not self.ignoreDirEvents:
                        el.append((name, monitors.EventType.Create))
                    else:
                        log.info('Not propagated.')
                    # Handle the recursion plus create any potentially missed Create events
                    if self.wm.watchParams[pathModule.path(name).parent].getAutoAdd():
                        self.wm.addWatch(name, self.wm.watchParams[pathModule.path(name).parent].getMask())
                        if self.wm.watchParams[pathModule.path(name).parent].getRec():
                            for d in pathModule.path(name).walkdirs():
                                log.info('NON-INOTIFY event: New directory at: %s', str(d))
                                if not self.ignoreDirEvents:
                                    el.append((str(d), monitors.EventType.Create))
                                else:
                                    log.info('Not propagated.')
                                self.wm.addWatch(str(d), self.wm.watchParams[pathModule.path(name).parent].getMask())
                            for f in pathModule.path(name).walkfiles():
                                log.info('NON-INOTIFY event: New file at: %s', str(f))
                                el.append((str(f), monitors.EventType.Create))
                        else:
                            for d in pathModule.path(name).dirs():
                                log.info('NON-INOTIFY event: New directory at: %s', str(d))
                                if not self.ignoreDirEvents:
                                    el.append((str(d), monitors.EventType.Create))
                                else:
                                    log.info('Not propagated.')
                            for f in pathModule.path(name).files():
                                log.info('NON-INOTIFY event: New file at: %s', str(f))
                                el.append((str(f), monitors.EventType.Create))
                else:
                    log.info('Created "untitled folder" ignored.')
            else:
                log.info('Not propagated.')
                
        # Deleted directory or one moved out of the watch area.
        elif event.mask ==  (EventsCodes.IN_MOVED_FROM | EventsCodes.IN_ISDIR) \
                or event.mask ==  (EventsCodes.IN_DELETE | EventsCodes.IN_ISDIR):
            log.info('Deleted directory event of type %s at: %s', maskname, name)
            if "Deletion" in self.et:
                if name.find('untitled folder') == -1:
                    if not self.ignoreDirEvents:
                        el.append((name, monitors.EventType.Delete))
                    else:
                        log.info('Not propagated.')
                    log.info('Files and subfolders within %s may have been deleted without notice', name)
                    self.wm.removeWatch(name)
                else:
                    log.info('Deleted "untitled folder" ignored.')
            else:
                log.info('Not propagated.')
        
        # New file within watch area, either created or moved into.
        # The file may have been created but it may not be complete and closed.
        # Modifications should be watched
        elif event.mask == EventsCodes.IN_CREATE:
            log.info('New file event of type %s at: %s', maskname, name)
            if "Creation" in self.et:
                self.waitingCreates.add(name)
            else:
                log.info('Not propagated.')

        # New file within watch area.
        elif event.mask == EventsCodes.IN_MOVED_TO:
            log.info('New file event of type %s at: %s', maskname, name)
            if "Creation" in self.et:
                el.append((name, monitors.EventType.Create))
            else:
                log.info('Not propagated.')

        # Modified file within watch area.
        elif event.mask == EventsCodes.IN_CLOSE_WRITE:
            log.info('Modified file event of type %s at: %s', maskname, name)
            if name in self.waitingCreates:
                if "Creation" in self.et:
                    el.append((name, monitors.EventType.Create))
                    self.waitingCreates.remove(name)
                else:
                    log.info('Not propagated.')
            else:
                if "Modification" in self.et:
                    el.append((name, monitors.EventType.Modify))
                else:
                    log.info('Not propagated.')

        # Modified file within watch area, only notify if file is not waitingCreate.
        elif event.mask == EventsCodes.IN_MODIFY:
            log.info('Modified file event of type %s at: %s', maskname, name)
            if name not in self.waitingCreates:
                if "Modification" in self.et:
                    el.append((name, monitors.EventType.Modify))
                else:
                    log.info('Not propagated.')
                    

        # Deleted file  or one moved out of the watch area.
        elif event.mask == EventsCodes.IN_MOVED_FROM or event.mask == EventsCodes.IN_DELETE:
            log.info('Deleted file event of type %s at: %s', maskname, name)
            if "Deletion" in self.et:
                el.append((name, monitors.EventType.Delete))
            else:
                log.info('Not propagated.')

        # These are all the currently ignored events.
        elif event.mask == EventsCodes.IN_ATTRIB:
            # Attributes have changed? Useful?
            log.debug('Ignored event of type %s at: %s', maskname, name)
        elif event.mask == EventsCodes.IN_DELETE_SELF or event.mask == EventsCodes.IN_IGNORED:
            # This is when a directory being watched is removed, handled above.
            log.debug('Ignored event of type %s at: %s', maskname, name)
        elif event.mask == EventsCodes.IN_MOVE_SELF:
            # This is when a directory being watched is moved out of the watch area (itself!), handled above.
            log.debug('Ignored event of type %s at: %s', maskname, name)
        elif event.mask == EventsCodes.IN_OPEN | EventsCodes.IN_ISDIR :
            # Event, dir open, we can ignore for now to reduce the log volume at any rate.
            log.debug('Ignored event of type %s at: %s', maskname, name)
        elif event.mask == EventsCodes.IN_CLOSE_NOWRITE | EventsCodes.IN_ISDIR :
            # Event, dir close, we can ignore for now to reduce the log volume at any rate.
            log.debug('Ignored event of type %s at: %s', maskname, name)
        elif event.mask == EventsCodes.IN_ACCESS | EventsCodes.IN_ISDIR :
            # Event, dir access, we can ignore for now to reduce the log volume at any rate.
            log.debug('Ignored event of type %s at: %s', maskname, name)
        elif event.mask == EventsCodes.IN_OPEN :
            # Event, file open, we can ignore for now to reduce the log volume at any rate.
            log.debug('Ignored event of type %s at: %s', maskname, name)
        elif event.mask == EventsCodes.IN_CLOSE_NOWRITE :
            # Event, file close, we can ignore for now to reduce the log volume at any rate.
            log.debug('Ignored event of type %s at: %s', maskname, name)
        elif event.mask == EventsCodes.IN_ACCESS :
            # Event, file access, we can ignore for now to reduce the log volume at any rate.
            log.debug('Ignored event of type %s at: %s', maskname, name)
        
        # Other events, log them since they really should be caught above    
        else:
            log.info('Uncaught event of type %s at: %s', maskname, name)
            log.info('Not propagated.')
        
        if len(el) > 0:  
            self.cb(el)

