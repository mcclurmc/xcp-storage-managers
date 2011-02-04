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
# LVHDoISCSISR: LVHD over ISCSI software initiator SR driver
#

import SR, VDI, LVHDSR, ISCSISR, SRCommand, util, scsiutil, lvutil
import statvfs, time
import os, socket, sys, re
import xs_errors
import xmlrpclib
import mpath_cli, iscsilib
import glob, copy
import mpp_luncheck
import scsiutil

CAPABILITIES = ["SR_PROBE", "SR_UPDATE", "VDI_CREATE", "VDI_DELETE",
                "VDI_ATTACH", "VDI_DETACH", "VDI_GENERATE_CONFIG",
                "VDI_CLONE", "VDI_SNAPSHOT", "VDI_RESIZE", "VDI_RESIZE_ONLINE",
                "ATOMIC_PAUSE"]

CONFIGURATION = [ [ 'SCSIid', 'The scsi_id of the destination LUN' ], \
                  [ 'target', 'IP address or hostname of the iSCSI target' ], \
                  [ 'targetIQN', 'The IQN of the target LUN group to be attached' ], \
                  [ 'chapuser', 'The username to be used during CHAP authentication' ], \
                  [ 'chappassword', 'The password to be used during CHAP authentication' ], \
                  [ 'incoming_chapuser', 'The incoming username to be used during bi-directional CHAP authentication (optional)' ], \
                  [ 'incoming_chappassword', 'The incoming password to be used during bi-directional CHAP authentication (optional)' ], \
                  [ 'port', 'The network port number on which to query the target' ], \
                  [ 'multihomed', 'Enable multi-homing to this target, true or false (optional, defaults to same value as host.other_config:multipathing)' ], \
                  [ 'usediscoverynumber', 'The specific iscsi record index to use. (optional)' ], \
                  [ 'allocation', 'Valid values are thick or thin (optional, defaults to thick)'] ]

DRIVER_INFO = {
    'name': 'LVHD over iSCSI',
    'description': 'SR plugin which represents disks as Logical Volumes within a Volume Group created on an iSCSI LUN',
    'vendor': 'Citrix Systems Inc',
    'copyright': '(C) 2008 Citrix Systems Inc',
    'driver_version': '1.0',
    'required_api_version': '1.0',
    'capabilities': CAPABILITIES,
    'configuration': CONFIGURATION
    }

class LVHDoISCSISR(LVHDSR.LVHDSR):
    """LVHD over ISCSI storage repository"""
    def handles(type):
        if __name__ == '__main__': 
            name = sys.argv[0]
        else:
            name = __name__
        if name.endswith("LVMoISCSISR"):
            return type == "lvmoiscsi"
        if type == "lvhdoiscsi":
            return True
        return False
    handles = staticmethod(handles)

    def load(self, sr_uuid):
        if not sr_uuid:
            # This is a probe call, generate a temp sr_uuid
            sr_uuid = util.gen_uuid()

        driver = SR.driver('iscsi')
        if self.original_srcmd.dconf.has_key('target'):
            self.original_srcmd.dconf['targetlist'] = self.original_srcmd.dconf['target']
        iscsi = driver(self.original_srcmd, sr_uuid)
        self.iscsiSRs = []
        self.iscsiSRs.append(iscsi)
        
        if self.dconf['target'].find(',') == 0 or self.dconf['targetIQN'] == "*":
            # Instantiate multiple sessions
            self.iscsiSRs = []
            if self.dconf['targetIQN'] == "*":
                IQN = "any"
            else:
                IQN = self.dconf['targetIQN']
            dict = {}
            try:
                if self.dconf.has_key('multiSession'):
                    IQNs = self.dconf['multiSession'].split("|")
                    for IQN in IQNs:
                        if IQN:
                            dict[IQN] = ""
                else:                    
                    for tgt in self.dconf['target'].split(','):
                        map = iscsilib.discovery(tgt,iscsi.port,iscsi.chapuser,iscsi.chappassword,targetIQN=IQN)
                        util.SMlog("Discovery for IP %s returned %s" % (tgt,map))
                        for i in range(0,len(map)):
                            (portal,tpgt,iqn) = map[i]
                            (ipaddr,port) = portal.split(',')[0].split(':')
                            key = "%s,%s,%s" % (ipaddr,port,iqn)
                            dict[key] = ""
                # Compose the IQNstring first
                IQNstring = ""
                for key in dict.iterkeys(): IQNstring += "%s|" % key

                # Now load the individual iSCSI base classes
                for key in dict.iterkeys():
                    (ipaddr,port,iqn) = key.split(',')
                    srcmd_copy = copy.deepcopy(self.original_srcmd)
                    srcmd_copy.dconf['target'] = ipaddr
                    srcmd_copy.dconf['targetIQN'] = iqn
                    srcmd_copy.dconf['multiSession'] = IQNstring
                    util.SMlog("Setting targetlist: %s" % srcmd_copy.dconf['targetlist'])
                    self.iscsiSRs.append(driver(srcmd_copy, sr_uuid))
                pbd = util.find_my_pbd(self.session, self.host_ref, self.sr_ref)
                if pbd <> None and not self.dconf.has_key('multiSession'):
                    dconf = self.session.xenapi.PBD.get_device_config(pbd)
                    dconf['multiSession'] = IQNstring
                    self.session.xenapi.PBD.set_device_config(pbd, dconf)
            except:
                pass
        self.iscsi = self.iscsiSRs[0]

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
        self._pathrefresh(LVHDoISCSISR)

        LVHDSR.LVHDSR.load(self, sr_uuid)

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
            # Force a rescan on the bus.
            self.iscsi.refresh()
#            time.sleep(5)
        # Now call attach (handles the refcounting + session activa)
        self.iscsi.attach(sr_uuid)

        util.SMlog("LUNprint: waiting for path: %s" % self.iscsi.path)
        if util.wait_for_path("%s/LUN*" % self.iscsi.path, ISCSISR.MAX_TIMEOUT):
            try:
                adapter=self.iscsi.adapter[self.iscsi.address]
                util.SMlog("adapter=%s" % adapter)

                # find a scsi device on which to issue a report luns command:
                devs=glob.glob("%s/LUN*" % self.iscsi.path)
                sgdevs = []
                for i in devs:
                    sgdevs.append(int(i.split("LUN")[1]))
                sgdevs.sort()
                sgdev = "%s/LUN%d" % (self.iscsi.path,sgdevs[0])                

                # issue a report luns:
                luns=util.pread2(["/usr/bin/sg_luns","-q",sgdev]).split('\n')
                nluns=len(luns)-1 # remove the line relating to the final \n
                # check if the LUNs are MPP-RDAC Luns
                scsi_id = scsiutil.getSCSIid(sgdev)
                mpp_lun = False
                if (mpp_luncheck.is_RdacLun(scsi_id)):
                    mpp_lun = True
                    link=glob.glob('/dev/disk/by-scsibus/%s-*' % scsi_id)
                    mpp_adapter = link[0].split('/')[-1].split('-')[-1].split(':')[0]

                # make sure we've got that many sg devices present
                for i in range(0,30): 
                    luns=scsiutil._dosgscan()
                    sgdevs=filter(lambda r: r[1]==adapter, luns)
                    if mpp_lun:
                        sgdevs.extend(filter(lambda r: r[1]==mpp_adapter, luns))
                    if len(sgdevs)>=nluns:
                        util.SMlog("Got all %d sg devices" % nluns)
                        break
                    else:
                        util.SMlog("Got %d sg devices - expecting %d" % (len(sgdevs),nluns))
                        time.sleep(1)

                util.pread2(["/sbin/udevsettle"])
            except:
                pass # Make sure we don't break the probe...

        self.iscsi.print_LUNs()
        self.iscsi.detach(sr_uuid)        
        
    def create(self, sr_uuid, size):
        # Check SCSIid not already in use by other PBDs
        if util.test_SCSIid(self.session, self.SCSIid):
            raise xs_errors.XenError('SRInUse')

        self.iscsi.attach(sr_uuid)
        try:
            if not self.iscsi._attach_LUN_bySCSIid(self.SCSIid):
                # UPGRADE FROM GEORGE: take care of ill-formed SCSIid
                upgraded = False
                matchSCSIid = False
                for file in filter(self.iscsi.match_lun, util.listdir(self.iscsi.path)):
                    path = os.path.join(self.iscsi.path,file)
                    if not util.wait_for_path(path, ISCSISR.MAX_TIMEOUT):
                        util.SMlog("Unable to detect LUN attached to host [%s]" % path)
                        continue
                    try:
                        SCSIid = scsiutil.getSCSIid(path)
                    except:
                        continue
                    try:
                        matchSCSIid = scsiutil.compareSCSIid_2_6_18(self.SCSIid, path)
                    except:
                        continue
                    if (matchSCSIid):
                        util.SMlog("Performing upgrade from George")
                        try:
                            pbd = util.find_my_pbd(self.session, self.host_ref, self.sr_ref)
                            device_config = self.session.xenapi.PBD.get_device_config(pbd)
                            device_config['SCSIid'] = SCSIid
                            self.session.xenapi.PBD.set_device_config(pbd, device_config)

                            self.dconf['SCSIid'] = SCSIid            
                            self.SCSIid = self.dconf['SCSIid']
                        except:
                            continue
                        if not self.iscsi._attach_LUN_bySCSIid(self.SCSIid):
                            raise xs_errors.XenError('InvalidDev')
                        else:
                            upgraded = True
                            break
                    else:
                        util.SMlog("Not a matching LUN, skip ... scsi_id is: %s" % SCSIid)
                        continue
                if not upgraded:
                    raise xs_errors.XenError('InvalidDev')
            self._pathrefresh(LVHDoISCSISR)
            LVHDSR.LVHDSR.create(self, sr_uuid, size)
        except Exception, inst:
            self.iscsi.detach(sr_uuid)
            raise xs_errors.XenError("SRUnavailable", opterr=inst)
        self.iscsi.detach(sr_uuid)

    def delete(self, sr_uuid):
        LVHDSR.LVHDSR.delete(self, sr_uuid)
        for i in self.iscsiSRs:
            i.detach(sr_uuid)

    def attach(self, sr_uuid):
        try:
            for i in self.iscsiSRs:
                i.attach(sr_uuid)
                if self.dconf.has_key('multiSession'):
                    # Force a manual bus refresh
                    for a in i.adapter:
                        scsiutil.rescan([i.adapter[a]])
                if not i._attach_LUN_bySCSIid(self.SCSIid):
                    raise xs_errors.XenError('InvalidDev')
            self._pathrefresh(LVHDoISCSISR)
            LVHDSR.LVHDSR.attach(self, sr_uuid)
        except Exception, inst:
            for i in self.iscsiSRs:
                i.detach(sr_uuid)
            raise xs_errors.XenError("SRUnavailable", opterr=inst)
        self._setMultipathableFlag(SCSIid=self.SCSIid)
        
    def detach(self, sr_uuid):
        LVHDSR.LVHDSR.detach(self, sr_uuid)
        for i in self.iscsiSRs:
            i.detach(sr_uuid)

    def probe(self):
        self.uuid = util.gen_uuid()

# When multipathing is enabled, since we don't refcount the multipath maps,
# we should not attempt to do the iscsi.attach/detach when the map is already present,
# as this will remove it (which may well be in use).
        if self.mpath == 'true' and self.dconf.has_key('SCSIid'):
            maps = []
            mpp_lun = False
            try:
                if (mpp_luncheck.is_RdacLun(self.dconf['SCSIid'])):
                    mpp_lun = True
                    link=glob.glob('/dev/disk/mpInuse/%s-*' % self.dconf['SCSIid'])
                else:
                    maps = mpath_cli.list_maps()
            except:
                pass

            if (mpp_lun):
                if (len(link)):
                    raise xs_errors.XenError('SRInUse')
            else:
                if self.dconf['SCSIid'] in maps:
                    raise xs_errors.XenError('SRInUse')

        self.iscsi.attach(self.uuid)
        if not self.iscsi._attach_LUN_bySCSIid(self.SCSIid):
            util.SMlog("Unable to detect LUN")
            raise xs_errors.XenError('InvalidDev')
        self._pathrefresh(LVHDoISCSISR)
        out = LVHDSR.LVHDSR.probe(self)
        self.iscsi.detach(self.uuid)
        return out

    def vdi(self, uuid):
        return LVHDoISCSIVDI(self, uuid)
    
class LVHDoISCSIVDI(LVHDSR.LVHDVDI):
    def generate_config(self, sr_uuid, vdi_uuid):
        util.SMlog("LVHDoISCSIVDI.generate_config")
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
        util.SMlog("LVHDoISCSIVDI.attach_from_config")
        try:
            self.sr.iscsi.attach(sr_uuid)
            if not self.sr.iscsi._attach_LUN_bySCSIid(self.sr.SCSIid):
                raise xs_errors.XenError('InvalidDev')
            LVHDSR.LVHDSR._cleanup(self.sr)
            return LVHDSR.LVHDVDI.attach(self, sr_uuid, vdi_uuid)
        except:
            util.logException("LVHDoISCSIVDI.attach_from_config")
            raise xs_errors.XenError('SRUnavailable', \
                        opterr='Unable to attach the heartbeat disk')


if __name__ == '__main__':
    SRCommand.run(LVHDoISCSISR, DRIVER_INFO)
else:
    SR.registerSR(LVHDoISCSISR)
