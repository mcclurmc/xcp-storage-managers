#!/usr/bin/env python
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
# EXTSR: Based on local-file storage repository, mounts ext3 partition

import SR, SRCommand, FileSR, util, lvutil, scsiutil

import os
import tempfile
import errno
import xs_errors
import vhdutil
from lock import Lock
import cleanup

CAPABILITIES = ["SR_PROBE","SR_UPDATE", "SR_SUPPORTS_LOCAL_CACHING", \
                "VDI_CREATE","VDI_DELETE","VDI_ATTACH","VDI_DETACH", \
                "VDI_UPDATE","VDI_CLONE","VDI_SNAPSHOT","VDI_RESIZE", \
                "VDI_RESET_ON_BOOT","VDI_RESIZE_ONLINE"]

CONFIGURATION = [ [ 'device', 'local device path (required) (e.g. /dev/sda3)' ] ]
                  
DRIVER_INFO = {
    'name': 'Local EXT3 VHD',
    'description': 'SR plugin which represents disks as VHD files stored on a local EXT3 filesystem, created inside an LVM volume',
    'vendor': 'Citrix Systems Inc',
    'copyright': '(C) 2008 Citrix Systems Inc',
    'driver_version': '1.0',
    'required_api_version': '1.0',
    'capabilities': CAPABILITIES,
    'configuration': CONFIGURATION
    }

EXT_PREFIX = 'XSLocalEXT-'

class EXTSR(FileSR.FileSR):
    """EXT3 Local file storage repository"""
    def handles(srtype):
        return srtype == 'ext'
    handles = staticmethod(handles)

    def load(self, sr_uuid):
        self.ops_exclusive = FileSR.OPS_EXCLUSIVE
        self.lock = Lock(vhdutil.LOCK_TYPE_SR, self.uuid)
        self.sr_vditype = SR.DEFAULT_TAP
        if not self.dconf.has_key('device') or not self.dconf['device']:
            raise xs_errors.XenError('ConfigDeviceMissing')

        self.root = self.dconf['device']
        for dev in self.root.split(','):
            if not self._isvalidpathstring(dev):
                raise xs_errors.XenError('ConfigDeviceInvalid', \
                      opterr='path is %s' % dev)
        self.path = os.path.join(SR.MOUNT_BASE, sr_uuid)
        self.vgname = EXT_PREFIX + sr_uuid
        self.remotepath = os.path.join("/dev",self.vgname,sr_uuid)
        self.attached = self._checkmount()

    def delete(self, sr_uuid):
        self.attach(sr_uuid)
        super(EXTSR, self).delete(sr_uuid)
        self.detach(sr_uuid)

        # Check PVs match VG
        try:
            for dev in self.root.split(','):
                cmd = ["pvs", dev]
                txt = util.pread2(cmd)
                if txt.find(self.vgname) == -1:
                    raise xs_errors.XenError('VolNotFound', \
                          opterr='volume is %s' % self.vgname)
        except util.CommandException, inst:
            raise xs_errors.XenError('PVSfailed', \
                  opterr='error is %d' % inst.code)

        # Remove LV, VG and pv
        try:
            cmd = ["lvremove", "-f", self.remotepath]
            util.pread2(cmd)
            
            cmd = ["vgremove", self.vgname]
            util.pread2(cmd)

            for dev in self.root.split(','):
                cmd = ["pvremove", dev]
                util.pread2(cmd)
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMDelete', \
                  opterr='errno is %d' % inst.code)
            
    def attach(self, sr_uuid):
        if not self._checkmount():
            try:
                #Activate LV
                cmd = ['lvchange','-ay',self.remotepath]
                util.pread2(cmd)
                
                # make a mountpoint:
                if not os.path.isdir(self.path):
                    os.makedirs(self.path)
            except util.CommandException, inst:
                raise xs_errors.XenError('LVMMount', \
                      opterr='Unable to activate LV. Errno is %d' % inst.code)
            
            try:
                util.pread(["fsck", "-a", self.remotepath])
            except util.CommandException, inst:
                if inst.code == 1:
                    util.SMlog("FSCK detected and corrected FS errors. Not fatal.")
                else:
                    raise xs_errors.XenError('LVMMount', \
                         opterr='FSCK failed on %s. Errno is %d' % (self.remotepath,inst.code))

            try:
                util.pread(["mount", self.remotepath, self.path])

                FileSR.FileSR.attach(self, sr_uuid)

                self.attached = True
            except util.CommandException, inst:
                raise xs_errors.XenError('LVMMount', \
                      opterr='Failed to mount FS. Errno is %d' % inst.code)
        #Update SCSIid string
        scsiutil.add_serial_record(self.session, self.sr_ref, \
                scsiutil.devlist_to_serialstring(self.root.split(',')))
        
        # Set the block scheduler
        for dev in self.root.split(','): self.block_setscheduler(dev)

    def detach(self, sr_uuid):
        if not self._checkmount():
            return
        cleanup.abort(self.uuid)
        try:
            # Change directory to avoid unmount conflicts
            os.chdir(SR.MOUNT_BASE)
            
            # unmount the device
            util.pread(["umount", self.path])

            # remove the mountpoint
            os.rmdir(self.path)
            self.path = None

            # deactivate SR
            try:
                cmd = ["lvchange", "-an", self.remotepath]
                util.pread2(cmd)
            except util.CommandException, inst:
                raise xs_errors.XenError('LVMUnMount', \
                      opterr='lvm -an failed errno is %d' % inst.code)

            self.attached = False
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMUnMount', \
                  opterr='errno is %d' % inst.code)
        except:
            raise xs_errors.XenError('LVMUnMount')

    def probe(self):
        return lvutil.srlist_toxml(lvutil.scan_srlist(EXT_PREFIX, self.root))

    def create(self, sr_uuid, size):
        if self._checkmount():
            raise xs_errors.XenError('SRExists')

        # Check none of the devices already in use by other PBDs
        if util.test_hostPBD_devs(self.session, self.root):
            raise xs_errors.XenError('SRInUse')

        # Check serial number entry in SR records
        for dev in self.root.split(','):
            if util.test_scsiserial(self.session, dev):
                raise xs_errors.XenError('SRInUse')

        if not lvutil._checkVG(self.vgname):
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
                    raise xs_errors.XenError('SRInUse')
                os.close(f)
                try:
                    # Overwrite the disk header, try direct IO first
                    cmd = ["dd","if=/dev/zero","of=%s" % dev,"bs=1M", \
                           "count=100","oflag=direct"]
                    util.pread2(cmd)
                except util.CommandException, inst:
                    if inst.code == errno.EPERM:
                        try:
                            cmd = ["dd","if=/dev/zero","of=%s" % dev,"bs=1M", \
                                   "count=100"]
                            util.pread2(cmd)
                        except util.CommandException, inst:
                            raise xs_errors.XenError('LVMWrite', 
                                  opterr='disk %s, error %d' % (dev, inst.code))
                    else:
                        raise xs_errors.XenError('LVMWrite', 
                              opterr='disk %s, error %d' % (dev, inst.code))
                if not lvutil._checkPV(dev):
                    try:                        
                        cmd = ["pvcreate", "--metadatasize", "10M", dev]
                        util.pread2(cmd)
                    except util.CommandException, inst:
                        raise xs_errors.XenError('LVMPartCreate', 
                              opterr='disk %s, error %d' % (dev, inst.code))
                else:
                    raise xs_errors.XenError('LVMPartInUse', 
                          opterr='disk %s' % dev)

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
        if lvutil._checkLV(self.remotepath):
            raise xs_errors.XenError('SRExists')
        try:
            stats = lvutil._getVGstats(self.vgname)
            size_mb = stats['freespace'] / (1024 * 1024)
            assert(size_mb > 0)
            cmd = ["lvcreate", "-n", sr_uuid, "-L", str(size_mb), \
                   self.vgname]
            text = util.pread(cmd)

            cmd = ["lvchange", "-ay", self.remotepath]
            text = util.pread(cmd)
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMCreate', \
                  opterr='lv operation, error %d' % inst.code)
        except AssertionError:
            raise xs_errors.XenError('SRNoSpace', \
                  opterr='Insufficient space in VG %s' % self.vgname)

        try:
            util.pread2(["mkfs.ext3", "-F", self.remotepath])
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMFilesystem', \
                  opterr='mkfs failed error %d' % inst.code)

        #Update serial number string
        scsiutil.add_serial_record(self.session, self.sr_ref, \
                  scsiutil.devlist_to_serialstring(self.root.split(',')))

    def vdi(self, uuid, loadLocked = False):
        if not loadLocked:
            return EXTFileVDI(self, uuid)
        return EXTFileVDI(self, uuid)

    def _checkmount(self):
        return self.path and os.path.ismount(self.path)

class EXTFileVDI(FileSR.FileVDI):
    def attach(self, sr_uuid, vdi_uuid):
        try:
            vdi_ref = self.sr.srcmd.params['vdi_ref']
            self.session.xenapi.VDI.remove_from_xenstore_data(vdi_ref, \
                    "vdi-type")
            self.session.xenapi.VDI.remove_from_xenstore_data(vdi_ref, \
                    "storage-type")
            self.session.xenapi.VDI.add_to_xenstore_data(vdi_ref, \
                    "storage-type", "ext")
        except:
            util.logException("EXTSR:attach")
            pass

        return super(EXTFileVDI, self).attach(sr_uuid, vdi_uuid)


if __name__ == '__main__':
    SRCommand.run(EXTSR, DRIVER_INFO)
else:
    SR.registerSR(EXTSR)
