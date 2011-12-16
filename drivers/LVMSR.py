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
# LVMSR: local-LVM storage repository
#

import SR, VDI, SRCommand, util, lvutil, scsiutil
import errno
import os, sys
import xml.dom.minidom
import xs_errors

CAPABILITIES = ["SR_PROBE","VDI_CREATE","VDI_DELETE","VDI_ATTACH",
                "VDI_DETACH","VDI_RESIZE","VDI_RESIZE_ONLINE"]

CONFIGURATION = [ [ 'device', 'local device path (required) (e.g. /dev/sda3)' ] ]
                
DRIVER_INFO = {
    'name': 'LVM',
    'description': 'SR plugin which represents disks as Logical Volumes within a locally-attached Volume Group',
    'vendor': 'Citrix Systems Inc',
    'copyright': '(C) 2008 Citrix Systems Inc',
    'driver_version': '1.0',
    'required_api_version': '1.0',
    'capabilities': CAPABILITIES,
    'configuration': CONFIGURATION
    }

TYPE = 'lvm'

LVM_PREFIX = 'VG_XenStorage-'

class LVMSR(SR.SR):
    """Local file storage repository"""
    def handles(type):
        if type == TYPE:
            return True
        return False
    handles = staticmethod(handles)

    def load(self, sr_uuid):
        self.sr_vditype = 'phy'

        if not self.dconf.has_key('device') or not self.dconf['device']:
            raise xs_errors.XenError('ConfigDeviceMissing',)
        self.root = self.dconf['device']
        for dev in self.root.split(','):
            if not self._isvalidpathstring(dev):
                raise xs_errors.XenError('ConfigDeviceInvalid', \
                      opterr='path is %s' % dev)

        self.vgname = LVM_PREFIX + sr_uuid
        self.path = os.path.join("/dev", self.vgname)
        self.bufferedVDI = {}
        self.SRmaster = ''
        self.isMaster = False
        if self.dconf.has_key('SRmaster') and self.dconf['SRmaster'] == 'true':
            self.SRmaster = '--master'
            self.isMaster = True

    def create(self, sr_uuid, size):
        if not self.isMaster:
            util.SMlog('sr_create blocked for non-master')
            raise xs_errors.XenError('LVMMaster')

        if lvutil._checkVG(self.vgname):
            raise xs_errors.XenError('SRExists')

        # Check none of the devices already in use by other PBDs
        if util.test_hostPBD_devs(self.session, self.root):
            raise xs_errors.XenError('SRInUse')

        # Check serial number entry in SR records
        for dev in self.root.split(','):
            if util.test_scsiserial(self.session, dev):
                raise xs_errors.XenError('SRInUse')
        
        systemroot = util.getrootdev()
        rootdev = self.root.split(',')[0]
        # Create PVs for each device
        for dev in self.root.split(','):
            if dev in [systemroot, '%s1'%systemroot, '%s2'%systemroot]:
                raise xs_errors.XenError('Rootdev', \
                      opterr=('Device %s contains core system files, ' \
                              + 'please use another device') % dev)
            if not os.path.exists(dev):
                raise xs_errors.XenError('InvalidDev', \
                      opterr=('Device %s does not exist') % dev)

            try:
                f = os.open("%s" % dev, os.O_RDWR | os.O_EXCL)
            except:
                raise xs_errors.XenError('SRInUse', \
                      opterr=('Device %s in use, please check your existing ' \
                      + 'SRs for an instance of this device') % dev)
            os.close(f)
            try:
                # Overwrite the disk header, try direct IO first
                cmd = ["dd","if=/dev/zero","of=%s" % dev,"bs=1M","count=100", \
                       "oflag=direct"]
                util.pread2(cmd)
            except util.CommandException, inst:
                if inst.code == errno.EPERM:
                    try:
                        # Overwrite the disk header, try normal IO
                        cmd = ["dd","if=/dev/zero","of=%s" % dev,"bs=1M", \
                               "count=100"]
                        util.pread2(cmd)
                    except util.CommandException, inst:
                        raise xs_errors.XenError('LVMWrite', \
                              opterr='device %s' % dev)
                else:
                    raise xs_errors.XenError('LVMWrite', \
                          opterr='device %s' % dev)
            try:
                cmd = ["pvcreate", "--metadatasize", "10M", dev]
                util.pread2(cmd)
            except util.CommandException, inst:
                raise xs_errors.XenError('LVMPartCreate', \
                      opterr='error is %d' % inst.code)

        # Create VG on first device
        try:
            cmd = ["vgcreate", self.vgname, rootdev]
            util.pread2(cmd)
        except :
            raise xs_errors.XenError('LVMGroupCreate')

        # Then add any additional devs into the VG
        for dev in self.root.split(',')[1:]:
            try:
                cmd = ["vgextend", self.vgname, dev]
                util.pread2(cmd)
            except util.CommandException, inst:
                # One of the PV args failed, delete SR
                try:
                    cmd = ["vgremove", self.vgname]
                    util.pread2(cmd)
                except:
                    pass
                raise xs_errors.XenError('LVMGroupCreate')
        try:
            cmd = ["vgchange", "-an", self.SRmaster, self.vgname]
            util.pread2(cmd)
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMUnMount', \
                  opterr='errno is %d' % inst.code)

        #Update serial number string
        scsiutil.add_serial_record(self.session, self.sr_ref, \
                  scsiutil.devlist_to_serialstring(self.root.split(',')))

    def delete(self, sr_uuid):
        if not self.isMaster:
            util.SMlog('sr_delete blocked for non-master')
            raise xs_errors.XenError('LVMMaster')

        # Load the buffer to verify that the VG exists and that there are no LVs
        self._loadbuffer()
        
        # Check PVs match VG
        try:
            for dev in self.root.split(','):
                cmd = ["pvs", dev]
                txt = util.pread2(cmd)
                if txt.find(self.vgname) == -1:
                    raise xs_errors.XenError('LVMNoVolume', \
                          opterr='volume is %s' % self.vgname)
        except util.CommandException, inst:
            raise xs_errors.XenError('PVSfailed', \
                  opterr='error is %d' % inst.code)

        if self.bufferedVDI.keys():
            raise xs_errors.XenError('SRNotEmpty')

        try:
            cmd = ["vgremove", self.vgname]
            util.pread2(cmd)

            for dev in self.root.split(','):
                cmd = ["pvremove", dev]
                util.pread2(cmd)
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMDelete', \
                  opterr='errno is %d' % inst.code)

    def attach(self, sr_uuid):
        if not util.match_uuid(sr_uuid) or not lvutil._checkVG(self.vgname):
            raise xs_errors.XenError('SRUnavailable', \
                  opterr='no such volume group: %s' % self.vgname)

        if self.isMaster:
            #Update SCSIid string
            util.SMlog("Calling devlist_to_serial")
            scsiutil.add_serial_record(self.session, self.sr_ref, \
                  scsiutil.devlist_to_serialstring(self.root.split(',')))
            
        # Set the block scheduler
        try:
            self.other_config = self.session.xenapi.SR.get_other_config(self.sr_ref)
            if self.other_config.has_key('scheduler') and self.other_config['scheduler'] != self.sched:
                self.sched = self.other_config['scheduler']
            for dev in self.root.split(','):
                realdev = os.path.realpath(dev)[5:]
                util.set_scheduler(realdev, self.sched)
        except:
            pass

    def probe(self):
        return lvutil.srlist_toxml(lvutil.scan_srlist(LVM_PREFIX, self.root))

    def detach(self, sr_uuid):
        if not lvutil._checkVG(self.vgname):
            return

        # Deactivate any active LVs
        try:
            cmd = ["vgchange", "-an", self.SRmaster, self.vgname]
            util.pread2(cmd)
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMUnMount', \
                  opterr='deactivating VG failed, VDIs in use?')

    def scan(self, sr_uuid):
        if not self.isMaster:
            util.SMlog('sr_scan blocked for non-master')
            raise xs_errors.XenError('LVMMaster')

        self._loadbuffer()
        self._loadvdis()
        stats = lvutil._getVGstats(self.vgname)
        self.physical_size = stats['physical_size']
        self.physical_utilisation = stats['physical_utilisation']
        self.virtual_allocation = self.physical_utilisation
        # Update the SR record
        self._db_update()
        # Synchronise the VDIs
        scanrecord = SR.ScanRecord(self)
        # XXX: never forget VDI records to work around glitch where some
        # volumes temporarily disappeared
        scanrecord.synchronise_new()
        scanrecord.synchronise_existing()

    def content_type(self, sr_uuid):
        return super(LVMSR, self).content_type(sr_uuid)

    def vdi(self, uuid):
        return LVMVDI(self, uuid)
    
    def _loadbuffer(self):
        try:
            cmd = ["lvs", "--noheadings", "--units", "b", self.vgname]
            text = util.pread2(cmd)
            for line in text.split('\n'):
                if line.find("LV-") != -1:
                    bufferval = BufferedLVSCAN(self, line)
                    self.bufferedVDI[bufferval.uuid] = bufferval
        except:
            raise xs_errors.XenError('SRUnavailable', \
                  opterr='no such volume group: %s' % self.vgname)

    def _loadvdis(self):
        for key in self.bufferedVDI.iterkeys():
            self.vdis[key] = self.bufferedVDI[key]
        return


class LVMVDI(VDI.VDI):
    def load(self, vdi_uuid):
        self.lvname = "LV-%s" % (vdi_uuid)
        self.path = os.path.join(self.sr.path, self.lvname)
        self.uuid = vdi_uuid
        self.location = self.uuid
        
    def create(self, sr_uuid, vdi_uuid, size):
        if not self.sr.isMaster:
            util.SMlog('vdi_create blocked for non-master')
            raise xs_errors.XenError('LVMMaster')

        try:
            mb = 1024L * 1024L
            size_mb = (long(size) + mb - 1L) / mb # round up
            # Rather than bailing out for small sizes, just round up to 1 MiB. The
            # LVM code will round up to the nearest PE size anyway (probably 4 MiB)
            if size_mb == 0:
                size_mb = 1
            
            if lvutil._checkLV(self.path):
                raise xs_errors.XenError('VDIExists')

            # Verify there's sufficient space for the VDI
            stats = lvutil._getVGstats(self.sr.vgname)
            freespace = stats['physical_size'] - stats['physical_utilisation']
            if freespace < long(size):
                raise xs_errors.XenError('SRNoSpace')              

            cmd = ["lvcreate", "-n", self.lvname, "-L", str(size_mb), \
                   self.sr.vgname]
            text = util.pread2(cmd)

            cmd = ["lvchange", "-an", self.path]
            text = util.pread2(cmd)

            self.size = lvutil._getLVsize(self.path)
            self.utilisation = self.size
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMCreate', \
                  opterr='lv operation failed error is %d' % inst.code)
        
        self._db_introduce()
        return super(LVMVDI, self).get_params()

    def delete(self, sr_uuid, vdi_uuid):
        if not self.sr.isMaster:
            util.SMlog('vdi_delete blocked for non-master')
            raise xs_errors.XenError('LVMMaster')

        if not lvutil._checkLV(self.path):
            return

        try:
            cmd = ["lvremove", "-f", self.path]
            text = util.pread2(cmd)
            self._db_forget()
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMDelete', \
                  opterr='lv operation failed error is %d' % inst.code)

    def attach(self, sr_uuid, vdi_uuid):
        try:
            if not os.path.exists(self.path) or not self._isactive(self.path):
                cmd = ["lvchange", "-ay", self.path]
                text = util.ioretry(lambda: util.pread2(cmd))
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMMount', \
                  opterr='lvchange failed error is %d' % inst.code)
        if self.sr.srcmd.params.has_key("vdi_ref"):
            vdi_ref = self.sr.srcmd.params['vdi_ref']
            scsiutil.update_XS_SCSIdata(self.session, vdi_ref, vdi_uuid, \
                                    scsiutil.gen_synthetic_page_data(vdi_uuid))
        return super(LVMVDI, self).attach(sr_uuid, vdi_uuid)

    def detach(self, sr_uuid, vdi_uuid):
        try:
            if os.path.exists(self.path):
                cmd = ["lvchange", "-an", self.path]
                text = util.ioretry(lambda:util.pread2(cmd))
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMUnMount', \
                  opterr='lvchange failed error is %d' % inst.code)

    def resize(self, sr_uuid, vdi_uuid, size):
        if not self.sr.isMaster:
            util.SMlog('vdi_resize blocked for non-master')
            raise xs_errors.XenError('LVMMaster')

        try:
            self.size = lvutil._getLVsize(self.path)
        except:
            raise xs_errors.XenError('VDIUnavailable', \
                  opterr='no such VDI %s' % self.path)

        size_mb = long(size) / (1024 * 1024)
        try:
            assert(size_mb >= self.size/(1024 * 1024))
            if size == self.size:
                self.size = lvutil._getLVsize(self.path)
                self.utilisation = self.size
                return super(LVMVDI, self).get_params()

            # Verify there's sufficient space for the VDI
            stats = lvutil._getVGstats(self.sr.vgname)
            freespace = stats['physical_size'] - stats['physical_utilisation']
            if freespace < long(size) - self.size:
                raise xs_errors.XenError('SRNoSpace')
            
            cmd = ["lvresize", "-L", str(size_mb), self.path]
            text = util.pread2(cmd)
            
            self.size = lvutil._getLVsize(self.path)
            self.utilisation = self.size
            self._db_update()
            
            return super(LVMVDI, self).get_params()
        
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMResize', \
                  opterr='lvresize failed error is %d' % inst.code)
        except AssertionError:
            raise xs_errors.XenError('VDISize', \
                  opterr='Reducing the size of the disk is not permitted')

    def resize_online(self, sr_uuid, vdi_uuid, size):
        # All resizes are the same, it's only the calling context which differs.
        return self.resize(sr_uuid, vdi_uuid, size)

    def clone(self, sr_uuid, vdi_uuid):
        raise xs_errors.XenError('Unimplemented', \
              opterr='LVM clone unsupported')

    def snapshot(self, sr_uuid, vdi_uuid):
        raise xs_errors.XenError('Unimplemented', \
              opterr='LVM snapshot unsupported')

    def _isactive(self, path):
        try:
            f=open(path, 'r')
            f.close()
            return True
        except IOError:
            return False
    
class BufferedLVSCAN(VDI.VDI):
    def load(self, line):
        fields = line.split()
        self.lvname = fields[0]
        self.uuid = fields[0].replace("LV-","")
        self.size = long(fields[3].replace("B",""))
        self.utilisation = self.size
        self.location = self.uuid
        if len(fields) > 4:
            self.parent = fields[4].replace("LV-","")


if __name__ == '__main__':
    SRCommand.run(LVMSR, DRIVER_INFO)
else:
    SR.registerSR(LVMSR)
