#!/usr/bin/env python
# Copyright (c) 2005-2007 XenSource, Inc. All use and distribution of this
# copyrighted material is governed by and subject to terms and conditions
# as licensed by XenSource, Inc. All other rights reserved.
# Xen, XenSource and XenEnterprise are either registered trademarks or
# trademarks of XenSource Inc. in the United States and/or other countries.

import sys
import xs_errors
import SR, SRCommand
# We could autodiscover these too
import FileSR, NFSSR, LVMSR, EXTSR, RAWSR

def main():
    command = SRCommand.SRCommand()
    command.parse("")

    if not command.type:
        raise SRCommand.SRInvalidArgumentException( \
                                'missing driver type argument: -t <type>')
    if not command.dconf:
        raise SRCommand.SRInvalidArgumentException('missing dconf string')
    if not command.cmdname:
        raise SRCommand.SRInvalidArgumentException('missing command')
    if not command.sr_uuid:
        raise SRCommand.SRInvalidArgumentException('missing SR UUID')
    if command.cmdtype == 'vdi' and not command.vdi_uuid:
        raise SRCommand.SRInvalidArgumentException('missing VDI UUID')
    if not command.cmd:
        raise SRCommand.SRInvalidArgumentException('unknown command')
    if command.args is None:
        raise SRCommand.SRInvalidArgumentException('wrong number of arguments')

    driver = SR.driver(command.type)
    sr = driver(command.dconf, command.sr_uuid)

    command.run(sr)

try:
    main()
except SRCommand.SRInvalidArgumentException, inst:
    print inst
    sys.exit(1)
except SR.SRException, inst:
    print inst
    sys.exit(inst.errno)
