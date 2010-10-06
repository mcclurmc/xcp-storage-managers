#!/bin/bash
## This is the iscsi-specific configuration script
## to enable setup of iscsi-backed SRs

if [ -z ${MY_HOSTNAME} ]; then
    MY_HOSTNAME=`hostname`
fi

ISCSIADM="/sbin/iscsiadm"
INITIATORNAME_FILE="/etc/iscsi/initiatorname.iscsi"

# Wait for device to appear
iqn_wait()
{
    TARGET_IQN=$1
    count=0
    while [ $count -lt 10 ]
      do
      if [ -d "/dev/iscsi/${TARGET_IQN}" ]; then
	  return 0
      else
	  count=`expr $count + 1`
	  sleep 1
      fi
    done
    return 1
}

verify_device()
{
    TARGET_IQN=$1
    if [ -e /dev/iscsi/${TARGET_IQN}/LUN${TARGET_LUN_BUS_ID} ]; then
	return 0
    else
	return 1
    fi
}

#Add IQN identifier
iqn_initialise()
{
    debug_test "Setup IQN identifier"
    if [ -e "$INITIATORNAME_FILE" ]; then
	# Test that the file doesn't already have a different IQN
	
	if grep "InitiatorName=" $INITIATORNAME_FILE; then
	    { grep ${IQN_INITIATOR_ID} $INITIATORNAME_FILE; RETVAL=$?; } || true
	    if [ $RETVAL -ne 0 ]; then
		debug "[$INITIATORNAME_FILE] already exists with a different initiator id"
		debug "*If* it is safe to do so, you must manually delete this file in order"
		debug "to run this script"
		return 1
	    fi
	fi 
    fi

    { touch $INITIATORNAME_FILE; RETVAL=$?; } || true
    [ $RETVAL -ne 0 ] && debug_result 1 && return 1

    echo "InitiatorName=${IQN_INITIATOR_ID}" > $INITIATORNAME_FILE
    echo "InitiatorAlias=${MY_HOSTNAME}" >> $INITIATORNAME_FILE
    debug_result 0
    return 0
}

open_iscsi_start()
{
    debug_test "Starting open-iscsi"
    { service_op open-iscsi status; RETVAL=$?; } || true
    if [ $RETVAL -eq 0 ]; then
	debug_result 0
	return 0	
    fi

    { service_op open-iscsi start; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug_result 1
	return 1
    fi
    debug_result 0
    return 0
}

list_target_luns()
{
    ISCSI_RECIQN="$1"
      ls -1 /dev/iscsi/${ISCSI_RECIQN} | awk '/LUN/{sub(/LUN/,"");print}' | while read line;
	do
	echo -en "[$line] "
      done
      echo
}

discover_target()
{
    ISCSI_TARGET_IP="$1"

    debug "Discover iscsi target"
    test=0
    ${ISCSIADM} -m discovery -t st -p ${ISCSI_TARGET_IP} | while read line;
      do
      ISCSI_RECID=`echo $line | cut -d" " -f1`
      ISCSI_RECIQN=`echo $line | cut -d" " -f2`
      debug "#####################################"
      debug "# New Record:"
      debug "# ISCSI_RECID=${ISCSI_RECID} ISCSI_RECIQN=${ISCSI_RECIQN}"
      attached=0
      if [ ! -e  /dev/iscsi/${ISCSI_RECIQN} ]; then
	  attached=1
	  { attach_target ${ISCSI_RECID} ${ISCSI_RECIQN}; RETVAL=$?; } || true
	  if [ $RETVAL -ne 0 ]; then
	      debug "# Unable to attach target, exiting..."
	      return 1
	  fi
      fi
      debug "# LUNs available on target:"
      echo -en "# "
      ls -1 /dev/iscsi/${ISCSI_RECIQN} | awk '/LUN/{sub(/LUN/,"");print}' | while read line;
	do
	{ out=`echo $line | grep "_"` ; RETVAL=$?; } || true
	if [ $RETVAL -ne 0 ]; then
	    echo -en "[$line] "
	fi
      done
      echo

      [ $attached -gt 0 ] && detach_target ${ISCSI_RECID} ${ISCSI_RECIQN} 
     debug "######################################"
    done
    return 0
}

verify_target()
{
    ISCSI_RECID="$1"
    ISCSI_RECIQN="$2"

    { ${ISCSIADM} -m node -T $ISCSI_RECIQN -p $ISCSI_RECID >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	return $RETVAL
    fi
    return 0
}

#Args: recid, reciqn
attach_target()
{
    ISCSI_RECID="$1"
    ISCSI_RECIQN="$2"

    #debug_test "Attaching iscsi target"
    #Verify whether target + LUN already attached
    if verify_device ${ISCSI_RECIQN}; then
	#Already attached
	#debug_result 0
	return 0
    fi

    { ${ISCSIADM} -m node -T $ISCSI_RECIQN -p $ISCSI_RECID -l >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	#debug_result 1
	debug "Attach to target failed"
	return 1
    fi

    { iqn_wait $ISCSI_RECIQN; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	#debug_result 1
	debug "Unable to detect LUN device"
	return 1
    fi
    #debug_result 0
    return 0
}

#Args: recid, reciqn
detach_target()
{
    ISCSI_RECID="$1"
    ISCSI_RECIQN="$2"
    #debug_test "Detach iscsi target"
    { ${ISCSIADM} -m node -T $ISCSI_RECIQN -p $ISCSI_RECID -u >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	#debug_result 1
	return 1
    fi
    #debug_result 0
    return 0
}

#Args: recid, reciqn
delete_target()
{
    ISCSI_RECID="$1"
    ISCSI_RECIQN="$2"

    debug_test "Delete iscsi target"
    { ${ISCSIADM} -m node -T $ISCSI_RECIQN -p $ISCSI_RECID -o delete >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug_result 1
	return 1
    fi
    debug_result 0
    return 0
}

#Args: recid, reciqn
automate_login()
{
    ISCSI_RECID="$1"
    ISCSI_RECIQN="$2"

    debug_test "Automate iscsi login"
    { ${ISCSIADM} -m node -T $ISCSI_RECIQN -p $ISCSI_RECID --op update -n node.conn[0].startup -v automatic >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug_result 1
	return 1
    fi
    
    { ${ISCSIADM} -m node -T $ISCSI_RECIQN -p $ISCSI_RECID --op update -n node.startup -v automatic >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug_result 1
	return 1
    fi
    debug_result 0
    return 0
}

add_runlevel()
{
    debug_test "Add to runlevel"
    { chkconfig --add open-iscsi; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug_result 1
	return 1
    fi

    { chkconfig open-iscsi on; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug_result 1
	return 1
    fi
    debug_result 0
    return 0
}

####Main Function
#Args: recid, reciqn
iscsi_setup()
{
    ISCSI_RECID="$1"
    ISCSI_RECIQN="$2"

    debug && debug "Initialising iscsi disks"
    { verify_target ${ISCSI_RECID} ${ISCSI_RECIQN}; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug "Target entry does not exist. Run DiscoverIscsiTarget.sh to generate entry."
	return 1
    fi

    { attach_target ${ISCSI_RECID} ${ISCSI_RECIQN}; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug "attach_target failed"
	return 1
    fi
    
    { automate_login ${ISCSI_RECID} ${ISCSI_RECIQN}; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug "automate_login failed"
	return 1
    fi
    
    { add_runlevel; RETVAL=$?; } || true
    if [ $RETVAL -ne 0 ]; then
	debug "add_runlevel failed"
	return 1
    fi    
    return 0
}

#Args: Target IP address
iscsi_Discover()
{
    ISCSI_TARGET_IP="$1"

    { iqn_initialise; RETVAL=$?; } || true
    if [ $RETVAL -gt 0 ]; then
	debug "iqn_initialise failed"
	return 1
    fi

    { open_iscsi_start; RETVAL=$?; } || true
    if [ $RETVAL -gt 0 ]; then
	debug "open_iscsi_start failed"
	return 1
    fi
    
    { discover_target ${ISCSI_TARGET_IP}; RETVAL=$?; } || true
    if [ $RETVAL -gt 0 ]; then
	debug "discover_target failed"
	return 1
    fi

    return 0
}

iscsi_DEVICE_STRING()
{
    echo ${ISCSI_DEVICE_STRING}
}

iscsi_verify_SRexists()
{
    { output=`pvs --noheadings --separator : $1`; RETVAL=$?; } || true
    [ $RETVAL -ne 0 ] && return 1

    { echo $output | grep "VG_XenStorage-" >& /dev/null; RETVAL=$?; } || true
    if [ $RETVAL -gt 0 ]; then
	echo "No XenEnterprise SR found on iscsi device"
	return 1
    fi
    echo $output | cut -d: -f 2 | sed 's/VG_XenStorage-//'
    return 0
}

#Args: recid, reciqn
iscsi_disconnect()
{
    detach_target ${ISCSI_RECID} ${ISCSI_RECIQN}
    delete_target ${ISCSI_RECID} ${ISCSI_RECIQN}
}

