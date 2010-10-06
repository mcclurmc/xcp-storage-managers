#!/bin/sh

## global variables
. /etc/xensource-inventory
. iscsi_ctrl.sh
#. iscsi_new.sh

if [ -z ${CMD} ]; then
    CMD="/opt/xensource/bin/xe"
fi

LINE_LEN=40

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


############################################################
## General Helper Functions
############################################################

usage()
{
    echo "Incorrect arguments to script"
    echo "Usage: $1 [VARIABLE=VALUE]"
    echo "       Where Variables may be one of:"
    echo "       CMD - [/path/to/xe/binary]"
    echo "       MY_HOSTNAME"
    echo "       IQN_INITIATOR_ID"
    echo "       ISCSI_TARGET_IP"
    echo "       TARGET_SRID"
    echo "       CLONE_VMID"
    echo "       TARGET_LUN_BUS_ID"
    echo ""
    exit 1
}

process_args()
{
    out=`echo $1 | grep =`
    if [ $? -ne 0 ]; then
	echo "Arguments must be in the form 'VARIABLE=VALUE'"
	exit
    fi
    VAR=`echo $1 | cut -d= -f1`
    VAL=`echo $1 | cut -d= -f2`

    case $VAR in
	CMD) CMD=$VAL;;
	MY_HOSTNAME) MY_HOSTNAME=$VAL;;
	IQN_INITIATOR_ID) IQN_INITIATOR_ID=$VAL;;
	ISCSI_TARGET_IP) ISCSI_TARGET_IP=$VAL;;
	ISCSI_RECID) ISCSI_RECID=$VAL;;
	ISCSI_RECIQN) ISCSI_RECIQN=$VAL;;
	TARGET_SRID) TARGET_SRID=$VAL;;
	CLONE_VMID) CLONE_VMID=$VAL;;
	TARGET_LUN_BUS_ID) TARGET_LUN_BUS_ID=$VAL;;
	*)       echo "Not a valid variable"
	         return 1
	         ;;
    esac
    return 0
}

get_user_input()
{
    STRING=$1
    echo $STRING
    echo "This operation may cause data loss"
    while true
      do
      echo "Are you sure you want to continue? (y|n)"
      read ans                           #read answer from standard in
      case $ans in
	  y*) return 0;;
	  n*) exit;;
	  *)  echo "Please enter y or n";;
      esac
    done
}

#Progress output
debug()
{
    echo "$1"
    return
}

#Operation name
#Args: Message, Color
debug_test()
{
    LEN=`echo $1 | wc -c`
    
    echo -en "$black"
    echo -en "$1"
    for (( dbgout = $LEN ; dbgout <= $LINE_LEN; dbgout++ ))
      do
	  echo -en "."
    done
    return
}

#Operation result
debug_result()
{
    if [ $1 == 0 ]; then
	echo -en "$green"
	echo -en "PASS"
    else
	echo -en "$red"
	echo -en "FAIL"
    fi
    reset
    return
}

reset()
{
    tput sgr0
    echo
    return
}

#Args: servicename, operation
service_op()
{
    operation=$2
    servicename=$1
    if service ${servicename} ${operation} >& /dev/null; then
	return 0
    fi
    return 1
}

#Args: SRID
switch_SR()
{
    debug_test "Switching default SR"
    { $CMD host-sr-set $ARGS sr-id=$1 >& /dev/null; RETVAL=$?; } || true
    if [ $? -gt 0 ]; then
	debug_result 1
	echo "Switching default SR failed"
	return 1
    fi
    debug_result 0
    return 0
}

#Clone VM
#Args: SRC SR, DST SR, VMID
cloneSRVM()
{
    debug_test "Cloning VM"
    { $CMD sr-vm-clone $ARGS sr-id=$2 vm-id=$3 auto_poweron=false new-name="Clone of $3" new-description="Clone of $3" >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -gt 0 ]; then
	debug_result 1
	echo "Cloning VM failed"
	return 1
    fi
    debug_result 0
    return 0
}


############################################################
## SM General Helper Functions
############################################################
#Args: "Device String"
sm_Create()
{
    debug_test "Create SR"
    { sm create -f -D ${DRIVER_TYPE} $1 >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -gt 0 ]; then
	debug_result 1
	echo "Create SR failed"
	return 1
    fi
    debug_result 0
    GLOBAL_RET=$output
    return 0
}

sm_Attach()
{
    debug_test "Attach SR"
    { sm attach -D ${DRIVER_TYPE} $1 $2 >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -gt 0 ]; then
	debug_result 1
	echo "Attach SR failed"
	return 1
    fi
    debug_result 0
    return 0
}

sm_Detach()
{
    debug_test "Detach SR"
    { sm detach -f -D ${DRIVER_TYPE} $1 >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -gt 0 ]; then
	debug_result 1
	echo "Detach SR failed"
	return 1
    fi
    debug_result 0
    return 0
}
