#!/bin/sh

BASE_IQN='iqn.2007-10.com.xensource.uk'
VOLGROUP='VolGroup00'
IETD_CONFIG='/etc/ietd.conf'
IETD_ALLOW='/etc/initiators.allow'
STATIC_CONFIG_DIR='/etc/ietd_configs'
ARGS=0

function usage
{
    echo "usage: iscsi-remove.sh <initiator host> [LUN id]"
}
# Test number of args
# Args: iscsi-conf.sh <initiator host> <LUN size> 
if [ $# != 1 -a  $# != 2 ]; then
    echo "$# args"
    usage
    exit 1
else
    ARGS=$#
    if [ $# == 2 ]; then
	LUNID=$2
    fi
fi


function delete_LUN 
{
    local name
    local lun

    name=$1
    lun=$2

    cmd="lvremove -f /dev/${VOLGROUP}/${name}_${lun}"
    `$cmd >& /dev/null`
    if [ $? != 0 ]; then
	echo "Unable to remove LV (name=${name}_${lun}) [$cmd]"
	exit 1
    fi
    return 0
}

function delete_LUNs
{
    local name

    name=$1

    cmd="lvremove -f /dev/${VOLGROUP}/${name}*"
    `$cmd >& /dev/null`
    if [ $? != 0 ]; then
	echo "Unable to remove LVs (name=$name*) [$cmd]"
	exit 1
    fi
    return 0
}

function flush_configs
{
    rm -f $IETD_CONFIG
    for val in `ls ${STATIC_CONFIG_DIR}`; do
	cat "${STATIC_CONFIG_DIR}/${val}" >> $IETD_CONFIG
    done
}

function restart_service
{
    service iscsi-target restart
}

function update_accesslist
{
    local addr

    addr=$1
    tmpfile=`uuidgen`

    cat ${IETD_ALLOW} | while read line;
    do
      ret=`echo "$line" | grep "${addr}"`
      if [ $? != 0 ]; then
	  echo "$line" >> /tmp/${tmpfile}
      fi
    done
    mv /tmp/${tmpfile} ${IETD_ALLOW}
}

function update_hostconfig
{
    local host
    local lun
    
    host=$1
    lun=$2
    tmpfile=`uuidgen`
    target="${BASE_IQN}:${host}"
    filename="${STATIC_CONFIG_DIR}/${target}"

    echo "Target ${target}" >> /tmp/${tmpfile}
    cat ${filename} | while read line;
    do
      ret=`echo "$line" | grep "Lun ${lun}"`
      if [ $? != 0 ]; then
	  ret=`echo "$line" | grep "Target"`
	  if [ $? != 0 ]; then
	      echo "    $line" >> /tmp/${tmpfile}
	  fi
      fi
    done
    mv /tmp/${tmpfile} ${filename}
}

#Extract the IP address of the initiator
val=`host $1`
if [ $? != 0 ]; then
    echo "Unable to resolve host $1"
    exit 1
fi

IP=`echo $val | cut -d " " -f4`
if [ $IP == 'pointer' ]; then
    IP=$1
fi

if [ ${ARGS} == 1 ]; then
    # Remove the whole entry
    rm -f ${STATIC_CONFIG_DIR}/*${IP}
    flush_configs
    update_accesslist ${IP}
    restart_service
    delete_LUNs ${IP}
else
    # Remove a single entry
    update_hostconfig ${IP} ${LUNID}
    flush_configs
    restart_service
    delete_LUN ${IP} ${LUNID}
fi



