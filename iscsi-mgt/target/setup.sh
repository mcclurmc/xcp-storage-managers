#!/bin/sh

mkdir -p /etc/ietd_configs
cp header.conf /etc/ietd_configs/aaaaaaaaaa-header
chmod +x iscsi-*.sh
cp iscsi-*.sh /usr/sbin/.
echo "ALL ALL" > /etc/initiators.deny
/usr/sbin/iscsi-flush.sh
