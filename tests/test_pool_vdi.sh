#!/bin/bash
## Set of SM tests for default SR

## Source the general system function file
. ./XE_api_library.sh

## source performance tests
. ./performance_functions.sh

ALPHA1=( a b c d e f g h )
ALPHA2=( i j k l m n o p )

init_host_devlist() {
    LOGFILE="/tmp/`date +%s-%N`"
    FREEFILE="${LOGFILE}-Freelist"
    INUSEFILE="${LOGFILE}-Usedlist"
    if [ -z ${TESTVAL} ]; then
	# Use all devices
        for i in ${ALPHA1[@]} ; do
	    smAddVdi ${SR_ID} xvda "TestVDI" ${VDI_SIZE}
            echo "xvd${i},${VDI_ID}" >> ${FREEFILE}
        done
        for i in ${ALPHA2[@]} ; do
	    smAddVdi ${SR_ID} xvda "TestVDI" ${VDI_SIZE}
            echo "xvd${i},${VDI_ID}" >> ${FREEFILE}
        done	
    elif [ ${TESTVAL} -gt 2 ]; then
        debug "Due to device limitations there can only be 2 instances of this test"
        exit
    elif [ ${TESTVAL} -eq 1 ]; then
        for i in ${ALPHA1[@]} ; do
	    smAddVdi ${SR_ID} xvda "TestVDI" ${VDI_SIZE}
            echo "xvd${i},${VDI_ID}" >> ${FREEFILE}
        done
    else
        for i in ${ALPHA2[@]} ; do
	    smAddVdi ${SR_ID} xvda "TestVDI" ${VDI_SIZE}
            echo "xvd${i},${VDI_ID}" >> ${FREEFILE}
        done
    fi
    touch ${INUSEFILE}
    debug "LOGFILES: ${FREEFILE} and ${INUSEFILE}"
}

setup_sr() 
{
    test_exit 0
    if [ -z ${SR_ID} ]; then
	smCreate "${SUBSTRATE_TYPE}" "${CONTENT_TYPE}" "${DEVSTRING}" \
            "${NFSSERVER}" "${NFSSERVERPATH}" \
            "${USE_IQN_INITIATOR_ID}" "${LISCSI_TARGET_IP}" "${LISCSI_TARGET_ID}" \
            "${LISCSI_ISCSI_ID}" "${NAPP_TARGET}" "${NAPP_USER}" "${NAPP_PASSWD}" \
	    "${NAPP_AGGR}" "${NAPP_FVOLS}" "${NAPP_SIZE}" "${EQL_TARGET}" \
	    "${EQL_USER}" "${EQL_PASSWD}" "${EQL_SPOOL}"
        test_exit 1

        CLEANUP_SR=1
    else
        smCheckSR ${SR_ID}
        test_exit 1
        CLEANUP_SR=0
    fi

    #Store the old default SR_ID
    smGetDefaultSR

    local OLD_SR_ID=$SR_DEFAULT_ID
    
    smSetDefaultSR ${SR_ID}

}

cleanup_sr()
{
    debug "Cleanup SR called"
    smSetDefaultSR ${OLD_SR_ID}
    if [ ${CLEANUP_SR} == 1 ]; then
        smDelete ${SR_ID}
        test_exit 0
        unset SR_ID
    fi
}

get_freedev() {
    DEVSIZE=`wc -l ${FREEFILE} | cut -d" " -f1`
    GLOBAL_RET=''
    if [ $DEVSIZE -eq 0 ]; then
        return
    fi
    GLOBAL_RET=`awk "NR==1" ${FREEFILE}`
    awk "NR!=1" ${FREEFILE} > ${FREEFILE}-tmp
    mv ${FREEFILE}-tmp ${FREEFILE}
    return
}

#Args: device, VDI
update_freelist() {
    echo "${1},${2}" >> ${FREEFILE}
}

vbdtest_exit() {
    if [ ${EXITVAL} -gt 0 -a $1 == 1 ]; then
        test_debug
        cleanupVBDs
        cleanup_sr
        exit
    fi
    test_exit $1
}

replug_vbd() {
    local VBD_ID=$1

    smUnplugVbd ${VBD_ID}
    vbdtest_exit 1
    smPlugVbd ${VBD_ID}
    vbdtest_exit 1

    sleep 1
}

gen_random_hostid() {
    rnd=$RANDOM
    let "rnd %= ${POOL_INDEX}"
    GLOBAL_RET=$rnd
    debug "Selecting host entry $rnd (${POOL_HOSTS[$rnd]})"
}

cleanupVBDs() {
    LOGSIZE=`wc -l ${INUSEFILE} | cut -d" " -f1`
    for i in `seq 1 $LOGSIZE` ; do
        VAL=`awk "NR==$i" ${INUSEFILE}`
        delDisk "$VAL"
    done

    LOGSIZE=`wc -l ${FREEFILE} | cut -d" " -f1`
    for i in `seq 1 $LOGSIZE` ; do
	VAL=`awk "NR==$i" ${FREEFILE}`
	VDI_UUID=`echo $VAL | cut -d, -f2`
	smDeleteVdi ${SR_ID} "autodetect" ${VDI_UUID}
    done
    rm -f ${FREEFILE}
    rm -f ${INUSEFILE}
    debug "VBD CLeanup complete"
}

addActiveDisk() {
    get_freedev  
    VBD_DEV=`echo ${GLOBAL_RET} | cut -d, -f1`
    VDI_ID=`echo ${GLOBAL_RET} | cut -d, -f2`
    debug "Using Free device ${VBD_DEV}"

    gen_random_hostid
    HOSTID=${POOL_VM[$GLOBAL_RET]}
    HOSTIDX=$GLOBAL_RET

    smCreateVbd ${VDI_ID} ${HOSTID}  ${VBD_DEV}
    vbdtest_exit 1

    smPlugVbd ${VBD_ID}
    vbdtest_exit 1

    GLOBAL_RET=`echo "${VDI_ID},${VBD_ID},${VBD_DEV}"`
    return
}


run_disktest() {
    DEVICE_ID=`echo $1 | cut -d, -f3`
    HOSTIP=${POOL_IPS[$HOSTIDX]}

    HOST_REM_CMD="ssh -q -oStrictHostKeyChecking=no -oUserKnownHostsFile=/dev/null -i ${SSH_PRIVATE_KEY} root@${HOSTIP}"

    run_mkfs "$HOST_REM_CMD" ${DEVICE_ID}
    vbdtest_exit 1

    run_fsck "$HOST_REM_CMD" ${DEVICE_ID}
    vbdtest_exit 1

    run_diskwipe "$HOST_REM_CMD" ${DEVICE_ID}
    vbdtest_exit 1
}

## Args: "VDI_ID VBD_ID"
delDisk() {
    VDI_ID=`echo $1 | cut -d, -f1`
    VBD_ID=`echo $1 | cut -d, -f2`
    debug "Deleting VBD, val $1"
    smUnplugVbd ${VBD_ID}

    smDeleteVbd ${VBD_ID}

    smDeleteVdi ${SR_ID} "autodetect" ${VDI_ID}

    return
}

## Args: "VDI_ID VBD_ID"
delActiveDisk() {
    VDI_ID=`echo $1 | cut -d, -f1`
    VBD_ID=`echo $1 | cut -d, -f2`
    smUnplugVbd ${VBD_ID}
    vbdtest_exit 1

    smDeleteVbd ${VBD_ID}
    vbdtest_exit 1

    return
}

run_dom0_vbdattach_tests() {
    DEVICE="xvda"
    DEV_NAME="/dev/${DEVICE}"
    BS=4K

    setup_sr

    init_host_devlist

    main_test_control

    cleanupVBDs

    cleanup_sr
}

main_test_control()
{
loop=0
while [ $loop -lt $MAXLOOP ]; do
    for i in `seq 1 $MAXSEQ` ;  do
        LOGSIZE=`wc -l ${INUSEFILE} | cut -d" " -f1`
        let "NUM = $RANDOM % 9"
        if [ $NUM == 0 ]; then
            if [ $LOGSIZE == 0 ]; then
                continue
            fi
            rnd=0
            while [ $rnd -eq 0 ]; do
                rnd=$RANDOM
                let "rnd %= ${LOGSIZE}+1"
            done
            VAL=`awk "NR==$rnd" ${INUSEFILE}`
            debug "Deleting VBD/VDI [$VAL]"
            awk "NR!=$rnd" ${INUSEFILE} > ${INUSEFILE}-tmp
            delActiveDisk "$VAL"
	    mv ${INUSEFILE}-tmp ${INUSEFILE}
	    update_freelist `echo $VAL | cut -d, -f3` `echo $VAL | cut -d, -f1`
        else
            if [ $LOGSIZE -eq $MAXSEQ ]; then
                continue
            fi
            addActiveDisk
	    echo "${GLOBAL_RET}" >> ${INUSEFILE}
	    debug "CREATED [${GLOBAL_RET}]"
	    run_disktest "${GLOBAL_RET}" $HOSTIDX
        fi
    done
    for i in `seq 1 $MAXSEQ` ;  do
        LOGSIZE=`wc -l ${INUSEFILE}| cut -d" " -f1`
        let "NUM = $RANDOM % 9"
        if [ $NUM != 0 ]; then
            if [ $LOGSIZE == 0 ]; then
                continue
            fi
            rnd=0
            while [ $rnd -eq 0 ]; do
                rnd=$RANDOM
                let "rnd %= ${LOGSIZE}+1"
            done
            VAL=`awk "NR==$rnd" ${INUSEFILE}`
            debug "Deleting VBD/VDI [$VAL]"
            awk "NR!=$rnd" ${INUSEFILE} > ${INUSEFILE}-tmp
            delActiveDisk "$VAL"
	    mv ${INUSEFILE}-tmp ${INUSEFILE}
	    update_freelist `echo $VAL | cut -d, -f3` `echo $VAL | cut -d, -f1`
        else
            if [ $LOGSIZE -eq $MAXSEQ ]; then
                continue
            fi
            addActiveDisk
	    echo "${GLOBAL_RET}" >> ${INUSEFILE}
	    debug "CREATED [${GLOBAL_RET}]"
	    run_disktest "${GLOBAL_RET}" $HOSTIDX
        fi
    done
    loop=`expr $loop + 1`
done
}


run_tests() {
    if [ -z $1 ] ; then
        debug "Please supply a test type (lvmoiscsi,nfs,netapp,lvmohba,equal)"
        return 1
    fi

    if [[ $1 = "lvmoiscsi"  || $1 = "lvmoiscsi_chap" ]] ; then
        # test lvm over iscsi
        DRIVER_TYPE=lvmoiscsi    
        SUBSTRATE_TYPE=lvmoiscsi
        CONTENT_TYPE=user
        VDI_SIZE=$((10*1024*1024))
        if [ $1 = "lvmoiscsi" ] ; then
            USE_IQN_INITIATOR_ID="${IQN_INITIATOR_ID}"
        else
            USE_IQN_INITIATOR_ID="${IQN_INITIATOR_ID_CHAP}"
            USE_CHAP=1
        fi
    elif [ $1 = "nfs" ] ; then
        #test file
        DRIVER_TYPE=nfs
        SUBSTRATE_TYPE=nfs
        CONTENT_TYPE=nfs
        VDI_SIZE=$((10*1024*1024))
    elif [[ $1 = "netapp"  || $1 = "netapp_chap" ]] ; then
        # test the netapp driver
        DRIVER_TYPE=netapp   
        SUBSTRATE_TYPE=netapp
        CONTENT_TYPE=user
        VDI_SIZE=$((10*1024*1024))
        if [ $1 = "netapp" ] ; then
            USE_IQN_INITIATOR_ID="${IQN_INITIATOR_ID}"
        else
            USE_IQN_INITIATOR_ID="${IQN_INITIATOR_ID_CHAP}"
            USE_CHAP=1
        fi
    elif [ $1 = "equal" ] ; then
        # test the EqualLogic driver
        DRIVER_TYPE=equal   
        SUBSTRATE_TYPE=equal
        CONTENT_TYPE=user
        VDI_SIZE=$((10*1024*1024*1024))
        USE_IQN_INITIATOR_ID="${IQN_INITIATOR_ID}"
    elif [ $1 = "lvmohba" ] ; then
        # test the netapp driver
        DRIVER_TYPE=lvmohba   
        SUBSTRATE_TYPE=lvmohba
        CONTENT_TYPE=user
        VDI_SIZE=$((10*1024*1024))
    else
        debug "Unknown test type: $1.
I only know about lvmoiscsi, nfs, equal, netapp or lvmohba"
        return 1
    fi
 
    debug ""
    debug "Running tests on substrate type:         ${SUBSTRATE_TYPE}"
    debug "                    driver type:         ${DRIVER_TYPE}"
    debug ""

    run_dom0_vbdattach_tests
}

TEMPLATE_ALIAS=windows


process_arguments $@

post_process

check_req_args

check_req_sw

install_ssh_certificate ${REMHOSTNAME} ${SSH_PRIVATE_KEY} ${PASSWD}

print_version_info

gen_hostlist

for i in `seq 1 ${POOL_INDEX}`; do
    IDX=`expr $i - 1`
    HOSTIP=${POOL_IPS[$IDX]}
    debug "Inserting password ${PASSWD} into host ${POOL_IPS[$IDX]}"
    install_ssh_certificate ${POOL_IPS[$IDX]} ${SSH_PRIVATE_KEY} ${PASSWD}
done

# Set default test params
if [ -z ${MAXSEQ} ]; then
    MAXSEQ=8  
fi

if [ -z ${MAXLOOP} ]; then
    MAXLOOP=100  
fi

if [[ -z ${NFSSERVER}  || -z ${NFSSERVERPATH} ]] ; then
    debug "No NFS information specified. Skip NFS tests"
else
    debug ""
    run_tests "nfs"
fi

if [[ -z ${IQN_INITIATOR_ID} || -z ${LISCSI_TARGET_IP} || -z ${LISCSI_TARGET_ID} || -z ${LISCSI_ISCSI_ID} ]]; then
    debug "iSCSI configuration information missing. Skip iSCSI tests"
else
    debug ""
    run_tests "lvmoiscsi"

    if [ ! -z ${IQN_INITIATOR_ID_CHAP} ] ; then
        run_tests "lvmoiscsi-chap"
    fi
fi

if [[ -z ${NAPP_TARGET} || -z ${NAPP_USER} || -z ${NAPP_PASSWD} || -z ${NAPP_AGGR} || -z ${NAPP_SIZE} ]]; then
    debug "Netapp configuration information missing. Skip netapp SR tests"
else
    if [ -z ${NAPP_FVOLS} ]; then
        NAPP_FVOLS=8
    fi
    debug ""
    run_tests "netapp"

    if [ ! -z ${IQN_INITIATOR_ID_CHAP} ] ; then
        debug ""
        run_tests "netapp_chap"
    fi
fi

if [[ -z ${EQL_TARGET} || -z ${EQL_USER} || -z ${EQL_PASSWD} || -z ${EQL_SPOOL} ]]; then
    debug "EqualLogic configuration information missing. Skip equal SR tests"
else
    echo ""
    run_tests "equal"
fi

if [ -z ${SHARED_HBA} ]; then
    debug "LVM over HBA information missing, skipping tests"
else
    debug ""
    run_tests "lvmohba"
fi

print_exit_info

