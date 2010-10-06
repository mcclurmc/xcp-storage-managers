#!/bin/bash
############################################################
## Test-wide variables
############################################################

## Default script variables file

##Source all driver and substrate files
. ./lvhd_functions.sh
. ./lvm_functions.sh
. ./file_functions.sh
. ./nfs_functions.sh
. ./lvmoiscsi_functions.sh
. ./extoiscsi_functions.sh
. ./netapp_functions.sh
. ./equal_functions.sh
. ./lvmohba_functions.sh

. ./file_config.sh
. ./nfs_config.sh
. ./liscsi_config.sh

## Default XE parameters
if [ -z ${USERNAME} ]; then
    USERNAME="root"
fi

if [ -z ${CMD} ]; then
    CMD="/opt/xensource/bin/xe"
fi

if [ -z ${CLONE_DEBUG} ]; then
    CLONE_DEBUG=0
fi

if [ -z ${BRIDGE} ]; then
    BRIDGE="xenbr0"
fi

#chap default disabled
if [ -z ${CHAP} ] ; then
    USE_CHAP=0
fi


#Misc Parameters
VERSION="StorageManager Tests v1.1"
DATE=`date +%s`
LOGFILE=`echo /tmp/SR-test-$DATE.tmp`
GLOBAL_INDEX=0
EXITVAL=0
NUM_TESTS=1
LINE_LEN=50
GUEST_IP_SLEEP_DELAY=30
#SM=/root/sm-obj/sm
#basis for the tools directory on a webserver somewhere
TOOLS_ROOT="http://xenrt.hq.xensource.com/storage"

#Switch Debug on/off
GLOBAL_DEBUG=1

#Host Reboot timeouts
if [ -z ${STOP_TIME} ]; then
    STOP_TIME=30
fi

if [ -z ${MAX_PERIOD} ]; then
    MAX_PERIOD=300
fi

#Color constants
black='\E[30m'
red='\E[31m'
green='\E[32m'
yellow='\E[33m'
blue='\E[34m'
magenta='\E[35m'
cyan='\E[36m'
white='\E[37m'

#Reset terminal
reset="tput sgr0"
