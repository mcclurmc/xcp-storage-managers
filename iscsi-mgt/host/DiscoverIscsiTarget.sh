#!/bin/bash
## Discover iscsi target entry
set -e

## Source the general system function file
. XE_ops.sh


#Process the arguments
until [ -z "$1" ]
do
  process_args $1
  shift
done

if [ -z ${IQN_INITIATOR_ID} ]; then
    echo "IQN_INITIATOR_ID required"
    usage
fi

if [ -z ${ISCSI_TARGET_IP} ]; then
    echo "ISCSI_TARGET_IP required"
    usage
fi

DRIVER_TYPE=lvm
SUBSTRATE_TYPE=iscsi

${SUBSTRATE_TYPE}_Discover ${ISCSI_TARGET_IP}

