#! /usr/bin/env python
# Copyright (c) 2008 Citrix Systems, Inc. All use and distribution of this
# copyrighted material is governed by and subject to terms and conditions
# as licensed by Citrix Systems, Inc. All other rights reserved.
# Xen, XenSource and XenEnterprise are either registered trademarks or
# trademarks of Citrix Systems, Inc. in the United States and/or other 
# countries.

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
