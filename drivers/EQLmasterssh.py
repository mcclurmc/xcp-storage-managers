#! /usr/bin/env python
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

import sshutil, util
import time, os, sys, re, signal

KEEPALIVE = 20
SLEEP = 5

def EQLssh_init(target, username, password):
    
    conn_cred = [target, username, password]
    conn = sshutil.SSHSession(conn_cred)
    conn.master_login()

    util.daemon()
    timer = KEEPALIVE
    command = "whoami"
    while os.path.exists(conn.statefile):
        time.sleep(SLEEP)
        timer -= SLEEP
        if timer <= 0:
            str = conn.command(command)
            timer = KEEPALIVE
    
    conn.close()
    os.kill(conn.sshpid,signal.SIGQUIT)


target = ""
username = ""
password = ""

for line in sys.stdin:
    if line.find("target=") != -1:
        target = re.sub("\s+", "", line.split("=")[-1])
    elif line.find("username=") != -1:
        username = re.sub("\s+", "", line.split("=")[-1])
    elif line.find("password=") != -1:
        password = re.sub("\s+", "", line.split("=")[-1])

if not target or not username or not password:
    print "Failed to read all credentials from STDIN"
    sys.exit(-1)

EQLssh_init(target, username, password)
