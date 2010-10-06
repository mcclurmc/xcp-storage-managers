#!/usr/bin/python
# Copyright (C) 2006-2007 XenSource Ltd.
# Copyright (C) 2008-2009 Citrix Ltd.
#
# This program is free software; you can redistribute it and/or modify 
# it under the terms of the GNU Lesser General Public License as published 
# by the Free Software Foundation; version 2.1 only.
#
# This program is distributed in the hope that it will be useful, 
# but WITHOUT ANY WARRANTY; without even the implied warranty of 
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the 
# GNU Lesser General Public License for more details.
#


"""IO-FI: I/O Fault injection for networked storage.

NFS volumes are softmounted, therefore sensitive to network
congestion. This module tests util.ioretry() usage in networked SRs by
blocking traffic via iptables. It overrides util.ioretry_stat() as
well. It may override various other functions in order to work around
error conditions induced not by an SR (typially NFSSR) implementation
but the fault injector code itself.

Actual fault injection is done by programming a custom packet filter
(see ../tests/faultinjection/).

Usage:

 This module typically gets hooked into the import chain of other
 modules. See the import conditions in util.py for details.

There are presently two general modes of operation:

'advisory': Traffic is only blocked upon entry to ioretry().  Then
   start from a blocked setting and incrementally let more and more
   packets through.

'mandatory': Blocking all traffic by default. Only open the filter in
   ioretry(). This has the advantage of tracking down I/O operations
   which lack a proper ioretry() call.

In both modes, 'incrementally' means we iterate through the original
 ioretry(). Each iteration i lets i packets through before dropping
 all traffic again. This is repeated until the operations finally
 succeeds.
"""

import util
import xs_errors
import sys, os, errno, time
import signal
import inspect

class PFilter:
    """Control the programmable packet fault injector."""

    PIDFILE="/var/run/pfilter/pid"
    pid = None

    def connect():
        try:
            pidfile = open(PFilter.PIDFILE)
        except IOError, e:
            util.SMlog("Failure connecting to PFilter: " + str(e))
            sys.exit(1)
            
        PFilter.pid = int(pidfile.readline())
        try:
            os.kill(PFilter.pid, 0)
        except OSError, e:
            util.SMlog("Failure connecting to PFilter (PID %d): " % PFilter.pid +
                str(e))
            sys.exit(1)
        pidfile.close()
    connect = staticmethod(connect)

    def trace(str):
        util.SMlog("FITrace: %s" % str)
    trace = staticmethod(trace)

    def kill(sig):
        os.kill(PFilter.pid, sig)
        time.sleep(.1)
    kill = staticmethod(kill)
    
    def increment_droppage():
        PFilter.kill(signal.SIGUSR1)
    increment_droppage = staticmethod(increment_droppage)

    def decrement_droppage():
        PFilter.kill(signal.SIGUSR2)
    decrement_droppage = staticmethod(decrement_droppage)

    def allow_all():
        PFilter.kill(signal.SIGCHLD)
    allow_all = staticmethod(allow_all)

    def disallow_all():
        PFilter.kill(signal.SIGALRM)
    disallow_all = staticmethod(disallow_all)

    def reset_packet_count():
        PFilter.kill(signal.SIGILL)
    reset_packet_count = staticmethod(reset_packet_count)

    def percentage_drop_mode():
        PFilter.kill(signal.SIGFPE)
    percentage_drop_mode = staticmethod(percentage_drop_mode)

    def packet_drop_mode():
        PFilter.kill(signal.SIGURG)
    packet_drop_mode = staticmethod(packet_drop_mode)

    def dump_settings():
        PFilter.kill(signal.SIGPROF)
    dump_settings = staticmethod(dump_settings)

class IORetryFilter:
    """
    PFilter quick reference: For this part, we always run
    DROP_PACKET mode (as opposed to DROP_PERCENT).  
    
    The NF_ACCEPT verdict is (curr_packet_count < drop_packet)

    drop_packet:
       the number of packets counted until we drop all which follow.
    
    last_action:
       always supersedes the packet count
    
     Here, blocked only means fully blocked. passing only a couple
     packets goes as non-blocked.
     """

    blocked = False

    def _drop_buffers():
        # man proc(5)
        file("/proc/sys/vm/drop_caches", "w").write("3")
    _drop_buffers = staticmethod(_drop_buffers)

    def block():
        util.SMlog("IORetryFilter: Block all packets.")
        # mode := DROP_PACKET, drop_packet := 0
        PFilter.packet_drop_mode()
        PFilter.reset_packet_count()
        #IORetryFilter._drop_buffers()
        IORetryFilter.blocked = True
    block = staticmethod(block)

    def is_blocked():
        #util.SMlog("IORetryFilter: is_blocked=%s" % IORetryFilter.blocked)
        # FIXME: A coherent check against the running pfilter would be
        # nice. For now, we keep our own flag and optionally log both
        # for debugging:
        #PFilter.dump_settings()
        # Should always say 'Disallowing All packets'. Consult the
        # logfile. (grep LOG ../tests/faultinjection/pfilter.c)
        return IORetryFilter.blocked
    is_blocked = staticmethod(is_blocked)

    def clear():
        util.SMlog("IORetryFilter: Allow all packets.")
        # last_action := ACTION_ALLOW_ALL
        PFilter.allow_all()
        IORetryFilter.blocked = False
    clear = staticmethod(clear)

    def increment():
        util.SMlog("IORetryFilter: Increment passing packets.")
        # drop_packet += 1
        PFilter.increment_droppage()
        # curr_packet_count := 0
        PFilter.reset_packet_count()
        IORetryFilter.blocked = False
    increment = staticmethod(increment)

    def reset():
        pass

FI_IORETRY_PERIOD = 0.1 # seconds

def __ioretry_quick(f, errlist, maxretry):

    assert IORetryFilter.is_blocked() is True

    try:
        util.SMlog("Running blocked")
        rv = __util_ioretry(f, errlist, maxretry=1, period=FI_IORETRY_PERIOD) \

        util.SMlog("Hm, nothing caught on ioretry(): f=%s from %s" % \
                      (f, callerinfo(4)))

    except util.CommandException, e:
        
        util.SMlog("Ok, caught exception on ioretry(): exception=%s, f=%s from %s" % \
                       (e, f, callerinfo(4)))

        IORetryFilter.clear()

        util.SMlog("Running clear")
        rv = __util_ioretry(f, errlist, maxretry, period=FI_IORETRY_PERIOD) \

    except OSError, e:
        assert e.errno not in errlist
        raise

    return rv

def __ioretry_iterate(f, errlist, maxretry, period):

    assert IORetryFilter.is_blocked() is True

    retries = 0
    iteration = 0
    while True:
        try:
            rv = __util_ioretry(f, errlist, maxretry=1, period=FI_IORETRY_PERIOD)

            if IORetryFilter.is_blocked():
                util.SMlog("Hm, nothing caught on BLOCKED ioretry(): f=%s from %s" % \
                              (f, callerinfo(4)))

            util.SMlog("ioretry: SUCCESS, return.  iteration=%d retry=%d/%d" % (iteration, retries, maxretry))
            return rv

        except util.CommandException, inst:
            if not int(inst.code) in errlist:
                break

            util.SMlog("ioretry: CATCH, increment.  iteration=%d retry=%d/%d" % (iteration, retries, maxretry))
            IORetryFilter.increment()

            # Not implemented: Could ask pfilter for the drop
            # count. No point in iterating if nothing is dropped. For
            # now, let's bluntly assume its all due to filtering.
            drop_count = 1

        except OSError, e:
            # errlist is supposed to always be translated to a
            # CommandException
            assert e.errno not in errlist
            raise

        iteration += 1
        if drop_count > 0:
            continue

        # Wow, it actually DID fail.
        retries += 1
        if retries >= maxretry:
            break
        
        time.sleep(period)

    util.SMlog("ioretry: FAILURE, raise. retries=%d/%d, " % (retries+1, maxretry) +
              "errno=%d" % inst.code)
    raise

__in_ioretry = 0 # no threads, no drama

def __ioretry(f, errlist, maxretry, period, nofail):

    global __in_ioretry

    assert IORetryFilter.is_blocked() is True
    assert not __in_ioretry, "Recursive ioretry() entry."

    __in_ioretry += 1

    try:
        if nofail:
            IORetryFilter.clear()
            rv = __util_ioretry(f, errlist, maxretry, period)

        else:
            if 'quick' in XE_IOFI_IORETRY:
                # this should be only used for testing the FI stuff by
                # itself.
                rv = __ioretry_quick(f, errlist, maxretry)
            else:
                rv = __ioretry_iterate(f, errlist, maxretry, period)

    finally:
        __in_ioretry -= 1

    return rv

def ioretry_mandatory(f, errlist=[errno.EIO],
                      maxretry=util.IORETRY_MAX, period=util.IORETRY_PERIOD,
                      nofail=False):
    
    assert IORetryFilter.is_blocked() is True

    try:
        return __ioretry(f, errlist, maxretry, period, nofail)

    finally:
        IORetryFilter.block()

def ioretry_advisory(f, errlist=[errno.EIO],
                     maxretry=util.IORETRY_MAX, period=util.IORETRY_PERIOD,
                     nofail=False):

    util.SMlog("IORetry(advisory): f=%s  from %s, nofail=%s" % (f, callerinfo(2), nofail))

    assert IORetryFilter.is_blocked() is False

    IORetryFilter.block()

    try:
        rv = __ioretry(f, errlist, maxretry, period, nofail)

    finally:
        IORetryFilter.clear()
        util.SMlog("/IORetry(advisory)")

    return rv

def ioretry_stat(f, maxretry=util.IORETRY_MAX):
    return __util_ioretry_stat(f, maxretry)


def _testHost(hostname, port, errstring):
    util.SMlog("_testHost from %s - assuming SUCCESS." % callerinfo())

def callerinfo(depth = 2):
    try:
        frame = inspect.currentframe(depth)
    except ValueError:
        return "<this came from nowhere (at depth 2)>"
    (file, line, fn, ctx, idx) = inspect.getframeinfo(frame)
    return "%s(), %s line %d" % (fn, file, line)

#
# init
#

XE_IOFI_IORETRY = os.environ.get('XE_IOFI_IORETRY')
if XE_IOFI_IORETRY is not None:
    XE_IOFI_IORETRY = XE_IOFI_IORETRY.split(',')

    util.SMlog("IO-FI: I/O-Retry fault injection.")

    PFilter.connect()
    util.SMlog("IO-FI: PFilter PID=%d" % PFilter.pid)

    __util_ioretry = util.ioretry
    __util_ioretry_stat = util.ioretry_stat

    util.SMlog("IO-FI: Overriding util.ioretry_stat()")
    util.ioretry_stat = ioretry_stat

    util.SMlog("IO-FI: Overriding util._testHost()")
    util._testHost = _testHost

    if 'mandatory' in XE_IOFI_IORETRY:
        IORetryFilter.block()
    
        util.ioretry = ioretry_mandatory
        util.SMlog("IO-FI: Overriding util.ioretry() (mandatory)")

    else:
        IORetryFilter.clear()
        
        util.ioretry = ioretry_advisory
        util.SMlog("IO-FI: Overriding util.ioretry() (advisory)")

    import nfs
    util.SMlog("IO-FI: Overriding nfs.SOFTMOUNT_TIMEOUT")
    nfs.SOFTMOUNT_TIMEOUT = 1 # 1/10s
    nfs.SOFTMOUNT_RETRANS = 0
