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
