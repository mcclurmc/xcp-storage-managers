#!/bin/sh

# Some functions written by Steffen Plotner, 2005
# http://iscsitarget.sourceforge.net/wiki/index.php/Easy_management

BASE_IQN='iqn.2007-10.com.xensource.uk'
VOLGROUP='VolGroup00'
IETD_CONFIG='/etc/ietd.conf'
IETD_ALLOW='/etc/initiators.allow'
STATIC_CONFIG_DIR='/etc/ietd_configs'

function usage
{
    echo "usage: iscsi-conf.sh <initiator host> <LUN size (GiB)>"
}
# Test number of args
# Args: iscsi-conf.sh <initiator host> <LUN size> 
if [ $# != 2 ]; then
    usage
    exit 1
fi

function get_next_lunid
{
    local prefix
    prefix==$1

    lines=`lvs $VOLGROUP | grep $1 | wc -l`
    if [ $lines == 0 ]; then
	echo "0"
	return 0
    fi

    highest=$(lvs $VOLGROUP | \
	grep $1 | \
	sed 's/^\s*//' | \
	cut -d" " -f1 | \
	cut -d"_" -f2 | \
	sort -n -r | \
	head -n 1)

    local new_lun_id
    new_lun_id=$(($highest + 1))
    echo "$new_lun_id"
    return 0
}

function get_scsi_id
{
    echo `uuidgen | cut -f1 -d-`
    return 0
}

function get_tid_from_target
{
        local target_name
        target_name=$1

        if [ -z "$target_name" ]; then
                echo "0"
                return 1
        fi

        local line
        line=$(cat /proc/net/iet/session | grep "name:$target_name$")
        if [ -z "$line" ]; then
                echo "0"
        else
                # tid:1 name:test
                echo $line | cut -f1 -d' ' | cut -f2 -d:
        fi
        return 0
}

function get_next_tid
{
        local last_tid
        last_tid=$(cat /proc/net/iet/session | \
                grep "^tid:" | \
                cut -f1 -d' ' | \
                cut -f2 -d: | \
                sort -n -r | \
                head -n 1)
        local new_tid
        new_tid=$(($last_tid + 1))
        echo "$new_tid"
        return 0
}

function create_LUN 
{
    local name
    local size

    name=$1
    size=$2

    cmd="lvcreate -L$size -n$name $VOLGROUP"
    `$cmd >& /dev/null`
    if [ $? != 0 ]; then
	echo "Unable to create LV (name=$name,size=$size) [$cmd]"
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

function update_config
{
    local target
    local lunid
    local lun
    local path

    target=$1
    lunid=$2
    lun=$3
    path="/dev/$VOLGROUP/$lun"

    #Check whether to update running ietd
    if [ $status == 1 ]; then
	#Check for tid
	tid=$(get_tid_from_target "$target")
	if [ "$tid" = 0 ]; then
	    tid=$(get_next_tid)
	    cmd="ietadm --op new --tid=$tid --params Name=$target"
	    $cmd
	    if [ $? != 0 ]; then
		echo "Unable to create new tid ($cmd)"
		exit 1
	    fi

	fi
	new_disk_id=$(get_scsi_id)
	cmd="ietadm --op new --tid=$tid --lun=$lunid --params=Path=$path,Type=fileio,ScsiId=${new_disk_id}"
	$cmd
	if [ $? != 0 ]; then
	    echo "Unable to insert new LUN into tid ($cmd)"
	    exit 1
	fi
    fi

    # Update static config
    if [ ! -e "${STATIC_CONFIG_DIR}/${target}" ]; then
	# Create new config
	echo "Target ${target}" > ${STATIC_CONFIG_DIR}/${target}
    fi
    echo "        Lun ${lunid} Path=${path},Type=fileio,ScsiId=${new_disk_id}" >> ${STATIC_CONFIG_DIR}/${target}
    flush_configs
}

function update_accesslist
{
    local target
    local addr

    target=$1
    addr=$2

    # Check to see whether an access entry already exists
    cmd="grep ${addr} ${IETD_ALLOW}"
    `$cmd >& /dev/null`
    if [ $? != 0 ]; then
	echo "${target} ${addr}" >> ${IETD_ALLOW}
    fi
}


# Test to see whether ietd is running
status=1
if [ ! -e /proc/net/iet/session -o ! -e /proc/net/iet/volume ]; then
    status=0
fi

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

TARGET_IQN=`echo $BASE_IQN:$IP`
LUNID=$(get_next_lunid $IP)
LUN_NAME=`echo ${IP}_${LUNID}`
SIZE_ARG="$2G"

create_LUN $LUN_NAME $SIZE_ARG

update_config $TARGET_IQN $LUNID $LUN_NAME

update_accesslist $TARGET_IQN $IP


