#!/usr/bin/python
# Copyright (C) 2010 Citrix Ltd.
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
# Clear the attach status for all VDIs in the given SR on this host.  
# Additionally, reset the paused state if this host is the master.

import util
import lock
from vhdutil import LOCK_TYPE_SR
from cleanup import LOCK_TYPE_RUNNING

def reset(session, host_uuid, sr_uuid, is_sr_master):
    gc_lock = lock.Lock(LOCK_TYPE_RUNNING, sr_uuid)
    sr_lock = lock.Lock(LOCK_TYPE_SR, sr_uuid)
    gc_lock.acquire()
    sr_lock.acquire()

    sr_ref = session.xenapi.SR.get_by_uuid(sr_uuid)

    host_ref = session.xenapi.host.get_by_uuid(host_uuid)
    host_key = "host_%s" % host_ref

    util.SMlog("RESET for SR %s (master: %s)" % (sr_uuid, is_sr_master))

    vdi_recs = session.xenapi.VDI.get_all_records_where( \
            "field \"SR\" = \"%s\"" % sr_ref)

    for vdi_ref, vdi_rec in vdi_recs.iteritems():
        vdi_uuid = vdi_rec["uuid"]
        sm_config = vdi_rec["sm_config"]
        if sm_config.get(host_key):
            util.SMlog("Clearing attached status for VDI %s" % vdi_uuid)
            session.xenapi.VDI.remove_from_sm_config(vdi_ref, host_key)
        if is_sr_master and sm_config.get("paused"):
            util.SMlog("Clearing paused status for VDI %s" % vdi_uuid)
            session.xenapi.VDI.remove_from_sm_config(vdi_ref, "paused")

    sr_lock.release()
    gc_lock.release()


if __name__ == '__main__':
    import sys
    import XenAPI
    
    if len(sys.argv) not in [3, 4]:
        print "Params: <HOST UUID> <SR UUID> [master]"
        print "*WARNING!* CALLING ON AN ATTACHED SR MAY CAUSE DATA CORRUPTION!"
        sys.exit(1)

    session = XenAPI.xapi_local()
    session.xenapi.login_with_password('root', '')
    host_uuid = sys.argv[1]
    sr_uuid = sys.argv[2]
    is_master = False
    if len(sys.argv) == 4 and sys.argv[3] == "master":
        is_master = True

    reset(session, host_uuid, sr_uuid, is_master)
