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
# EXToISCSISR: EXT over ISCSI software initiator SR driver
#

import SR, VDI, EXTSR, ISCSISR, SRCommand, util
import statvfs, time
import os, socket, sys, re
import xs_errors

class EXToISCSISR(EXTSR.EXTSR):
    """LVM over ISCSI storage repository"""
    def handles(type):
        if type == "extoiscsi":
            return True
        return False
    handles = staticmethod(handles)

    def load(self, sr_uuid):
        driver = SR.driver('iscsi')
        self.iscsi = driver(self.original_srcmd, sr_uuid)

        # User must specify a LUN ID(s) for adding to the VG
        if not self.dconf.has_key('LUNid') or  not self.dconf['LUNid']:
            raise xs_errors.XenError('ConfigLUNIDMissing')
        self.dconf['device'] = os.path.join(self.iscsi.path,"LUN%s" % \
                               self.dconf['LUNid'])

        if not self.iscsi.attached:
            # Must attach SR here in order to load VG
            self.iscsi.attach(sr_uuid)
        super(EXToISCSISR, self).load(sr_uuid)

    def delete(self, sr_uuid):
        super(EXToISCSISR, self).delete(sr_uuid)
        self.iscsi.detach(sr_uuid)
        
    def detach(self, sr_uuid):
        super(EXToISCSISR, self).detach(sr_uuid)
        self.iscsi.detach(sr_uuid)
                
if __name__ == '__main__':
    SRCommand.run(EXToISCSISR)
else:
    SR.registerSR(EXToISCSISR)
