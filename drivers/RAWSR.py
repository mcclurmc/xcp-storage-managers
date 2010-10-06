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
# RAWSR: local raw file storage repository for OSS compatibility

import SR, SRCommand, FileSR, util
import os
import xs_errors

CAPABILITIES = [""]

CONFIGURATION = [ [ 'location', 'path where images are stored (required)' ] ]

                  
DRIVER_INFO = {
    'name': 'Local raw',
    'description': 'Raw file-based image Storage Repository driver. No fast clone or snapshot capability.',
    'vendor': 'Citrix Systems Inc',
    'copyright': '(C) 2008 Citrix Systems Inc',
    'driver_version': '1.0',
    'required_api_version': '1.0',
    'capabilities': CAPABILITIES,
    'configuration': CONFIGURATION
    }

class RAWSR(FileSR.FileSR):
    """Local raw file storage repository"""
    def handles(type):
        return type == 'aio'
    handles = staticmethod(handles)

    def load(self, sr_uuid):
        self.sr_vditype = 'aio'
        if not self.dconf.has_key('location') or  not self.dconf['location']:
            raise xs_errors.XenError('ConfigLocationMissing')
        self.path = self.dconf['location']
        self.attached = False

    def scan(self, sr_uuid):
        if not self._checkpath(self.path):
            raise xs_errors.XenError('SRUnavailable', \
                  opterr='no such directory %s' % self.path)

        # We're gonna get passed a list of VDIs in our dconf string,
        # that the client has put in the PBD...
        files = {}
        
        for key in self.dconf.keys():
            if key.startswith("uuid-"):
                files[key[5:]] = self.dconf[key]

        # Lets create a link for VDIs that should exist
        for vdi_uuid, filename in files.items():
            fullpath = os.path.join(
               self.path, "%s.%s" % (vdi_uuid,self.sr_vditype))
            if not os.path.exists(fullpath) \
                   and os.path.exists(filename):
                os.symlink(filename, fullpath)

        # And remove them for VDIs that shouldn't exist
        should_exist = map(lambda k: "%s.%s" % (k,self.sr_vditype), files.keys())
        do_exist     = os.listdir(self.path)

        should_not_exist = [exists
                            for exists in do_exist
                            if exists not in should_exist]

        for link in should_not_exist:
            os.remove(os.path.join(
               self.path, link))

        # Now leave the super class to do the hard work...        
        self._loadvdis()

        self.physical_utilisation = 0
        self.virtual_allocation = 0
        for uuid, vdi in self.vdis.iteritems():
            self.virtual_allocation += vdi.size
        self.physical_utilisation  = self._getutilisation()
        self.physical_size = self._getsize()
            
        return super(RAWSR, self).scan(sr_uuid)

    def vdi(self, uuid):
        return RAWVDI(self, uuid)
            
class RAWVDI(FileSR.FileVDI):
    def load(self, vdi_uuid):
        self.vdi_type = 'aio'
        self.path = os.path.join(self.sr.path, "%s.%s" % \
                                 (vdi_uuid,self.vdi_type))
        self.label = os.readlink(self.path)
        self.size  = os.stat(self.label).st_size
        
        if os.path.exists(self.path):
            try:
                st = os.stat(self.path)
                self.size = long(st.st_size)
                self.utilisation = long(st.st_size)
            except OSError, inst:
                raise xs_errors.XenError('EIO', \
                      opterr='failed to star %s error is %d' % self.path % \
                      inst.sterror)

    def create(self, sr_uuid, vdi_uuid, size):
        # This is a no-op in this driver, VDI's will be introduced via 
        # separate call vdi_introduce
        raise xs_errors.XenError('Unimplemented', \
              opterr='VDI create unsupported')

    def delete(self, sr_uuid, vdi_uuid):
        raise xs_errors.XenError('Unimplemented', \
              opterr='VDI delete unsupported')

    def clone(self, sr_uuid, vdi_uuid):
        raise xs_errors.XenError('Unimplemented', \
              opterr='VDI clone unsupported')

    def snapshot(self, sr_uuid, vdi_uuid):
        raise xs_errors.XenError('Unimplemented', \
              opterr='VDI snapshot unsupported')

if __name__ == '__main__':
    SRCommand.run(RAWSR, DRIVER_INFO)
else:
    SR.registerSR(RAWSR)
