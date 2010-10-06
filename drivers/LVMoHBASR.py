#!/usr/bin/env python
# Copyright (c) 2005-2007 XenSource, Inc. All use and distribution of this
# copyrighted material is governed by and subject to terms and conditions
# as licensed by XenSource, Inc. All other rights reserved.
# Xen, XenSource and XenEnterprise are either registered trademarks or
# trademarks of XenSource Inc. in the United States and/or other countries.
#
#
# LVMoISCSISR: LVM over Hardware HBA LUN driver, e.g. Fibre Channel or hardware
# based iSCSI
#

import SR, VDI, LVMSR, SRCommand, devscan, lvutil, HBASR
import util, scsiutil
import os, sys, re
import xs_errors
import xmlrpclib

CAPABILITIES = ["SR_PROBE","VDI_CREATE","VDI_DELETE","VDI_ATTACH",
                "VDI_DETACH","VDI_RESIZE","VDI_GENERATE_CONFIG"]

CONFIGURATION = [ [ 'SCSIid', 'The scsi_id of the destination LUN' ] ]

DRIVER_INFO = {
    'name': 'LVM over FC',
    'description': 'SR plugin which represents disks as Logical Volumes within a Volume Group created on an HBA LUN, e.g. hardware-based iSCSI or FC support',
    'vendor': 'Citrix Systems Inc',
    'copyright': '(C) 2008 Citrix Systems Inc',
    'driver_version': '1.0',
    'required_api_version': '1.0',
    'capabilities': CAPABILITIES,
    'configuration': CONFIGURATION
    }

class LVMoHBASR(LVMSR.LVMSR):
    """LVM over HBA storage repository"""
    def handles(type):
        if type == "lvmohba":
            return True
        return False
    handles = staticmethod(handles)

    def load(self, sr_uuid):
        driver = SR.driver('hba')
        self.hbasr = driver(self.original_srcmd, sr_uuid)

        pbd = None
        try:
            pbd = util.find_my_pbd(self.session, self.host_ref, self.sr_ref)
        except:
            pass

        try:
            if not self.dconf.has_key('SCSIid') and self.dconf.has_key('device'):
                # UPGRADE FROM MIAMI: add SCSIid key to device_config
                util.SMlog("Performing upgrade from Miami")
                if not os.path.exists(self.dconf['device']):
                    raise
                SCSIid = scsiutil.getSCSIid(self.dconf['device'])
                self.dconf['SCSIid'] = SCSIid
                del self.dconf['device']

                if pbd <> None:
                    device_config = self.session.xenapi.PBD.get_device_config(pbd)
                    device_config['SCSIid'] = SCSIid
                    device_config['upgraded_from_miami'] = 'true'
                    del device_config['device']
                    self.session.xenapi.PBD.set_device_config(pbd, device_config)
        except:
            pass

        if not self.dconf.has_key('SCSIid') or not self.dconf['SCSIid']:
            print >>sys.stderr,self.hbasr.print_devs()
            raise xs_errors.XenError('ConfigSCSIid')

        self.SCSIid = self.dconf['SCSIid']
        self._pathrefresh(LVMoHBASR)
        super(LVMoHBASR, self).load(sr_uuid)

    def create(self, sr_uuid, size):
        self.hbasr.attach(sr_uuid)
        self._pathrefresh(LVMoHBASR)
        super(LVMoHBASR, self).create(sr_uuid, size)

    def attach(self, sr_uuid):
        self.hbasr.attach(sr_uuid)
        self._pathrefresh(LVMoHBASR)
        super(LVMoHBASR, self).attach(sr_uuid)
        self._setMultipathableFlag(SCSIid=self.SCSIid)

    def vdi(self, uuid):
        return LVMoHBAVDI(self, uuid)
    
class LVMoHBAVDI(LVMSR.LVMVDI):
    def generate_config(self, sr_uuid, vdi_uuid):
        if not lvutil._checkLV(self.path):
                raise xs_errors.XenError('VDIUnavailable')
        dict = {}
        self.sr.dconf['multipathing'] = self.sr.mpath
        self.sr.dconf['multipathhandle'] = self.sr.mpathhandle
        dict['device_config'] = self.sr.dconf
        dict['sr_uuid'] = sr_uuid
        dict['vdi_uuid'] = vdi_uuid
        dict['command'] = 'vdi_attach_from_config'
	# Return the 'config' encoded within a normal XMLRPC response so that
	# we can use the regular response/error parsing code.
	config = xmlrpclib.dumps(tuple([dict]), "vdi_attach_from_config")
        return xmlrpclib.dumps((config,), "", True)

    def attach_from_config(self, sr_uuid, vdi_uuid):
        return super(LVMoHBAVDI, self).attach(sr_uuid, vdi_uuid)

def match_scsidev(s):
    regex = re.compile("^/dev/disk/by-id|^/dev/mapper")
    return regex.search(s, 0)
    
if __name__ == '__main__':
    SRCommand.run(LVMoHBASR, DRIVER_INFO)
else:
    SR.registerSR(LVMoHBASR)
