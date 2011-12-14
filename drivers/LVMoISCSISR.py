#!/usr/bin/env python
# Copyright (C) 2010-2011 Citrix Ltd.
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
#
# LVMoISCSISR: LVM over ISCSI software initiator SR driver
#

import SR, VDI, LVMSR, ISCSISR, SRCommand, util, scsiutil, lvutil
import statvfs, time
import os, socket, sys, re
import xs_errors
import xmlrpclib

CAPABILITIES = ["SR_PROBE","VDI_CREATE","VDI_DELETE","VDI_ATTACH",
                "VDI_DETACH","VDI_RESIZE","VDI_RESIZE_ONLINE","VDI_GENERATE_CONFIG"]

CONFIGURATION = [ [ 'SCSIid', 'The scsi_id of the destination LUN' ], \
                  [ 'target', 'IP address or hostname of the iSCSI target' ], \
                  [ 'targetIQN', 'The IQN of the target LUN group to be attached' ], \
                  [ 'chapuser', 'The username to be used during CHAP authentication' ], \
                  [ 'chappassword', 'The password to be used during CHAP authentication' ], \
                  [ 'port', 'The network port number on which to query the target' ], \
                  [ 'multihomed', 'Enable multi-homing to this target, true or false (optional, defaults to same value as host.other_config:multipathing)' ], \
                  [ 'usediscoverynumber', 'The specific iscsi record index to use. (optional)' ] ]

DRIVER_INFO = {
    'name': 'LVM over iSCSI',
    'description': 'SR plugin which represents disks as Logical Volumes within a Volume Group created on an iSCSI LUN',
    'vendor': 'Citrix Systems Inc',
    'copyright': '(C) 2008 Citrix Systems Inc',
    'driver_version': '1.0',
    'required_api_version': '1.0',
    'capabilities': CAPABILITIES,
    'configuration': CONFIGURATION
    }

class LVMoISCSISR(LVMSR.LVMSR):
    """LVM over ISCSI storage repository"""
    def handles(type):
        if type == "lvmoiscsi":
            return True
        return False
    handles = staticmethod(handles)

    def load(self, sr_uuid):
        if not sr_uuid:
            # This is a probe call, generate a temp sr_uuid
            sr_uuid = util.gen_uuid()

        driver = SR.driver('iscsi')
        self.iscsi = driver(self.original_srcmd, sr_uuid)

        # Be extremely careful not to throw exceptions here since this function
        # is the main one used by all operations including probing and creating
        pbd = None
        try:
            pbd = util.find_my_pbd(self.session, self.host_ref, self.sr_ref)
        except:
            pass
        
        if not self.dconf.has_key('SCSIid') and self.dconf.has_key('LUNid') and pbd <> None:
            # UPGRADE FROM RIO: add SCSIid key to device_config
            util.SMlog("Performing upgrade from Rio")
            scsiid = self._getSCSIid_from_LUN(sr_uuid)
            
            device_config = self.session.xenapi.PBD.get_device_config(pbd)
            device_config['SCSIid'] = scsiid
            device_config['upgraded_from_rio'] = 'true'
            self.session.xenapi.PBD.set_device_config(pbd, device_config)

            self.dconf['SCSIid'] = scsiid            

        # Apart from the upgrade case, user must specify a SCSIid
        if not self.dconf.has_key('SCSIid'):
            self._LUNprint(sr_uuid)
            raise xs_errors.XenError('ConfigSCSIid')

        self.SCSIid = self.dconf['SCSIid']
        self._pathrefresh(LVMoISCSISR)

        super(LVMoISCSISR, self).load(sr_uuid)

    def _getSCSIid_from_LUN(self, sr_uuid):
        was_attached = True
        self.iscsi.attach(sr_uuid)        
        dev = self.dconf['LUNid'].split(',')
        if len(dev) > 1:
            raise xs_errors.XenError('LVMOneLUN')
        path = os.path.join(self.iscsi.path,"LUN%s" % dev[0])
        if not util.wait_for_path(path, ISCSISR.MAX_TIMEOUT):
            util.SMlog("Unable to detect LUN attached to host [%s]" % path)
        try:
            SCSIid = scsiutil.getSCSIid(path)
        except:
            raise xs_errors.XenError('InvalidDev')
        self.iscsi.detach(sr_uuid)
        return SCSIid

    def _LUNprint(self, sr_uuid):
        if self.iscsi.attached:
            # Force a rescan on the bus, pause for 5 seconds
            # N.B. Probing for LUNs can always be repeated, so don't wait a long time
            self.iscsi.refresh()
            time.sleep(5)
        # Now call attach (handles the refcounting + session activa)
        self.iscsi.attach(sr_uuid)
        # Wait up to 15 seconds for the base iscsi udev path
        # to show up. This may fail under extreme load or if
        # LUNs are not mapped to the host
        util.wait_for_path(self.iscsi.path, ISCSISR.MAX_TIMEOUT)
        self.iscsi.print_LUNs()
        self.iscsi.detach(sr_uuid)        
        
    def create(self, sr_uuid, size):
        # Check SCSIid not already in use by other PBDs
        if util.test_SCSIid(self.session, self.SCSIid):
            raise xs_errors.XenError('SRInUse')

        self.iscsi.attach(sr_uuid)
        try:
            if not self.iscsi._attach_LUN_bySCSIid(self.SCSIid):
                raise xs_errors.XenError('InvalidDev')
            self._pathrefresh(LVMoISCSISR)
            super(LVMoISCSISR, self).create(sr_uuid, size)
        except Exception, inst:
            self.iscsi.detach(sr_uuid)
            raise xs_errors.XenError("SRUnavailable", opterr=inst)
        self.iscsi.detach(sr_uuid)

    def delete(self, sr_uuid):
        super(LVMoISCSISR, self).delete(sr_uuid)
        self.iscsi.detach(sr_uuid)

    def attach(self, sr_uuid):
        self.iscsi.attach(sr_uuid)
        try:
            if not self.iscsi._attach_LUN_bySCSIid(self.SCSIid):
                raise xs_errors.XenError('InvalidDev')
            self._pathrefresh(LVMoISCSISR)
            super(LVMoISCSISR, self).attach(sr_uuid)
        except Exception, inst:
            self.iscsi.detach(sr_uuid)
            raise xs_errors.XenError("SRUnavailable", opterr=inst)
        self._setMultipathableFlag(SCSIid=self.SCSIid)
        
    def detach(self, sr_uuid):
        super(LVMoISCSISR, self).detach(sr_uuid)
        self.iscsi.detach(sr_uuid)

    def probe(self):
        self.uuid = util.gen_uuid()
        self.iscsi.attach(self.uuid)
	if not self.iscsi._attach_LUN_bySCSIid(self.SCSIid):
            util.SMlog("Unable to detect LUN")
            raise xs_errors.XenError('InvalidDev')
        out = super(LVMoISCSISR, self).probe()
        self.iscsi.detach(self.uuid)
        return out

    def vdi(self, uuid):
        return LVMoISCSIVDI(self, uuid)
    
class LVMoISCSIVDI(LVMSR.LVMVDI):
    def generate_config(self, sr_uuid, vdi_uuid):
        if not lvutil._checkLV(self.path):
                raise xs_errors.XenError('VDIUnavailable')
        dict = {}
        self.sr.dconf['localIQN'] = self.sr.iscsi.localIQN
        self.sr.dconf['multipathing'] = self.sr.mpath
        self.sr.dconf['multipathhandle'] = self.sr.mpathhandle
        dict['device_config'] = self.sr.dconf
        if dict['device_config'].has_key('chappassword_secret'):
            s = util.get_secret(self.session, dict['device_config']['chappassword_secret'])
            del dict['device_config']['chappassword_secret']
            dict['device_config']['chappassword'] = s
        dict['sr_uuid'] = sr_uuid
        dict['vdi_uuid'] = vdi_uuid
        dict['command'] = 'vdi_attach_from_config'
	# Return the 'config' encoded within a normal XMLRPC response so that
	# we can use the regular response/error parsing code.
	config = xmlrpclib.dumps(tuple([dict]), "vdi_attach_from_config")
        return xmlrpclib.dumps((config,), "", True)

    def attach_from_config(self, sr_uuid, vdi_uuid):
        self.sr.iscsi.attach(sr_uuid)
        if not self.sr.iscsi._attach_LUN_bySCSIid(self.sr.SCSIid):
            raise xs_errors.XenError('InvalidDev')
        return super(LVMoISCSIVDI, self).attach(sr_uuid, vdi_uuid)
        

if __name__ == '__main__':
    SRCommand.run(LVMoISCSISR, DRIVER_INFO)
else:
    SR.registerSR(LVMoISCSISR)
